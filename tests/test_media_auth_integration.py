"""End-to-end Flask integration: media routes go through the auth gate.

Why this exists separate from test_authorize_request.py: those tests call
`authorize_request()` directly with a FakeRequest. They prove the policy
is correct, but they don't prove the policy is *wired in* for media
routes. A blueprint registration shuffle, a `before_request` hook
re-shuffle, or a future refactor that splits the gate could silently
bypass the check without breaking any unit test.

Each test goes through Flask's `test_client()` to GET an actual media
URL with a controlled IP/token/cookie combo and asserts on the HTTP
status. We don't care whether the handler finds a real file (it won't —
the path_hash is fake), only whether the gate let the request through
or rejected it as 403.

Status semantics:
  * 403 → gate rejected the request (the auth-failure case we want)
  * 404 → gate passed, handler couldn't find the file (auth OK)
  * 200 → gate passed AND file exists (not exercised here)
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(tmp_path):
    """Fresh Flask app with TESTING=False so the auth middleware actually runs."""
    from bpp.web.app import create_app
    from bpp.web.share import set_lan_sharing_enabled

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = False
    with app.app_context():
        set_lan_sharing_enabled(app.extensions["bpp"].get_conn(), True)
    return app


def _get(app, path, *, remote_addr, token=None, fp=None):
    """Issue a GET claiming to come from a specific IP, optionally with token + FP cookie."""
    client = app.test_client()
    if fp:
        client.set_cookie("bpp_share_fp", fp)
    headers = {}
    if token:
        headers["X-Auth-Token"] = token
    return client.get(path, headers=headers, environ_overrides={"REMOTE_ADDR": remote_addr})


# Three-prefix sweep: every test runs against /thumb/, /photo/, /video/.
MEDIA_PREFIXES = ["/thumb/", "/photo/", "/video/"]


@pytest.mark.parametrize("prefix", MEDIA_PREFIXES)
class TestMediaRoutesIntegration:
    def test_lan_no_token_returns_403(self, app, prefix):
        """LAN client with no auth at all → 403 (gate rejects, never reaches handler)."""
        r = _get(app, f"{prefix}fakehash", remote_addr="192.168.1.5")
        assert r.status_code == 403, (
            f"{prefix} on LAN without token returned {r.status_code}; "
            "expected 403 — the auth gate must fire BEFORE the handler"
        )

    def test_lan_share_token_no_fingerprint_returns_403(self, app, prefix):
        """Share token alone is not enough for LAN — pairing is required."""
        from bpp.web.share import get_share_token

        with app.app_context():
            share_token = get_share_token(app.extensions["bpp"].get_conn())
        r = _get(app, f"{prefix}fakehash", remote_addr="192.168.1.5", token=share_token)
        # 403 in either flavour (DENY or PAIR_REQUIRED both render as 403)
        assert r.status_code == 403

    def test_lan_paired_trusted_passes_gate(self, app, prefix):
        """Paired+trusted device → gate passes (404 from handler is fine — proves auth ran)."""
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_share_token,
        )

        with app.app_context():
            conn = app.extensions["bpp"].get_conn()
            share_token = get_share_token(conn)
            d = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
            approve_device(conn, d["id"])
        r = _get(
            app,
            f"{prefix}fakehash",
            remote_addr="192.168.1.5",
            token=share_token,
            fp="fp-A",
        )
        # 404 is the success signal: gate passed, handler ran, no such file
        assert r.status_code != 403, (
            f"{prefix} blocked a paired+trusted LAN device — auth gate too tight"
        )

    def test_lan_revoked_returns_403(self, app, prefix):
        """Once revoked, a previously-trusted FP cookie no longer unlocks media."""
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_share_token,
            revoke_device,
        )

        with app.app_context():
            conn = app.extensions["bpp"].get_conn()
            share_token = get_share_token(conn)
            d = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
            approve_device(conn, d["id"])
            revoke_device(conn, d["id"])
        r = _get(
            app,
            f"{prefix}fakehash",
            remote_addr="192.168.1.5",
            token=share_token,
            fp="fp-A",
        )
        assert r.status_code == 403

    def test_loopback_with_app_token_passes_gate(self, app, prefix):
        """Loopback + valid app token → gate passes (Tauri / dev browser path)."""
        ctx = app.extensions["bpp"]
        r = _get(app, f"{prefix}fakehash", remote_addr="127.0.0.1", token=ctx.auth_token)
        assert r.status_code != 403, (
            f"{prefix} blocked the local Tauri webview — Tauri can't render media at all"
        )

    def test_loopback_no_token_returns_403(self, app, prefix):
        """Regression: loopback used to be ALLOW for media. Now requires the app token —
        defends against a malicious local app on the host scraping localhost:5001."""
        r = _get(app, f"{prefix}fakehash", remote_addr="127.0.0.1")
        assert r.status_code == 403

    def test_lan_disabled_loopback_only(self, app, prefix):
        """When the LAN toggle is off, ALL non-loopback requests are denied —
        even with a valid share token + paired device."""
        from bpp.web.share import (
            approve_device,
            find_or_create_pending_device,
            get_share_token,
            set_lan_sharing_enabled,
        )

        with app.app_context():
            conn = app.extensions["bpp"].get_conn()
            share_token = get_share_token(conn)
            d = find_or_create_pending_device(conn, "fp-A", "iPhone", "192.168.1.5")
            approve_device(conn, d["id"])
            set_lan_sharing_enabled(conn, False)
        r = _get(
            app,
            f"{prefix}fakehash",
            remote_addr="192.168.1.5",
            token=share_token,
            fp="fp-A",
        )
        assert r.status_code == 403, (
            f"{prefix} from LAN passed the gate while LAN sharing is OFF — toggle is broken"
        )
