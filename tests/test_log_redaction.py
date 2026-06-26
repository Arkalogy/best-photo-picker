"""Token redaction in logs — regression guard.

The LAN share token is a long-lived secret that lives in URL query
strings (`?_token=...`). Without redaction it ended up in
server.log, the startup banner, and the Activity tab response.
Anyone who could read server.log (cloud backup, support bundle,
shared workstation) could exfiltrate it.

These tests cover the central redaction helper, the formatter
applied by `setup_logging` / `add_file_handler`, and downstream
consumers (memory ring buffer / `/api/v1/logs`).
"""

from __future__ import annotations

import io
import logging

from bpp.utils.logging import (
    InMemoryHandler,
    RedactingFormatter,
    redact_secrets,
)

# A representative 64-hex token (matches secrets.token_hex(32) shape)
_TOKEN = "0123456789abcdef" * 4


# ─── redact_secrets() unit ────────────────────────────────────────


class TestRedactSecrets:
    def test_redacts_token_in_url_query(self):
        out = redact_secrets(f"GET /api/photos?_token={_TOKEN} HTTP/1.1")
        assert _TOKEN not in out
        assert "[REDACTED]" in out

    def test_redacts_token_in_share_url(self):
        out = redact_secrets(f"http://192.168.1.5:5001/?_token={_TOKEN}")
        assert _TOKEN not in out

    def test_redacts_x_auth_token_header(self):
        out = redact_secrets(f"X-Auth-Token: {_TOKEN}")
        assert _TOKEN not in out
        assert "[REDACTED]" in out

    def test_redacts_x_auth_token_lowercase(self):
        out = redact_secrets(f"x-auth-token: {_TOKEN}")
        assert _TOKEN not in out

    def test_redacts_auth_token_kv(self):
        out = redact_secrets(f"auth_token={_TOKEN} foo=bar")
        assert _TOKEN not in out
        assert "foo=bar" in out

    def test_redacts_share_token_kv(self):
        out = redact_secrets(f"share_token={_TOKEN}")
        assert _TOKEN not in out

    def test_redacts_app_token_kv(self):
        """Pin the bare `app_token=` form explicitly. Covered today by
        the `*_token` family regex via the `arbitrary_prefix` test, but
        easy to lose on a future regex tightening that requires a
        non-empty prefix. R12 audit (Secrets F2)."""
        out = redact_secrets(f"app_token={_TOKEN}")
        assert _TOKEN not in out
        assert "[REDACTED]" in out

    def test_redacts_lan_share_token_kv(self):
        """The actual DB key is `lan_share_token`. A word-boundary
        regex would not match the underscore-prefixed form, so the
        redactor must allow leading underscores."""
        out = redact_secrets(f"lan_share_token={_TOKEN}")
        assert _TOKEN not in out
        assert "[REDACTED]" in out

    def test_redacts_dict_repr_form(self):
        """Python dict repr / YAML uses `'key': 'value'`. Cover the
        `key: value` shape too."""
        out = redact_secrets(f"settings = {{'lan_share_token': '{_TOKEN}'}}")
        assert _TOKEN not in out

    def test_redacts_arbitrary_prefix(self):
        """Future code that stores tokens under names like
        `legacy_app_token` or `device_auth_token` is covered too."""
        for key in ("legacy_app_token", "device_auth_token", "rotated_share_token"):
            out = redact_secrets(f"{key}={_TOKEN}")
            assert _TOKEN not in out, f"failed for key {key!r}"

    def test_does_not_redact_unrelated_underscore_words(self):
        """Don't false-positive on benign identifiers that happen to
        contain `auth` / `share` / `app` as substrings somewhere."""
        # These shouldn't trip — they don't end in _token
        for benign in (
            "share_url=http://h:5001",
            "auth_method=oauth",
            "app_version=1.2.3",
        ):
            assert redact_secrets(benign) == benign, f"false-positive on {benign!r}"

    def test_idempotent(self):
        once = redact_secrets(f"?_token={_TOKEN}")
        twice = redact_secrets(once)
        assert once == twice

    def test_does_not_break_non_secret_text(self):
        text = "import worker started: 100 photos"
        assert redact_secrets(text) == text

    def test_handles_empty_string(self):
        assert redact_secrets("") == ""

    def test_redacts_multiple_in_one_string(self):
        text = f"req=?_token={_TOKEN} hdr=X-Auth-Token: {_TOKEN}"
        out = redact_secrets(text)
        assert _TOKEN not in out
        assert out.count("[REDACTED]") == 2

    def test_redacts_bare_token_assignment_in_traceback_locals(self):
        """R9-fr-secrets-H1: when a subprocess crashes, the parent
        re-emits `traceback.format_exc()` which can include a frame's
        local variables (Python ≥3.11 in some configurations). A bare
        `token = "<64hex>"` line was NOT matched by the `*_token=`
        pattern because the variable name lacks an `auth/share/app_`
        prefix. Add coverage for that shape."""
        traceback_locals = (
            'File "share.py", line 41, in get_share_token\n'
            "    return token\n"
            f'  token = "{_TOKEN}"\n'
        )
        out = redact_secrets(traceback_locals)
        assert _TOKEN not in out, "bare `token = ...` leaked verbatim"
        assert "[REDACTED]" in out

    def test_redacts_bare_token_yaml_form(self):
        out = redact_secrets(f"token: {_TOKEN}")
        assert _TOKEN not in out
        assert "[REDACTED]" in out

    def test_redacts_bare_token_dict_repr_form(self):
        out = redact_secrets(f"{{'token': '{_TOKEN}', 'created': 123}}")
        assert _TOKEN not in out
        assert "[REDACTED]" in out

    def test_bare_token_pattern_does_not_falsepositive_on_short_values(self):
        """`token = "yes"` or `token: ok` shouldn't match — the new
        pattern gates on a 32+ char token-shaped value to avoid
        eating prose."""
        for benign in (
            "token = yes",
            "token: ok",
            "token='abc'",
            "Bearer token expired (status_token=invalid)",
        ):
            assert redact_secrets(benign) == benign, f"false-positive on {benign!r}"

    def test_redacts_macos_home_path(self):
        """R11-L3: absolute paths like /Users/<name>/... leak the
        OS username into log lines. Backup / library / workdir log
        emissions all carry these. Replace the username segment
        with `~/` so the path stays useful for diagnosis without
        exposing the user."""
        out = redact_secrets(
            "Backup at /Users/alice/Pictures/BestPhotoPicker/data/x.db.backup failed"
        )
        assert "/Users/alice/" not in out
        assert "~/Pictures/BestPhotoPicker/data/x.db.backup" in out

    def test_redacts_linux_home_path(self):
        out = redact_secrets("workdir=/home/bob/photos library=/home/bob/photos/lib1")
        assert "/home/bob/" not in out
        assert "~/photos" in out
        assert "~/photos/lib1" in out

    def test_home_path_redaction_preserves_remainder(self):
        """The path tail after the username segment must remain so
        the operator can still reason about the file structure."""
        out = redact_secrets("/Users/charlie/Library/Caches/bpp/models/clip.onnx")
        assert out == "~/Library/Caches/bpp/models/clip.onnx"

    def test_home_path_redaction_idempotent(self):
        already = "~/Pictures/foo"
        assert redact_secrets(already) == already

    def test_home_path_redaction_does_not_match_substrings(self):
        """`/foo/Users/x/...` (some unrelated path that happens to
        contain `Users/`) shouldn't be mangled. The pattern uses a
        negative lookbehind to anchor at a non-path-char before
        `/Users/`."""
        # `prefix/Users/x/...` should NOT redact the inner path.
        text = "prefix/Users/alice/foo"
        assert redact_secrets(text) == text


