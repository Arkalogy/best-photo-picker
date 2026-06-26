"""Structured logging for bpp.

All log records pass through `RedactingFormatter`, which strips share /
auth-token values out of URLs and `X-Auth-Token` headers before they
reach stderr, server.log, or the in-memory ring buffer (which the
Activity tab and `/api/v1/logs` read from).

Why: the LAN share token is a long-lived secret that's embedded in
share-URL query strings (`?_token=...`). Without redaction it ended
up in:
  * server.log (rotates at 5MB x 3 -> persists for weeks; one cloud
    backup or shared Mac exfiltrates it)
  * the startup banner (any tail / pastebin / support bundle)
  * the Activity tab response payload (renderable as plain text)

Redaction is best-effort defense in depth — the canonical place to
hand out the URL is the owner-only Settings → Share UI, which calls
`/api/v1/share/info` directly and bypasses the log path entirely.
"""

from __future__ import annotations

import collections
import logging
import re
import sys

_CONFIGURED = False
_LOG_FMT = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
_LOG_DATEFMT = "%H:%M:%S"

# Patterns that match common token-bearing shapes in log output.
# Order matters — header form is checked before generic ?param= so a
# header with `=` doesn't get partially redacted.
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # X-Auth-Token: <hex> or x-auth-token: <hex>
    (re.compile(r"(X-Auth-Token:\s*)([A-Za-z0-9+/=._-]+)", re.IGNORECASE), r"\1[REDACTED]"),
    # Authorization: Bearer <token> / Authorization: Basic <b64>
    # Belt-and-suspenders: bpp doesn't issue these today, but
    # contributed reverse-proxy configs, OAuth integrations, or
    # third-party tooling can attach `Authorization` headers that
    # werkzeug or a custom debug log might emit verbatim. The two
    # standard schemes both put the secret immediately after the
    # scheme keyword, so one regex covers Bearer + Basic + any
    # similarly-shaped scheme. `[A-Za-z]+` for the scheme avoids
    # matching `Authorization-Required:` style header names.
    (
        re.compile(
            r"(Authorization:\s*[A-Za-z]+\s+)([A-Za-z0-9+/=._\-]+)",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]",
    ),
    # ?_token=<value> or &_token=<value> in URLs / log lines
    (re.compile(r"([?&]_token=)([^\s\"'&#]+)"), r"\1[REDACTED]"),
    # *_auth_token / *_share_token / *_app_token bare key=value or
    # key: value (YAML / Python dict-repr / debug dumps).
    # Optional prefix `\w+_` catches `lan_share_token`,
    # `legacy_app_token`, etc. — the actual DB key shape that the
    # The original word-boundary anchor missed traceback-local assignments.
    # The middle `['\"]?\s*[=:]\s*['\"]?` admits Python dict-repr
    # form where the key is quoted: `'lan_share_token': 'value'`.
    # Trailing terminator excludes whitespace, quotes, ampersands,
    # commas (dict-repr separator), `}` (dict close) and `#` so the
    # match doesn't eat past the value.
    (
        re.compile(
            r"((?:\w+_)?(?:auth|share|app)_token['\"]?\s*[=:]\s*['\"]?)"
            r"([^\s,}#'\"&]+)"
        ),
        r"\1[REDACTED]",
    ),
    # Bare `token` / `secret` assignments with a token-shaped value
    # When a subprocess crashes inside scoring /
    # face / clip workers, `traceback.format_exc()` may include
    # local-variable frames where a variable named `token` (or
    # `secret`) holds a 256-bit hex / base64url string — covered by
    # the longer `*_token` pattern only when the variable name had
    # an `auth_` / `share_` / `app_` prefix. The `\b` ensures we
    # don't double-match inside `auth_token` (already redacted by
    # the rule above). The value-shape gate (32+ chars from the
    # token alphabet) prevents false positives on prose like
    # `token = "yes"`.
    (
        re.compile(
            r"\b(token['\"]?\s*[=:]\s*['\"]?)([A-Za-z0-9+/=_-]{32,})",
        ),
        r"\1[REDACTED]",
    ),
    # anonymize home-directory paths. Backup, library, and
    # workdir paths log absolute filesystem locations like
    # `/Users/alice/Pictures/BestPhotoPicker/...` — not credential-
    # grade, but a privacy-grade leak when logs end up in support
    # bundles, screencasts, or pasted into a public bug tracker.
    # Replace the username segment with `~` so the remainder of the
    # path stays useful for diagnosis. Covers macOS `/Users/<name>/`
    # and Linux `/home/<name>/`. Idempotent: a path already starting
    # with `~/` won't match.
    (
        re.compile(r"(?<![/A-Za-z0-9._-])/Users/[^/\s'\"]+/"),
        "~/",
    ),
    (
        re.compile(r"(?<![/A-Za-z0-9._-])/home/[^/\s'\"]+/"),
        "~/",
    ),
)


