"""Tests for the LAN-share auth gate in the request middleware.

Contract: when LAN sharing is OFF (the persisted toggle), only loopback
requests reach the app — anything from a LAN address gets 403, even
with a valid auth token. When LAN sharing is ON, requests from any
network are admitted as long as the auth token is valid.

This is the load-bearing security check: a regression here would
silently expose port 5001 to whoever is on the LAN. Test it like the
auth boundary it is.
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(tmp_path):
    """Fresh Flask app with TESTING=False so the auth middleware actually runs."""
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    # Auth check is bypassed under TESTING — we explicitly want it active here.
    app.config["TESTING"] = False
    return app


def _request_from(app, path, *, remote_addr, token=None):
    """Make a request claiming to come from a specific IP."""
    headers = {}
    if token:
        headers["X-Auth-Token"] = token
    builder = app.test_client()
    return builder.get(path, headers=headers, environ_overrides={"REMOTE_ADDR": remote_addr})


class TestLoopbackAlwaysAllowed:
    """The local app (Tauri webview) must keep working regardless of LAN toggle."""

    def test_loopback_with_token_works_when_lan_off(self, app):
        ctx = app.extensions["bpp"]
        # No need to enable LAN — loopback should bypass the gate
        r = _request_from(app, "/api/v1/status", remote_addr="127.0.0.1", token=ctx.auth_token)
        assert r.status_code == 200

    def test_loopback_ipv6_also_allowed(self, app):
        ctx = app.extensions["bpp"]
        r = _request_from(app, "/api/v1/status", remote_addr="::1", token=ctx.auth_token)
        assert r.status_code == 200


class TestLanGated:
    """Non-loopback requests are rejected unless LAN sharing is enabled."""

    def test_lan_request_rejected_when_lan_off(self, app):
        ctx = app.extensions["bpp"]
        # Default: LAN sharing off
        r = _request_from(app, "/api/v1/status", remote_addr="192.168.1.5", token=ctx.auth_token)
        assert r.status_code == 403

    def test_lan_request_allowed_when_lan_on(self, app):
        from bpp.web.share import set_lan_sharing_enabled

        ctx = app.extensions["bpp"]
        # Enable LAN sharing in the DB settings
        with app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)
        r = _request_from(app, "/api/v1/status", remote_addr="192.168.1.5", token=ctx.auth_token)
        assert r.status_code == 200

    def test_lan_request_still_rejected_without_token_when_on(self, app):
        """LAN-on does not disable auth — token still required."""
        from bpp.web.share import set_lan_sharing_enabled

        ctx = app.extensions["bpp"]
        with app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)
        r = _request_from(app, "/api/v1/status", remote_addr="192.168.1.5", token=None)
        assert r.status_code == 403

    def test_index_page_always_loopback_only_when_off(self, app):
        """Even the index page is blocked from LAN when sharing is off."""
        # The index page doesn't require a token, so the LAN gate is the
        # only thing standing between a stranger on the LAN and the SPA
        # (which would then itself be auth-walled, but defense in depth).
        r = _request_from(app, "/", remote_addr="192.168.1.5")
        assert r.status_code == 403

    def test_index_page_reachable_from_lan_when_on(self, app):
        from bpp.web.share import set_lan_sharing_enabled

        ctx = app.extensions["bpp"]
        with app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)
        r = _request_from(app, "/", remote_addr="192.168.1.5")
        assert r.status_code == 200


class TestDualTokenAuth:
    """API requests authenticate with EITHER the app session token OR the
    persisted share token. The two-token model lets the user revoke the
    share token (kicking phones off) without invalidating their own
    Tauri webview session."""

    def test_share_token_with_trusted_device_works_from_lan(self, app):
        """Tier 2: share token alone is no longer enough. Phone also
        needs a fingerprint cookie that maps to an *approved* device."""
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_share_token,
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
        r = client.get(
            "/api/v1/status",
            headers={"X-Auth-Token": share_token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.5"},
        )
        assert r.status_code == 200

    def test_share_token_without_pairing_returns_pair_required(self, app):
        """Tier 2: phone with share token but no approved fingerprint
        gets PAIR_REQUIRED so the JS can route to the pairing page."""
        from bpp.web.share import get_share_token, set_lan_sharing_enabled

        ctx = app.extensions["bpp"]
        with app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)
            share_token = get_share_token(ctx.get_conn())
        r = _request_from(app, "/api/v1/status", remote_addr="192.168.1.5", token=share_token)
        assert r.status_code == 403
        assert r.get_json().get("pair_required") is True

    def test_app_token_works_from_lan_when_on(self, app):
        """Tauri-style requests still work even after enabling LAN sharing."""
        from bpp.web.share import set_lan_sharing_enabled

        ctx = app.extensions["bpp"]
        with app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)
        r = _request_from(app, "/api/v1/status", remote_addr="127.0.0.1", token=ctx.auth_token)
        assert r.status_code == 200

    def test_revoke_invalidates_old_share_token(self, app):
        from bpp.web.share import (
            get_share_token,
            regenerate_share_token,
            set_lan_sharing_enabled,
        )

        ctx = app.extensions["bpp"]
        with app.app_context():
            set_lan_sharing_enabled(ctx.get_conn(), True)
            old_token = get_share_token(ctx.get_conn())
            regenerate_share_token(ctx.get_conn())
        r = _request_from(app, "/api/v1/status", remote_addr="192.168.1.5", token=old_token)
        assert r.status_code == 403