# ─── RedactingFormatter integration ───────────────────────────────


class TestRedactingFormatter:
    def test_formatter_redacts_message(self):
        formatter = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            "test",
            logging.INFO,
            "x.py",
            1,
            f"Auth rejected: 127.0.0.1 /api/status?_token={_TOKEN}",
            None,
            None,
        )
        out = formatter.format(record)
        assert _TOKEN not in out

    def test_formatter_redacts_args_substitution(self):
        formatter = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            "test",
            logging.INFO,
            "x.py",
            1,
            "url=%s",
            (f"http://h:5001/?_token={_TOKEN}",),
            None,
        )
        out = formatter.format(record)
        assert _TOKEN not in out


# ─── End-to-end: handler attached to a logger redacts before write ─


class TestEndToEndRedaction:
    def test_stream_handler_output_redacted(self):
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(RedactingFormatter("%(message)s"))
        logger = logging.getLogger("bpp.test_redact_e2e")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            logger.info("got url ?_token=%s", _TOKEN)
            output = buf.getvalue()
            assert _TOKEN not in output, f"Stream handler leaked token: {output!r}"
        finally:
            logger.removeHandler(handler)

    def test_in_memory_handler_redacted(self):
        """The Activity tab and /api/logs read from this handler."""
        h = InMemoryHandler(capacity=10)
        h.setFormatter(RedactingFormatter("%(message)s"))
        logger = logging.getLogger("bpp.test_redact_inmem")
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
        try:
            logger.warning("LAN url http://h:5001/?_token=%s", _TOKEN)
            entries = h.get_entries()
            assert entries, "expected one entry"
            for e in entries:
                assert _TOKEN not in e["msg"]
        finally:
            logger.removeHandler(h)