def redact_secrets(text: str) -> str:
    """Strip token values out of *text*.

    Idempotent — running it twice on already-redacted output is a
    no-op. Used by `RedactingFormatter` and exported for callers that
    need to scrub a string before stuffing it into a log message
    template (e.g. constructing a banner)."""
    if not text:
        return text
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    """Formatter that runs the rendered message through `redact_secrets`."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


# Regex to parse log lines written by _LOG_FMT
_LOG_LINE_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+\[(\w+)\s*\]\s+([\w.]+):\s+(.*)$")

RING_BUFFER_SIZE = 1000


class InMemoryHandler(logging.Handler):
    """Keeps the last *capacity* log records in a deque for the UI."""

    # Loggers that emit chatty per-request output (raw HTTP access lines
    # with ANSI escape codes, full URLs with tokens, 304 noise, etc.).
    # We still want them on stderr + the rotating log file for ops
    # diagnostics, but not in the user-facing Activity feed where the
    # signal-to-noise ratio drops to near zero.
    _UI_NOISE_LOGGERS = ("werkzeug",)

    def __init__(self, capacity: int = RING_BUFFER_SIZE) -> None:
        super().__init__()
        self.buffer: collections.deque[dict] = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        # Suppress per-request noise from chatty loggers in the UI feed,
        # but let ERROR / CRITICAL through — an unhandled exception in
        # request handling is exactly the kind of thing the operator
        # needs to see in Activity, not just buried in server.log.
        if record.name.startswith(self._UI_NOISE_LOGGERS) and record.levelno < logging.ERROR:
            return
        # Bug #11 (UAT 2026-06-01): the activity-log dropdown surfaces
        # the formatted message verbatim. Python's standard formatter
        # appends the traceback to the message when record.exc_info is
        # set — dumping 50+ lines of urllib internals into the user-
        # facing Activity feed reads as 'app is broken' when it's just
        # 'check failed, retrying.' Format without exc_info here; the
        # full traceback still reaches server.log via the file handler
        # because the source log call retains exc_info=True there.
        if record.exc_info or record.exc_text:
            ui_record = logging.makeLogRecord(record.__dict__)
            ui_record.exc_info = None
            ui_record.exc_text = None
            ui_record.stack_info = None
            formatted = self.format(ui_record)
        else:
            formatted = self.format(record)
        self.buffer.append(
            {
                "ts": record.created,
                "level": record.levelname,
                "module": record.name,
                "msg": formatted,
            }
        )

    def get_entries(
        self,
        since: float | None = None,
        level: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Return entries filtered by *since* timestamp and minimum *level*."""
        min_level = getattr(logging, level.upper(), 0) if level else 0
        out: list[dict] = []
        # Snapshot the deque to avoid "deque mutated during iteration"
        # when background threads append entries while we're reading.
        # list() takes the deque lock atomically in CPython.
        for e in list(self.buffer):
            if since and e["ts"] <= since:
                continue
            if min_level and logging.getLevelName(e["level"]) < min_level:
                continue
            out.append(e)
        return out[-limit:]

    def clear(self) -> None:
        self.buffer.clear()

    def preload_from_file(self, log_path: str, max_lines: int = 500) -> None:
        """Parse existing server.log and pre-populate the buffer.

        Each preloaded line is run through `redact_secrets` BEFORE
        storing — `RedactingFormatter` only protects new log emits,
        and old server.log files (rotated 5MB x 3, weeks of retention)
        will contain pre-redaction tokens that would otherwise resurface
        verbatim in `/api/v1/logs` and the Activity tab on next startup.
        """
        import os

        if not os.path.isfile(log_path):
            return
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            # Take the tail
            for line in lines[-max_lines:]:
                # Redact BEFORE the regex match so the parsed level /
                # module fields can't accidentally pull a token off a
                # malformed line. Idempotent on already-redacted lines.
                line = redact_secrets(line.rstrip("\n"))
                m = _LOG_LINE_RE.match(line)
                if not m:
                    continue
                module = m.group(3)
                level_str = m.group(2).strip()
                # Same filter as emit(): suppress chatty loggers below
                # ERROR but let ERROR / CRITICAL resurface from disk.
                if module.startswith(self._UI_NOISE_LOGGERS) and level_str not in (
                    "ERROR",
                    "CRITICAL",
                ):
                    continue
                self.buffer.append(
                    {
                        "ts": 0,  # no date in HH:MM:SS format; 0 = historical
                        "level": level_str,
                        "module": module,
                        "msg": line,
                    }
                )
        except OSError:
            pass


# Singleton — created once, shared across the app
_memory_handler: InMemoryHandler | None = None


def get_memory_handler() -> InMemoryHandler | None:
    return _memory_handler


