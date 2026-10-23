"""Token redaction in logs — regression guard — part 2 of 2: TestWerkzeugRedaction,
TestHistoricalLogScrub."""

from __future__ import annotations

import io
import logging

from bpp.utils.logging import (
    RedactingFormatter,
)
from tests._log_redaction_shared import _TOKEN

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
        # setup_logging now attaches handlers to the ROOT
        # logger so plugin / third-party records also pass through
        # RedactingFormatter. Strip handlers from root, bpp, and
        # werkzeug so a re-init starts clean.
        for name in ("", "bpp", "werkzeug"):
            lg = logging.getLogger(name)
            lg.handlers.clear()
            lg.propagate = True

    def test_werkzeug_logger_redacts_token_in_url(self):
        """Handlers now live on root. werkzeug propagates up
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
        # werkzeug propagates to root (the default); a
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
        """A plugin emitting through its own logger namespace
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


# ─── In-place scrub of historical (pre-redaction) log files ──


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