# ─── format_share_banner: output never contains a token ────────────


class TestBannerNoToken:
    def test_banner_takes_host_port_not_url(self):
        from bpp.web.share import format_share_banner

        lines = format_share_banner("192.168.1.5:5001")
        joined = "\n".join(lines)
        assert "_token=" not in joined
        # Token-shaped 32-byte hex string would also fail
        assert _TOKEN not in joined


# ─── Authorization: Bearer / Basic header (belt-and-suspenders) ───


class TestAuthorizationHeader:
    """bpp doesn't issue Bearer/Basic auth today, but a contributed
    reverse-proxy config, OAuth integration, or third-party tool can
    cause Authorization headers to land in werkzeug error logs or
    debug traces. The redactor covers them so a future feature
    addition doesn't accidentally start leaking credentials into
    rotating server.log files."""

    _BEARER = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature"
    _BASIC = "dXNlcjpwYXNzd29yZA=="  # user:password in base64

    def test_redacts_bearer_token(self):
        out = redact_secrets(f"req hdr=Authorization: Bearer {self._BEARER}")
        assert self._BEARER not in out
        assert "Authorization: Bearer [REDACTED]" in out

    def test_redacts_basic_credentials(self):
        out = redact_secrets(f"req hdr=Authorization: Basic {self._BASIC}")
        assert self._BASIC not in out
        assert "Authorization: Basic [REDACTED]" in out

    def test_case_insensitive(self):
        """werkzeug emits headers in canonical form, but other libraries
        (curl --verbose, requests in debug mode) emit them lowercased."""
        out = redact_secrets(f"hdr authorization: bearer {self._BEARER}")
        assert self._BEARER not in out

    def test_does_not_match_authorization_required_etc(self):
        """Header *names* that start with `Authorization` but aren't
        the auth header itself must not be touched. The pattern
        anchors on `Authorization:` (with the colon) which excludes
        these by construction — this test pins that."""
        line = "Authorization-Required: yes  (some other text)"
        assert redact_secrets(line) == line

    def test_redacts_alongside_other_tokens_in_same_line(self):
        """Multi-token line: query-string token AND Authorization
        header. Both must be redacted in one pass."""
        text = (
            f"GET /api/v1/photos?_token={_TOKEN} HTTP/1.1 hdr=Authorization: Bearer {self._BEARER}"
        )
        out = redact_secrets(text)
        assert _TOKEN not in out
        assert self._BEARER not in out
        assert out.count("[REDACTED]") == 2

    def test_idempotent_on_already_redacted(self):
        """Lines emitted by a server with redaction in place have
        `[REDACTED]` already in the slot. Running the regex again
        must leave the placeholder intact, not mangle it."""
        text = "Authorization: Bearer [REDACTED]"
        assert redact_secrets(text) == text


# ─── Preload from existing server.log: old tokens must be redacted ──


