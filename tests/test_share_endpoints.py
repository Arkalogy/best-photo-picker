"""Tests for /api/share/{info,toggle,revoke,qr} endpoints.

These cover the HTTP surface the Settings → Share tab consumes. Run
under TESTING=True so we don't have to forge tokens; the auth-gate
contract is locked in by tests/test_share_auth_gate.py.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def share_app(tmp_path):
    """App with TESTING=True so the auth middleware lets us through."""
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


# ─── /api/share/info ─────────────────────────────────────────────────


class TestShareInfoEndpoint:
    def test_reflects_persisted_disabled(self, share_app):
        r = share_app.test_client().get("/api/v1/share/info")
        assert r.status_code == 200
        data = r.get_json()
        assert data["enabled"] is False
        assert data["share_url"] is None

    def test_reflects_persisted_enabled(self, share_app):
        from bpp.web.share import set_lan_sharing_enabled

        ctx = share_app.extensions["bpp"]
        with share_app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)

        from unittest.mock import patch

        with patch("bpp.web.share.detect_lan_ip", return_value="192.168.1.50"):
            r = share_app.test_client().get("/api/v1/share/info")
        data = r.get_json()
        assert data["enabled"] is True
        assert data["share_url"] is not None
        assert "192.168.1.50" in data["share_url"]

    def test_share_url_uses_share_token_not_app_token(self, share_app):
        """Critical: the URL handed to phones must contain the persisted
        share token, not the per-boot app session token. Otherwise URLs
        would die on every restart — defeats the whole point."""
        from unittest.mock import patch

        from bpp.web.share import get_share_token, set_lan_sharing_enabled

        ctx = share_app.extensions["bpp"]
        with share_app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)
            share_token = get_share_token(ctx.get_conn())

        with patch("bpp.web.share.detect_lan_ip", return_value="192.168.1.50"):
            data = share_app.test_client().get("/api/v1/share/info").get_json()
        assert share_token in data["share_url"]
        # And NOT the app session token (which rotates per boot)
        assert ctx.auth_token not in data["share_url"]


# ─── /api/share/toggle ───────────────────────────────────────────────


class TestShareToggleEndpoint:
    def test_toggle_on(self, share_app):
        # R11-M2: simulate a server bound to a LAN interface so the
        # restart-required gate doesn't fire. The default bound_host
        # is now "127.0.0.1" (fail-closed); these tests target the
        # happy path where sharing was on at startup.
        share_app.extensions["bpp"].bound_host = "0.0.0.0"
        r = share_app.test_client().post("/api/v1/share/toggle", json={"enabled": True})
        assert r.status_code == 200
        assert r.get_json()["enabled"] is True

    def test_toggle_off(self, share_app):
        share_app.extensions["bpp"].bound_host = "0.0.0.0"
        client = share_app.test_client()
        client.post("/api/v1/share/toggle", json={"enabled": True})
        r = client.post("/api/v1/share/toggle", json={"enabled": False})
        assert r.status_code == 200
        assert r.get_json()["enabled"] is False

    def test_toggle_persists(self, share_app):
        """Toggle survives in DB — info endpoint reflects it on next call."""
        from unittest.mock import patch

        share_app.extensions["bpp"].bound_host = "0.0.0.0"
        client = share_app.test_client()
        client.post("/api/v1/share/toggle", json={"enabled": True})
        with patch("bpp.web.share.detect_lan_ip", return_value="192.168.1.50"):
            data = client.get("/api/v1/share/info").get_json()
        assert data["enabled"] is True

    def test_missing_enabled_field_returns_400(self, share_app):
        r = share_app.test_client().post("/api/v1/share/toggle", json={})
        assert r.status_code == 400


# ─── R10-M1: refuse enable while loopback-bound ──────────────────────


class TestShareToggleBoundHostGate:
    """R9-P3 made `bpp serve --host` default to 127.0.0.1 when LAN
    sharing is off. Without R10-M1, a user who started the server
    with sharing off and then toggled it ON via Settings would
    persist the flag and see a LAN URL in the UI — but the bind
    address is fixed at startup, so phones could not actually
    connect. Refuse with 409 so the user knows a restart is
    required."""

    def test_enable_on_loopback_bind_returns_409(self, share_app):
        share_app.extensions["bpp"].bound_host = "127.0.0.1"
        r = share_app.test_client().post("/api/v1/share/toggle", json={"enabled": True})
        assert r.status_code == 409, (
            f"Expected 409 restart-required; got {r.status_code} {r.data!r}"
        )
        body = r.get_json()
        assert body.get("restart_required") is True

        # And the DB flag must NOT have flipped to True.
        from bpp.web.share import is_lan_sharing_enabled

        with share_app.app_context():
            assert is_lan_sharing_enabled(share_app.extensions["bpp"].get_conn()) is False

    def test_enable_on_lan_bind_succeeds(self, share_app):
        share_app.extensions["bpp"].bound_host = "0.0.0.0"
        r = share_app.test_client().post("/api/v1/share/toggle", json={"enabled": True})
        assert r.status_code == 200

    def test_disable_on_loopback_bind_still_works(self, share_app):
        """Disabling sharing must succeed regardless of bind host —
        the failure mode the gate prevents is the enable side only."""
        from bpp.web.share import set_lan_sharing_enabled

        ctx = share_app.extensions["bpp"]
        ctx.bound_host = "127.0.0.1"
        # Pre-seed sharing as enabled (e.g. the previous run had sharing
        # on at startup, so the bind host was 0.0.0.0; user has since
        # restarted into a non-sharing config and is now disabling).
        with share_app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)

        r = share_app.test_client().post("/api/v1/share/toggle", json={"enabled": False})
        assert r.status_code == 200

    def test_ipv6_loopback_also_blocked(self, share_app):
        share_app.extensions["bpp"].bound_host = "::1"
        r = share_app.test_client().post("/api/v1/share/toggle", json={"enabled": True})
        assert r.status_code == 409

    def test_default_bound_host_is_loopback_sentinel(self, share_app):
        """R11-M2: WebAppState defaults bound_host to '127.0.0.1'
        instead of None. A test build that didn't go through do_serve
        therefore inherits the fail-closed posture and the toggle gate
        fires. Pre-fix, None silently bypassed the gate — a footgun
        for any future code path that constructs WebAppState outside
        of do_serve."""
        ctx = share_app.extensions["bpp"]
        # Don't override bound_host — use the default.
        assert ctx.bound_host == "127.0.0.1"

        r = share_app.test_client().post("/api/v1/share/toggle", json={"enabled": True})
        assert r.status_code == 409, (
            f"Expected 409 with default bound_host; got {r.status_code} {r.data!r}"
        )

    def test_uppercase_localhost_blocked(self, share_app):
        """R11-M1: an operator passing `--host=LOCALHOST` should also
        trip the gate. Pre-fix, the case-sensitive `.startswith` check
        accepted LOCALHOST as non-loopback and let the toggle through."""
        share_app.extensions["bpp"].bound_host = "LOCALHOST"
        r = share_app.test_client().post("/api/v1/share/toggle", json={"enabled": True})
        assert r.status_code == 409, (
            f"--host=LOCALHOST should trip the gate; got {r.status_code} {r.data!r}"
        )


# ─── /api/share/revoke ───────────────────────────────────────────────


class TestShareRevokeEndpoint:
    def test_revoke_returns_new_share_url(self, share_app):
        from unittest.mock import patch

        from bpp.web.share import get_share_token, set_lan_sharing_enabled

        ctx = share_app.extensions["bpp"]
        with share_app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)
            old_token = get_share_token(ctx.get_conn())

        with patch("bpp.web.share.detect_lan_ip", return_value="192.168.1.50"):
            r = share_app.test_client().post("/api/v1/share/revoke")
        assert r.status_code == 200
        data = r.get_json()
        assert data["share_url"] is not None
        assert old_token not in data["share_url"]

    def test_revoke_changes_persisted_token(self, share_app):
        from bpp.web.share import get_share_token

        ctx = share_app.extensions["bpp"]
        with share_app.app_context():
            old = get_share_token(ctx.get_conn())
        share_app.test_client().post("/api/v1/share/revoke")
        with share_app.app_context():
            new = get_share_token(ctx.get_conn())
        assert old != new


# ─── L6: token-bearing responses must not be cached ─────────────────


class TestShareCacheControl:
    """L6: /api/share/info and /api/share/revoke responses contain a
    URL whose query string carries the share token. Both must set
    Cache-Control: no-store so intermediaries / browser BFCache don't
    persist the token any longer than necessary."""

    def test_info_response_is_no_store(self, share_app):
        r = share_app.test_client().get("/api/v1/share/info")
        assert r.status_code == 200
        assert "no-store" in r.headers.get("Cache-Control", "")

    def test_revoke_response_is_no_store(self, share_app):
        r = share_app.test_client().post("/api/v1/share/revoke")
        assert r.status_code == 200
        assert "no-store" in r.headers.get("Cache-Control", "")

    def test_info_does_not_return_bare_token(self, share_app):
        """The token only appears as part of share_url, never as a
        standalone 'token' field — keeps the leakage surface to a
        single, documented vector."""
        from unittest.mock import patch

        from bpp.web.share import set_lan_sharing_enabled

        ctx = share_app.extensions["bpp"]
        with share_app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)
        with patch("bpp.web.share.detect_lan_ip", return_value="192.168.1.50"):
            data = share_app.test_client().get("/api/v1/share/info").get_json()
        assert "token" not in data
        assert "share_token" not in data

    def test_revoke_does_not_return_bare_token(self, share_app):
        data = share_app.test_client().post("/api/v1/share/revoke").get_json()
        assert "token" not in data
        assert "share_token" not in data
