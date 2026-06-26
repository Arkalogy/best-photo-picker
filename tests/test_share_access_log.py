"""Tests for the LAN share access log.

Every successful share-token authentication appends a row: timestamp,
remote IP, user-agent. Settings → Share renders the last 10 so the
user can spot a connection they don't recognize ("iPad in the kitchen
at 3am — wasn't me").

The log is intentionally tiny — capped at last 100 rows total to bound
the table size. No long-term retention story, no rolling window — just
"recent activity, briefly."
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def conn(tmp_path):
    from bpp.db.connection import get_db, init_db

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    return get_db(db_path)


class TestAccessLogStorage:
    def test_record_and_fetch(self, conn):
        from bpp.web.share import recent_share_access, record_share_access

        record_share_access(conn, ip="192.168.1.5", user_agent="Mozilla/5.0 iPhone")
        rows = recent_share_access(conn, limit=10)
        assert len(rows) == 1
        assert rows[0]["ip"] == "192.168.1.5"
        assert "iPhone" in rows[0]["user_agent"]
        assert rows[0]["ts"] > 0

    def test_dedup_within_window(self, conn):
        """Repeat calls from the same (ip, ua) within 10 minutes don't
        spam the table — one page load fires many API requests."""
        from bpp.web.share import recent_share_access, record_share_access

        for _ in range(20):
            record_share_access(conn, ip="192.168.1.5", user_agent="iPhone Safari")
        rows = recent_share_access(conn, limit=50)
        assert len(rows) == 1, f"Expected dedup → 1 row; got {len(rows)}: {rows}"

    def test_returned_in_reverse_chronological_order(self, conn):
        import time

        from bpp.web.share import recent_share_access, record_share_access

        record_share_access(conn, ip="10.0.0.1", user_agent="ua1")
        time.sleep(0.01)
        record_share_access(conn, ip="10.0.0.2", user_agent="ua2")
        time.sleep(0.01)
        record_share_access(conn, ip="10.0.0.3", user_agent="ua3")

        rows = recent_share_access(conn, limit=10)
        assert [r["ip"] for r in rows] == ["10.0.0.3", "10.0.0.2", "10.0.0.1"]

    def test_limit_caps_results(self, conn):
        from bpp.web.share import recent_share_access, record_share_access

        for i in range(5):
            record_share_access(conn, ip=f"10.0.0.{i}", user_agent=f"ua{i}")
        rows = recent_share_access(conn, limit=3)
        assert len(rows) == 3

    def test_old_rows_pruned_at_100(self, conn):
        """Total table size is capped — older rows fall off when new ones arrive."""
        from bpp.web.share import recent_share_access, record_share_access

        for i in range(150):
            record_share_access(conn, ip=f"10.0.0.{i % 256}", user_agent=f"ua{i}")
        # Read everything back — should be at most 100
        rows = recent_share_access(conn, limit=200)
        assert len(rows) <= 100


# ─── Auto-population from middleware ────────────────────────────────


@pytest.fixture
def app(tmp_path):
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = False
    return app


class TestAccessLogPopulatedFromMiddleware:
    def test_share_token_auth_records_access(self, app):
        """Trusted-device share-token auth from LAN appends a row."""
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_share_token,
            recent_share_access,
            set_lan_sharing_enabled,
        )

        ctx = app.extensions["bpp"]
        with app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)
            share_token = get_share_token(ctx.get_conn())
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])

        client = app.test_client()
        client.set_cookie("bpp_share_fp", "fp-A")
        client.get(
            "/api/v1/status",
            headers={"X-Auth-Token": share_token, "User-Agent": "iPhone Safari"},
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )

        with app.app_context():
            rows = recent_share_access(ctx.get_conn(), limit=10)
        assert any(r["ip"] == "192.168.1.5" for r in rows), (
            f"Expected access log entry from 192.168.1.5; got {rows}"
        )

    def test_app_token_does_not_record(self, app):
        """App session token (Tauri) is local — not interesting to log."""
        from bpp.web.share import recent_share_access

        ctx = app.extensions["bpp"]
        client = app.test_client()
        client.get(
            "/api/v1/status",
            headers={"X-Auth-Token": ctx.auth_token},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )

        with app.app_context():
            rows = recent_share_access(ctx.get_conn(), limit=10)
        assert rows == []

    def test_middleware_dedupes_multiple_calls(self, app):
        """The dedup is wired through the middleware too — a phone
        firing 30 API calls per page load must produce ONE row, not 30.
        Verifies the integration boundary, not just the helper."""
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_share_token,
            recent_share_access,
            set_lan_sharing_enabled,
        )

        ctx = app.extensions["bpp"]
        with app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)
            share_token = get_share_token(ctx.get_conn())
            d = find_or_create_pending_device(ctx.get_conn(), "fp-A", "iPhone", "192.168.1.5")
            approve_device(ctx.get_conn(), d["id"])

        client = app.test_client()
        client.set_cookie("bpp_share_fp", "fp-A")
        for _ in range(15):
            client.get(
                "/api/v1/status",
                headers={"X-Auth-Token": share_token, "User-Agent": "iPhone Safari"},
                environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
            )

        with app.app_context():
            rows = recent_share_access(ctx.get_conn(), limit=50)
        assert len(rows) == 1, (
            f"middleware should dedup within the 10-min window; got {len(rows)} rows"
        )

    def test_failed_auth_does_not_record(self, app):
        from bpp.web.share import recent_share_access, set_lan_sharing_enabled

        ctx = app.extensions["bpp"]
        with app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)

        client = app.test_client()
        client.get(
            "/api/v1/status",
            headers={"X-Auth-Token": "wrong-token"},
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )

        with app.app_context():
            rows = recent_share_access(ctx.get_conn(), limit=10)
        assert rows == []


# ─── Endpoint surfaces it ────────────────────────────────────────────


@pytest.fixture
def share_app(tmp_path):
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


class TestShareInfoIncludesAccessLog:
    def test_recent_access_in_info_response(self, share_app):
        from bpp.web.share import record_share_access

        ctx = share_app.extensions["bpp"]
        with share_app.app_context():
            record_share_access(ctx.get_conn(), ip="192.168.1.5", user_agent="iPhone")

        data = share_app.test_client().get("/api/v1/share/info").get_json()
        assert "recent_access" in data
        assert len(data["recent_access"]) == 1
        assert data["recent_access"][0]["ip"] == "192.168.1.5"