class TestPreloadRedaction:
    """RedactingFormatter only protects NEW log emits. server.log
    files rotated before this defense was in place still contain
    raw tokens; preloading them into the in-memory ring buffer
    must scrub the values before they reach /api/logs."""

    def _write_log(self, tmp_path, lines):
        path = tmp_path / "server.log"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def test_preload_redacts_url_token_in_query(self, tmp_path):
        path = self._write_log(
            tmp_path,
            [
                f"12:00:00 [INFO ] bpp.test: GET /api/photos?_token={_TOKEN} HTTP/1.1",
            ],
        )
        h = InMemoryHandler()
        h.preload_from_file(path)
        for e in h.get_entries():
            assert _TOKEN not in e["msg"], f"Preloaded entry leaked token: {e['msg']!r}"

    def test_preload_redacts_share_url_token(self, tmp_path):
        path = self._write_log(
            tmp_path,
            [
                "12:00:00 [WARNING] bpp.commands:   Share URL: "
                f"http://192.168.1.5:5001/?_token={_TOKEN}",
            ],
        )
        h = InMemoryHandler()
        h.preload_from_file(path)
        for e in h.get_entries():
            assert _TOKEN not in e["msg"]

    def test_preload_redacts_x_auth_token_header(self, tmp_path):
        path = self._write_log(
            tmp_path,
            [f"12:00:00 [DEBUG] bpp.test: header X-Auth-Token: {_TOKEN}"],
        )
        h = InMemoryHandler()
        h.preload_from_file(path)
        for e in h.get_entries():
            assert _TOKEN not in e["msg"]

    def test_preload_redacts_share_token_kv(self, tmp_path):
        path = self._write_log(
            tmp_path,
            [f"12:00:00 [INFO ] bpp.test: state share_token={_TOKEN}"],
        )
        h = InMemoryHandler()
        h.preload_from_file(path)
        for e in h.get_entries():
            assert _TOKEN not in e["msg"]

    def test_preload_idempotent_on_already_redacted(self, tmp_path):
        """Lines that already contain `[REDACTED]` (because they were
        emitted by a server with redaction in place) must round-trip
        unchanged."""
        path = self._write_log(
            tmp_path,
            [
                "12:00:00 [INFO ] bpp.test: GET /api/photos?_token=[REDACTED] HTTP/1.1",
            ],
        )
        h = InMemoryHandler()
        h.preload_from_file(path)
        entries = h.get_entries()
        assert entries
        # The redact_secrets pattern matches the token value; running
        # it again on a "[REDACTED]" placeholder leaves it as-is.
        assert "[REDACTED]" in entries[0]["msg"]
        assert entries[0]["msg"].count("[REDACTED]") == 1

    def test_preload_redacts_multiple_tokens_in_same_line(self, tmp_path):
        path = self._write_log(
            tmp_path,
            [
                f"12:00:00 [INFO ] bpp.test: req=?_token={_TOKEN} hdr=X-Auth-Token: {_TOKEN}",
            ],
        )
        h = InMemoryHandler()
        h.preload_from_file(path)
        for e in h.get_entries():
            assert _TOKEN not in e["msg"]
            assert e["msg"].count("[REDACTED]") == 2

    def test_preload_skips_unparseable_but_still_redacts(self, tmp_path):
        """Lines that don't match `_LOG_LINE_RE` are dropped (not
        stored), so the preload path doesn't even need to redact
        them — but the redaction runs first and cheaply, so a
        future change that *does* keep unparsed lines is already
        safe by construction."""
        path = self._write_log(
            tmp_path,
            [
                f"completely unstructured log line with ?_token={_TOKEN}",
                f"12:00:00 [INFO ] bpp.test: parsable line ?_token={_TOKEN}",
            ],
        )
        h = InMemoryHandler()
        h.preload_from_file(path)
        # Only the second line stores; verify it's redacted.
        entries = h.get_entries()
        assert len(entries) == 1
        assert _TOKEN not in entries[0]["msg"]


# ─── Werkzeug access-log redaction ────────────────────────────────