def setup_logging(debug: bool = False) -> None:
    global _CONFIGURED, _memory_handler
    if _CONFIGURED:
        return
    level = logging.DEBUG if debug else logging.INFO
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(RedactingFormatter(_LOG_FMT, datefmt=_LOG_DATEFMT))

    # attach redacting handlers to the ROOT logger so any
    # third-party plugin emitting through its own namespace (e.g.
    # `logging.getLogger("my_plugin")`) gets the same scrub before
    # its records reach stderr / the ring buffer. Pre-fix, only
    # `bpp.*` and `werkzeug` were wired through `RedactingFormatter`;
    # a plugin's own logger bypassed it, and a careless plugin
    # author could leak a token-shaped value into the unredacted
    # output stream.
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)

    # Attach in-memory ring buffer (Activity tab + /api/logs read this)
    _memory_handler = InMemoryHandler()
    _memory_handler.setFormatter(RedactingFormatter(_LOG_FMT, datefmt=_LOG_DATEFMT))
    root_logger.addHandler(_memory_handler)

    # Keep the explicit `bpp` namespace level so contributors can
    # bump it without touching root, but route emissions to root's
    # handlers (above) — `propagate=True` is the default and we
    # need it for the bpp records to hit the redacting handlers.
    bpp_logger = logging.getLogger("bpp")
    bpp_logger.setLevel(level)

    # Werkzeug's WSGIRequestHandler.log() writes raw URLs (with
    # ?_token=… query strings) to the `werkzeug` logger, which is
    # OUTSIDE our `bpp` namespace. With root-level handlers it
    # propagates correctly, but we keep the explicit setLevel so
    # the chatty access log isn't silenced by a future root-level
    # downgrade.
    werkzeug = logging.getLogger("werkzeug")
    werkzeug.setLevel(level)

    _CONFIGURED = True


_SCRUB_SENTINEL = ".scrubbed-v1"


def _scrub_historical_logs_in_place(log_path: str) -> None:
    """rewrite rotated server.log files to redact tokens that
    were emitted before `RedactingFormatter` was introduced.

    `preload_from_file()` (called below) already scrubs lines as
    they're loaded into the ring buffer, but the *file bytes* on disk
    remain cleartext. Anything that copies the log dir — backup tool,
    cloud sync, support bundle, screenshot of the file in a file
    browser — exposes the live LAN share token verbatim. The token
    is per-library and persistent, so a single leaked file plus
    knowledge of the user's LAN address compromises sharing for the
    library's lifetime.

    This is a one-shot, idempotent rewrite gated by a sentinel file
    (`<log_dir>/.scrubbed-v1`). Skips if already scrubbed. Atomic via
    tmp+replace in the same directory so a power loss mid-rewrite
    leaves either the original or the scrubbed file, never a half-
    written one.

    Runs BEFORE `RotatingFileHandler` opens the live log so we don't
    pull the inode out from under an open writer.
    """
    import os

    log_dir = os.path.dirname(log_path)
    if not log_dir or not os.path.isdir(log_dir):
        return
    sentinel = os.path.join(log_dir, _SCRUB_SENTINEL)
    if os.path.exists(sentinel):
        return

    # Targets: live log + rotated siblings (RotatingFileHandler uses
    # `.1`, `.2`, `.3` per `LOG_BACKUP_COUNT`). Glob conservatively to
    # also pick up any non-default rotation suffix or external rotator.
    import glob as _glob

    candidates = [log_path, *sorted(_glob.glob(log_path + ".*"))]

    import contextlib

    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                original = f.read()
        except OSError:
            continue
        scrubbed = redact_secrets(original)
        if scrubbed == original:
            continue  # no tokens present; skip the rewrite
        # Atomic in-place rewrite: write tmp in same dir, replace
        tmp_path = path + ".scrub.tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(scrubbed)
            os.replace(tmp_path, path)
        except OSError:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            continue

    # Sentinel written even if no files were rewritten — the scrub
    # has run and the result is "nothing to scrub". Idempotent.
    try:
        with open(sentinel, "w", encoding="utf-8") as f:
            f.write("v1: historical log scrub completed\n")
    except OSError:
        pass


def add_file_handler(log_path: str) -> None:
    """Add a rotating file handler to the bpp logger."""
    import os
    from logging.handlers import RotatingFileHandler

    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    # scrub any historical (pre-redaction) tokens out of
    # existing server.log files BEFORE RotatingFileHandler opens the
    # live log for append. Idempotent via .scrubbed-v1 sentinel.
    _scrub_historical_logs_in_place(log_path)

    from bpp.constants import LOG_BACKUP_COUNT, LOG_MAX_BYTES

    handler = RotatingFileHandler(log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT)
    handler.setFormatter(RedactingFormatter(_LOG_FMT, datefmt=_LOG_DATEFMT))
    # attach to the root logger so plugin / third-party
    # records (which propagate to root) also land in the rotating
    # server.log via the redacting formatter. setup_logging() now
    # configures root rather than `bpp.*` directly, so this
    # mirrors that — bpp.* and werkzeug records still hit the
    # handler via propagation.
    logging.getLogger().addHandler(handler)

    # Pre-populate ring buffer from existing log file
    if _memory_handler is not None:
        _memory_handler.preload_from_file(log_path)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
