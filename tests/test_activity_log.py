"""Tests for the activity log feature — InMemoryHandler + /api/logs endpoints."""

from __future__ import annotations

import logging
import os
import time

import pytest

# ── InMemoryHandler unit tests ──


class TestInMemoryHandler:
    def _make_handler(self, capacity=100):
        from bpp.utils.logging import InMemoryHandler

        return InMemoryHandler(capacity=capacity)

    def test_emit_stores_entries(self):
        h = self._make_handler()
        logger = logging.getLogger("test.activity.emit")
        logger.addHandler(h)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("hello world")
            assert len(h.buffer) == 1
            assert h.buffer[0]["level"] == "INFO"
            assert h.buffer[0]["module"] == "test.activity.emit"
            assert "hello world" in h.buffer[0]["msg"]
        finally:
            logger.removeHandler(h)

    def test_exc_info_traceback_stripped_from_ui_message(self):
        """Bug #11 (UAT 2026-06-01): when a log call passes exc_info=True,
        Python's default formatter appends the full traceback to the
        message. The in-memory handler powers /api/v1/logs which feeds
        the Activity Log dropdown — surfacing a raw Python traceback
        there read as 'app is broken' to users. The handler must strip
        the traceback for the UI feed while preserving the human
        message. The file handler (server.log) still gets the full
        traceback via the unmodified record.
        """
        h = self._make_handler()
        # Standard formatter so we exercise the exc_info-append path.
        h.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger("test.activity.exc_info")
        logger.addHandler(h)
        logger.setLevel(logging.DEBUG)
        try:
            try:
                raise ValueError("synthetic boom")
            except ValueError:
                logger.warning("Update check failed", exc_info=True)
            assert len(h.buffer) == 1
            stored = h.buffer[0]["msg"]
            # The human message is present.
            assert "Update check failed" in stored
            # The traceback markers are NOT present.
            assert "Traceback" not in stored, f"traceback leaked into UI feed: {stored!r}"
            assert "ValueError" not in stored
            assert "synthetic boom" not in stored
        finally:
            logger.removeHandler(h)

    def test_capacity_limit(self):
        h = self._make_handler(capacity=3)
        logger = logging.getLogger("test.activity.cap")
        logger.addHandler(h)
        logger.setLevel(logging.DEBUG)
        try:
            for i in range(5):
                logger.info("msg %d", i)
            assert len(h.buffer) == 3
            assert "msg 2" in h.buffer[0]["msg"]
            assert "msg 4" in h.buffer[2]["msg"]
        finally:
            logger.removeHandler(h)

    def test_get_entries_since(self):
        h = self._make_handler()
        now = time.time()
        h.buffer.append({"ts": now - 10, "level": "INFO", "module": "a", "msg": "old"})
        h.buffer.append({"ts": now - 1, "level": "WARNING", "module": "a", "msg": "new"})
        entries = h.get_entries(since=now - 5)
        assert len(entries) == 1
        assert entries[0]["msg"] == "new"

    def test_get_entries_level_filter(self):
        h = self._make_handler()
        now = time.time()
        h.buffer.append({"ts": now, "level": "INFO", "module": "a", "msg": "info"})
        h.buffer.append({"ts": now, "level": "WARNING", "module": "a", "msg": "warn"})
        h.buffer.append({"ts": now, "level": "ERROR", "module": "a", "msg": "err"})
        entries = h.get_entries(level="warning")
        assert len(entries) == 2
        assert entries[0]["msg"] == "warn"
        assert entries[1]["msg"] == "err"

    def test_get_entries_limit(self):
        h = self._make_handler()
        now = time.time()
        for i in range(10):
            h.buffer.append({"ts": now, "level": "INFO", "module": "a", "msg": f"m{i}"})
        entries = h.get_entries(limit=3)
        assert len(entries) == 3
        # Should return the last 3
        assert entries[0]["msg"] == "m7"

    def test_clear(self):
        h = self._make_handler()
        h.buffer.append({"ts": 1, "level": "INFO", "module": "a", "msg": "x"})
        h.clear()
        assert len(h.buffer) == 0

    def test_preload_from_file(self, tmp_path):
        log_file = tmp_path / "server.log"
        log_file.write_text(
            "14:32:01 [INFO ] bpp.web.app: Server started\n"
            "14:32:02 [WARN ] bpp.scoring.face: Face failed for img.jpg\n"
            "not a log line\n"
            "14:32:03 [ERROR] bpp.web.state: DB corrupt\n"
        )
        h = self._make_handler()
        h.preload_from_file(str(log_file))
        assert len(h.buffer) == 3
        assert h.buffer[0]["level"] == "INFO"
        assert h.buffer[1]["level"] == "WARN"
        assert h.buffer[2]["level"] == "ERROR"

    def test_preload_missing_file(self, tmp_path):
        h = self._make_handler()
        h.preload_from_file(str(tmp_path / "nonexistent.log"))
        assert len(h.buffer) == 0