class TestWerkzeugRedaction:
    """Werkzeug's `WSGIRequestHandler.log()` writes raw URLs (with
    `?_token=…` query strings) to the `werkzeug` logger. That logger
    sits OUTSIDE our `bpp` namespace, so without explicit wiring its
    output bypasses RedactingFormatter and leaks tokens to stderr,
    `tee`-captured terminals, and launchd's StandardErrorPath.

    These tests verify setup_logging() routes werkzeug through the
    same redacting handlers as bpp.* loggers.
    """

    def _reset_logging(self):
        """Force setup_logging to re-run by clearing the singleton flag
        and stripping handlers it might have left behind."""
        from bpp.utils import logging as bpp_logging

        bpp_logging._CONFIGURED = False
        bpp_logging._memory_handler = None
        # R11-L4: setup_logging now attaches handlers to the ROOT
        # logger so plugin / third-party records also pass through
        # RedactingFormatter. Strip handlers from root, bpp, and
        # werkzeug so a re-init starts clean.
        for name in ("", "bpp", "werkzeug"):
            lg = logging.getLogger(name)
            lg.handlers.clear()
            lg.propagate = True

    def test_werkzeug_logger_redacts_token_in_url(self):
        """R11-L4: handlers now live on root. werkzeug propagates up
        and the root-level RedactingFormatter scrubs the token before
        emit."""
        from bpp.utils.logging import setup_logging

        self._reset_logging()
        setup_logging()

        # Root has the redacting handler chain.
        root = logging.getLogger()
        assert any(
            isinstance(h.formatter, RedactingFormatter)
            for h in root.handlers
            if h.formatter is not None
        ), "root must have a RedactingFormatter-wrapped handler"

        werkzeug = logging.getLogger("werkzeug")
        # werkzeug propagates to root (default after R11-L4); a
        # separate StringIO handler attached to root captures the
        # output for inspection.
        buf = io.StringIO()
        capture = logging.StreamHandler(buf)
        capture.setFormatter(RedactingFormatter("%(message)s"))
        root.addHandler(capture)
        try:
            werkzeug.info(
                '127.0.0.1 - - [date] "GET /api/status?_token=%s HTTP/1.1" 200 -',
                _TOKEN,
            )
            output = buf.getvalue()
            assert _TOKEN not in output, f"werkzeug leaked token: {output!r}"
            assert "[REDACTED]" in output
        finally:
            root.removeHandler(capture)

    def test_third_party_logger_also_redacts(self):
        """R11-L4: a plugin emitting through its own logger namespace
        (e.g. `logging.getLogger("my_plugin")`) propagates to root.
        Pre-fix, root had no redacting handlers, so plugin output
        bypassed the scrub. Now root carries the redacting chain so
        third-party logger output is covered too."""
        from bpp.utils.logging import setup_logging

        self._reset_logging()
        setup_logging()

        root = logging.getLogger()
        buf = io.StringIO()
        capture = logging.StreamHandler(buf)
        capture.setFormatter(RedactingFormatter("%(message)s"))
        root.addHandler(capture)
        try:
            plugin = logging.getLogger("my_third_party_plugin")
            plugin.warning("Plugin emit with token=%s", _TOKEN)
            output = buf.getvalue()
            assert _TOKEN not in output, f"third-party plugin logger leaked token: {output!r}"
        finally:
            root.removeHandler(capture)

    def test_werkzeug_is_filtered_from_memory_handler(self):
        """Activity tab / /api/logs should hide chatty werkzeug access
        lines while still redacting them in the normal log stream."""
        from bpp.utils.logging import (
            get_memory_handler,
            setup_logging,
        )

        self._reset_logging()
        setup_logging()

        werkzeug = logging.getLogger("werkzeug")
        werkzeug.warning("test access line ?_token=%s", _TOKEN)

        mem = get_memory_handler()
        assert mem is not None
        msgs = [e["msg"] for e in mem.get_entries()]
        assert not any("test access line" in m for m in msgs), (
            "werkzeug output should be filtered from the in-memory ring buffer"
        )
        for m in msgs:
            assert _TOKEN not in m

    def test_werkzeug_error_reaches_memory_handler(self):
        """ERROR / CRITICAL from werkzeug must NOT be filtered — those
        are real failures the operator needs to see in Activity, not
        the chatty per-request noise we suppress at WARNING and below.
        """
        from bpp.utils.logging import (
            get_memory_handler,
            setup_logging,
        )

        self._reset_logging()
        setup_logging()

        werkzeug = logging.getLogger("werkzeug")
        werkzeug.error("simulated unhandled request error")
        werkzeug.critical("simulated socket failure")

        mem = get_memory_handler()
        assert mem is not None
        msgs = [e["msg"] for e in mem.get_entries()]
        assert any("simulated unhandled request error" in m for m in msgs), (
            "werkzeug ERROR was filtered — operator would miss real failures"
        )
        assert any("simulated socket failure" in m for m in msgs), (
            "werkzeug CRITICAL was filtered — operator would miss real failures"
        )


# ─── R8-H3: in-place scrub of historical (pre-redaction) log files ──