# ── API endpoint tests ──


@pytest.fixture
def log_client(tmp_path, monkeypatch):
    """Flask test client with activity log endpoints."""
    from bpp.web.app import create_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    lib = tmp_path / "testlib"
    lib.mkdir()
    (lib / "logs").mkdir()

    app = create_app(workdir=str(lib), library_path=str(lib))
    app.config["TESTING"] = True
    client = app.test_client()
    # Trigger DB init
    client.get("/api/v1/status")
    return client, lib


class TestApiLogs:
    def test_get_logs_empty(self, log_client):
        client, _ = log_client
        resp = client.get("/api/v1/logs")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "entries" in data
        assert "count" in data

    def test_get_logs_with_limit(self, log_client):
        client, _ = log_client
        resp = client.get("/api/v1/logs?limit=5")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] <= 5

    def test_get_logs_with_level_filter(self, log_client):
        client, _ = log_client
        resp = client.get("/api/v1/logs?level=error")
        assert resp.status_code == 200
        data = resp.get_json()
        for entry in data["entries"]:
            assert entry["level"] == "ERROR"

    def test_clear_logs(self, log_client):
        client, lib = log_client
        # Create a log file
        log_file = lib / "logs" / "server.log"
        log_file.write_text("some log content\n")
        assert log_file.stat().st_size > 0

        resp = client.post("/api/v1/logs/clear")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "cleared"
        assert data["files"] >= 1
        # File should be truncated
        assert log_file.stat().st_size == 0

    def test_clear_logs_removes_rotated_files(self, log_client):
        client, lib = log_client
        logs_dir = lib / "logs"
        for name in ["server.log", "server.log.1", "server.log.2"]:
            (logs_dir / name).write_text("content\n")

        resp = client.post("/api/v1/logs/clear")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["files"] == 3
        for name in ["server.log", "server.log.1", "server.log.2"]:
            assert (logs_dir / name).stat().st_size == 0


class TestApiClientError:
    """POST /api/v1/client-error ingests uncaught client-side JS errors into
    the server log so they show in Activity (they used to live only in the
    browser console)."""

    def test_logs_client_error_at_warning_on_client_logger(self, log_client, caplog):
        client, _ = log_client
        with caplog.at_level(logging.WARNING, logger="bpp.web.client"):
            resp = client.post(
                "/api/v1/client-error",
                json={
                    "message": "TypeError: el.innerHTML is not a function",
                    "source": "/static/js/modules/people.mjs",
                    "lineno": 128,
                    "colno": 5,
                    "stack": "at startPersonRename\nat _bppDispatch",
                },
            )
        assert resp.status_code == 200, resp.get_json()
        # Logged on the dedicated client logger at WARNING — this is the same
        # logger the in-memory handler (Activity feed) captures at runtime.
        recs = [r for r in caplog.records if r.name == "bpp.web.client"]
        assert recs, "client error must be logged on bpp.web.client"
        msg = recs[0].getMessage()
        assert "Client-side error" in msg
        assert "el.innerHTML is not a function" in msg
        assert "people.mjs:128:5" in msg

    def test_oversized_fields_are_clamped(self, log_client, caplog):
        client, _ = log_client
        with caplog.at_level(logging.WARNING, logger="bpp.web.client"):
            resp = client.post(
                "/api/v1/client-error",
                json={"message": "x" * 5000, "stack": "y" * 50000},
            )
        assert resp.status_code == 200
        msg = next(r.getMessage() for r in caplog.records if r.name == "bpp.web.client")
        # Message capped at 500, stack at 2000 — total line stays bounded.
        assert len(msg) < 3000, "client-error log line must be length-capped"

    def test_missing_fields_dont_crash(self, log_client, caplog):
        client, _ = log_client
        with caplog.at_level(logging.WARNING, logger="bpp.web.client"):
            resp = client.post("/api/v1/client-error", json={})
        assert resp.status_code == 200
        assert any(
            "(no message)" in r.getMessage() for r in caplog.records if r.name == "bpp.web.client"
        )


class TestApiDebugMemory:
    def test_returns_200(self, log_client):
        client, _ = log_client
        resp = client.get("/api/v1/debug/memory")
        assert resp.status_code == 200

    def test_has_required_top_level_keys(self, log_client):
        client, _ = log_client
        data = client.get("/api/v1/debug/memory").get_json()
        for key in ("process", "caches", "gc", "threads"):
            assert key in data, f"missing top-level key: {key}"

    def test_process_section_has_rss(self, log_client):
        client, _ = log_client
        data = client.get("/api/v1/debug/memory").get_json()
        assert "rss_mb" in data["process"]
        assert isinstance(data["process"]["rss_mb"], (int, float))

    def test_caches_section_has_known_keys(self, log_client):
        client, _ = log_client
        data = client.get("/api/v1/debug/memory").get_json()
        caches = data["caches"]
        for key in (
            "clip_embeddings_count",
            "thumb_hash_count",
            "face_cluster_map_entries",
            "log_ring_buffer",
        ):
            assert key in caches, f"missing cache key: {key}"

    def test_gc_section_has_counts(self, log_client):
        client, _ = log_client
        data = client.get("/api/v1/debug/memory").get_json()
        gc_data = data["gc"]
        assert "garbage_objects" in gc_data
        assert "collections" in gc_data

    def test_local_app_only(self, log_client):
        """Endpoint must be LOCAL_APP gated (TESTING bypasses — verify it exists and responds)."""
        client, _ = log_client
        resp = client.get("/api/v1/debug/memory")
        assert resp.status_code == 200


# ── JS source scan tests ──


class TestActivityLogJS:
    @pytest.fixture(autouse=True)
    def _load_js(self):
        js_dir = os.path.join(
            os.path.dirname(__file__), "..", "bpp", "web", "static", "js", "modules"
        )
        with open(os.path.join(js_dir, "activity-log.mjs")) as f:
            self.js = f.read()

    def test_has_init_function(self):
        assert "export function initActivityLog()" in self.js

    def test_has_show_function(self):
        assert "export function showActivityLog()" in self.js

    def test_has_clear_function(self):
        assert "export async function clearActivityLog()" in self.js

    def test_has_toggle_dropdown(self):
        assert "export function toggleActivityDropdown()" in self.js

    def test_polls_api(self):
        assert "/api/v1/logs" in self.js

    def test_uses_await_app_confirm_for_clear(self):
        assert "await appConfirm(" in self.js


# ── HTML structure tests ──


class TestActivityLogHTML:
    @pytest.fixture(autouse=True)
    def _load_html(self):
        html_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "bpp",
            "web",
            "templates",
            "index.html",
        )
        with open(html_path) as f:
            self.html = f.read()

    def test_bell_icon_present(self):
        assert 'id="activity-bell-wrap"' in self.html

    def test_dropdown_present(self):
        assert 'id="activity-dropdown"' in self.html

    def test_settings_activity_tab(self):
        assert 'data-tab="activity"' in self.html

    def test_settings_activity_pane(self):
        assert 'id="settings-pane-activity"' in self.html

    def test_level_filter_present(self):
        assert 'id="activity-level-filter"' in self.html

    def test_clear_button_present(self):
        # After onclick→data-action migration the button uses data-action
        assert 'data-action="clearActivityLog"' in self.html

    def test_script_included(self):
        # activity-log.js was migrated to activity-log.mjs and is bridged via
        # the window-export block in index.html — pin both signals.
        assert "activity-log.mjs" in self.html
        assert "activityLog" in self.html  # `import * as activityLog from ...`