class TestHistoricalLogScrub:
    """`RedactingFormatter` and `preload_from_file` cover NEW emits
    and the in-memory ring buffer — but the on-disk bytes of a
    rotated `server.log` written before redaction landed are still
    cleartext. A backup tool, cloud sync, support bundle, or copied
    file from the log dir then exposes the live LAN share token.

    `add_file_handler()` runs an in-place atomic rewrite of every
    `server.log*` file in the log dir at startup, gated by a
    `.scrubbed-v1` sentinel. Once-and-done; no perf cost on later
    boots. This test class exercises that contract."""

    def test_scrub_rewrites_token_in_active_log(self, tmp_path):
        from bpp.utils.logging import _scrub_historical_logs_in_place

        log_path = tmp_path / "server.log"
        log_path.write_text(f"OLD LINE ?_token={_TOKEN} HTTP/1.1\n", encoding="utf-8")

        _scrub_historical_logs_in_place(str(log_path))

        contents = log_path.read_text(encoding="utf-8")
        assert _TOKEN not in contents, "Live token still readable in scrubbed log"
        assert "[REDACTED]" in contents

    def test_scrub_rewrites_token_in_rotated_siblings(self, tmp_path):
        from bpp.utils.logging import _scrub_historical_logs_in_place

        log_path = tmp_path / "server.log"
        log_path.write_text("clean\n", encoding="utf-8")
        # Rotated siblings (RotatingFileHandler default backupCount=3)
        for n in range(1, 4):
            (tmp_path / f"server.log.{n}").write_text(
                f"rotated-{n} ?_token={_TOKEN}\n", encoding="utf-8"
            )

        _scrub_historical_logs_in_place(str(log_path))

        for n in range(1, 4):
            sibling = (tmp_path / f"server.log.{n}").read_text(encoding="utf-8")
            assert _TOKEN not in sibling, f"Token leaked in server.log.{n}"

    def test_scrub_writes_sentinel(self, tmp_path):
        from bpp.utils.logging import _SCRUB_SENTINEL, _scrub_historical_logs_in_place

        log_path = tmp_path / "server.log"
        log_path.write_text(f"OLD ?_token={_TOKEN}\n", encoding="utf-8")

        _scrub_historical_logs_in_place(str(log_path))

        assert (tmp_path / _SCRUB_SENTINEL).exists()

    def test_scrub_is_idempotent_via_sentinel(self, tmp_path):
        """Second call must be a no-op. Verify by writing a NEW token
        into the log AFTER the sentinel exists and confirming the
        scrub does NOT touch it (we trust the live-log redactor for
        post-sentinel emits, not the scrub)."""
        from bpp.utils.logging import _SCRUB_SENTINEL, _scrub_historical_logs_in_place

        log_path = tmp_path / "server.log"
        # First run, real scrub
        log_path.write_text(f"OLD ?_token={_TOKEN}\n", encoding="utf-8")
        _scrub_historical_logs_in_place(str(log_path))
        assert (tmp_path / _SCRUB_SENTINEL).exists()

        # Inject a "new" cleartext token (simulating the impossible
        # case where the post-sentinel formatter failed to redact)
        log_path.write_text(f"NEW ?_token={_TOKEN}\n", encoding="utf-8")
        _scrub_historical_logs_in_place(str(log_path))

        # Sentinel kept us out — file unchanged
        assert _TOKEN in log_path.read_text(encoding="utf-8"), (
            "Idempotence broken: sentinel should have skipped the second scrub"
        )

    def test_scrub_no_op_when_no_tokens_present(self, tmp_path):
        """If existing logs have no tokens, the scrub still writes the
        sentinel (so it doesn't re-scan on every boot) but doesn't
        rewrite the file."""
        from bpp.utils.logging import _SCRUB_SENTINEL, _scrub_historical_logs_in_place

        log_path = tmp_path / "server.log"
        clean_content = "12:00:00 [INFO ] bpp.test: nothing-secret-here\n"
        log_path.write_text(clean_content, encoding="utf-8")
        original_mtime = log_path.stat().st_mtime

        _scrub_historical_logs_in_place(str(log_path))

        # File unchanged (mtime AND bytes), sentinel written
        assert log_path.read_text(encoding="utf-8") == clean_content
        assert log_path.stat().st_mtime == original_mtime
        assert (tmp_path / _SCRUB_SENTINEL).exists()

    def test_scrub_handles_missing_log_dir(self, tmp_path):
        """Defensive: if the log dir doesn't exist (first run, ever),
        the scrub must return cleanly without crashing."""
        from bpp.utils.logging import _scrub_historical_logs_in_place

        nonexistent = tmp_path / "no-such-dir" / "server.log"
        # Must not raise
        _scrub_historical_logs_in_place(str(nonexistent))

    def test_scrub_redacts_x_auth_token_in_history(self, tmp_path):
        """Header-form leak — `X-Auth-Token: <hex>` lines from werkzeug
        debug must also get scrubbed, not just URL-query-form."""
        from bpp.utils.logging import _scrub_historical_logs_in_place

        log_path = tmp_path / "server.log"
        log_path.write_text(f"DEBUG bpp: header X-Auth-Token: {_TOKEN}\n", encoding="utf-8")

        _scrub_historical_logs_in_place(str(log_path))

        contents = log_path.read_text(encoding="utf-8")
        assert _TOKEN not in contents
