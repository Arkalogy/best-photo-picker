"""Owner-only endpoints must reject paired LAN devices.

Background: `_require_local_app()` in `bpp/web/bp_core.py` blocks
non-LOCAL_APP principals from owner-mutating endpoints (device
approve/revoke). Three high-value endpoints once
forgot to call it:

  * PUT /api/settings           (rewrite scoring weights / token / theme)
  * POST /api/share/toggle      (disable LAN sharing under the owner)
  * POST /api/share/revoke      (rotate share token, locking everyone out)

A trusted LAN_DEVICE (paired phone) holds the share token + a
fingerprint cookie, and authorize_request() admits it as
`PRINCIPAL_LAN_DEVICE`. Without `_require_local_app()` the phone can
hit these endpoints and escalate.

These tests use TESTING=False so the real auth middleware runs.
"""

from __future__ import annotations

from tests.owner_only._owner import as_loopback


class TestSettingsPutOwnerOnly:
    def test_lan_device_blocked(self, app, lan_device):
        # Set the fingerprint cookie so the LAN_DEVICE principal is
        # actually admitted past the LAN gate (otherwise we'd be
        # testing the wrong layer).
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.put(
            "/api/v1/settings",
            json={"theme": "dark"},
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403, (
            f"LAN device must not write settings (got {r.status_code} {r.data})"
        )
        assert b"Owner-only" in r.data

    def test_loopback_owner_allowed(self, app):
        r = as_loopback(app, "PUT", "/api/v1/settings", json={"theme": "dark"})
        assert r.status_code == 200


# ─── /api/share/toggle POST ────────────────────────────────────────


class TestShareTogglOwnerOnly:
    def test_lan_device_blocked(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.post(
            "/api/v1/share/toggle",
            json={"enabled": False},
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403
        # Sharing must remain enabled (the request was rejected)
        from bpp.web.share import is_lan_sharing_enabled

        with app.app_context():
            assert is_lan_sharing_enabled(app.extensions["bpp"].get_conn())

    def test_loopback_owner_allowed(self, app):
        # bound_host defaults to loopback now; pretend the
        # server bound a LAN interface so the restart-required gate
        # doesn't intercept this owner-allowed test.
        app.extensions["bpp"].bound_host = "0.0.0.0"
        r = as_loopback(app, "POST", "/api/v1/share/toggle", json={"enabled": True})
        assert r.status_code == 200


# ─── /api/share/revoke POST ────────────────────────────────────────


class TestShareRevokeOwnerOnly:
    def test_lan_device_blocked(self, app, lan_device):
        from bpp.web.share import get_share_token

        ctx = app.extensions["bpp"]
        with app.app_context():
            token_before = get_share_token(ctx.get_conn())

        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.post(
            "/api/v1/share/revoke",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403

        # Token must NOT have rotated (the LAN device's attempt was
        # rejected, so other paired devices' bookmarks still work)
        with app.app_context():
            token_after = get_share_token(ctx.get_conn())
        assert token_before == token_after

    def test_loopback_owner_allowed(self, app):
        r = as_loopback(app, "POST", "/api/v1/share/revoke")
        assert r.status_code == 200


# ─── GET /api/v1/settings filters sensitive keys for LAN ─────────


class TestSettingsGetSensitiveFilter:
    """GET /api/v1/settings must not return `lan_share_token` to
    authenticated LAN devices. A paired phone reading the token could
    grant other devices access bypassing the pairing flow, or
    exfiltrate it for offline replay.

    Fix: filter sensitive keys for non-LOCAL_APP principals. The
    GET stays accessible to LAN (the SPA needs theme / weights /
    model toggles to render) but token-shaped keys are stripped.
    """

    def test_loopback_owner_sees_share_token(self, app):
        """Owner SPA needs the token to render Share tab + QR."""
        from bpp.web.share import get_share_token

        ctx = app.extensions["bpp"]
        with app.app_context():
            existing_token = get_share_token(ctx.get_conn())

        r = as_loopback(app, "GET", "/api/v1/settings")
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("lan_share_token") == existing_token, (
            "LOCAL_APP must see the share token (needed to render the Settings → Share UI)"
        )

    def test_lan_device_does_not_see_share_token(self, app, lan_device):
        """The actual leak: LAN_DEVICE used to receive the token in
        the GET response. Use the EXISTING share token (not a synthetic
        one) so the auth middleware admits the request — overwriting
        `lan_share_token` mid-test would invalidate the cookie/token
        the LAN device authenticated with."""
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/settings",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 200, (
            f"LAN device should still GET settings (filtered), got {r.status_code} {r.data}"
        )
        data = r.get_json()
        assert "lan_share_token" not in data, (
            "Sensitive key 'lan_share_token' was returned to LAN_DEVICE — "
            "filter is broken; check _SENSITIVE_SETTING_KEYS"
        )
        # And the token value itself nowhere in the response body
        body = r.get_data(as_text=True)
        assert token not in body, (
            "The actual token value appears in the LAN response body even "
            "though the key was filtered — something is leaking it elsewhere"
        )

    def test_pattern_match_on_future_token_keys(self, app, lan_device):
        """Defense in depth on top of the denylist. A future
        token-shaped key (`oauth_secret`, `api_key`, etc.) added to
        settings without remembering to extend _SENSITIVE_SETTING_KEYS
        must still be filtered. The regex catches the common shapes
        (token / secret / password / credential / api_key / private_key)
        case-insensitively."""
        from bpp.db.settings import set_settings

        ctx = app.extensions["bpp"]
        with app.app_context():
            # Plant exotic future-token-shaped keys not in the explicit list
            set_settings(
                ctx.get_conn(),
                {
                    "oauth_client_secret": "secret-A",
                    "db_encryption_key": "secret-B",
                    "user_api_key": "secret-C",
                    "session_password": "secret-D",
                },
            )

        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/settings",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        data = r.get_json()
        body = r.get_data(as_text=True)
        for key in ("oauth_client_secret", "db_encryption_key", "user_api_key", "session_password"):
            assert key not in data, (
                f"Token-shaped key {key!r} leaked to LAN — token-key pattern filter broken"
            )
        for value in ("secret-A", "secret-B", "secret-C", "secret-D"):
            assert value not in body, f"Sensitive value {value!r} appears in LAN response body"

    def test_lan_device_still_sees_non_sensitive_keys(self, app, lan_device):
        """The filter must NOT strip everything — LAN clients need
        theme, weights, etc. to render the SPA. Verify a benign key
        round-trips."""
        from bpp.db.settings import set_settings

        ctx = app.extensions["bpp"]
        with app.app_context():
            # Set a non-sensitive key. Don't touch lan_share_token —
            # that would invalidate the LAN device's auth.
            set_settings(ctx.get_conn(), {"theme": "dark"})

        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/settings",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data.get("theme") == "dark"
        assert "lan_share_token" not in data


# ─── GET /api/v1/share/devices is owner-only ─────────────────────


class TestShareDevicesOwnerOnly:
    """A paired LAN device must NOT be able to enumerate the full
    device roster (other paired phones, pending requests, IPs).
    That's an admin-surface leak — approve and revoke are already
    LOCAL_APP-only. Listing matches.
    """

    def test_loopback_owner_allowed(self, app):
        r = as_loopback(app, "GET", "/api/v1/share/devices")
        assert r.status_code == 200, r.data
        data = r.get_json()
        # Returns the device roster grouped by state
        assert isinstance(data, dict)
        assert "pending" in data
        assert "trusted" in data

    def test_lan_device_blocked(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/share/devices",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403, (
            f"LAN device must not enumerate the device roster (got {r.status_code} {r.data})"
        )
        assert b"Owner-only" in r.data

    def test_lan_device_can_still_check_own_pair_status(self, app, lan_device):
        """The owner-only filter only locks down the FULL roster.
        A LAN device still needs `/api/v1/share/pair/status` to learn
        its own state (pending / trusted / revoked). Verify that path
        is still open."""
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/share/pair/status",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 200, (
            "Pair status must remain LAN-accessible — locking it down would break the pairing UX"
        )


# ─── Admin/install endpoints owner-only ─────────────────────


class TestAdminEndpointsOwnerOnly:
    """A paired LAN device must not trigger admin operations:
    pip install (most consequential network op in the app), model
    redownload/uninstall (cache invalidation + bandwidth DoS), model
    toggles (could disable owner-mandated detection like NSFW
    filter), or library wipe (most destructive endpoint).

    These were missed in the initial @requires_local_app
    sweep. The fix grepped for `@bp.post|put|delete` decorators
    against the admin surface and added the decorator to each.
    """

    def test_models_toggle_blocks_lan(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.post(
            "/api/v1/models/toggle",
            json={"key": "model_clip", "enabled": False},
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403, (
            f"LAN device must not toggle model state (got {r.status_code} {r.data})"
        )

    def test_models_redownload_blocks_lan(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.post(
            "/api/v1/models/redownload",
            json={"name": "CLIP visual"},
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403

    def test_models_uninstall_blocks_lan(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.post(
            "/api/v1/models/uninstall",
            json={"name": "CLIP visual"},
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403

    def test_install_post_blocks_lan(self, app, lan_device):
        """The biggie: pip install runs arbitrary code from PyPI."""
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.post(
            "/api/v1/install/faces",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403

    def test_install_progress_get_blocks_lan(self, app, lan_device):
        """GET /install/<key>/progress runs the
        actual pip subprocess inside its generator. Without owner-only
        gate, opening the URL alone could start pip — bypassing the
        POST start-gate."""
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/install/faces/progress",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403

    def test_clear_library_blocks_lan(self, app, lan_device):
        """Most destructive endpoint in the app — wipes DB + photo
        files on disk."""
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.delete(
            "/api/v1/library",
            json={"confirmation": "delete"},
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403

    def test_install_progress_refuses_without_post_first(self, app):
        """Even from LOCAL_APP, GET /install/<key>/progress must
        refuse if no install was POSTed. The previous shape made the
        GET handler the operation starter; the fix turns it into a
        progress-only consumer."""
        from bpp.web import bp_install

        bp_install._claim = None  # no POST has run

        client = app.test_client()
        ctx = app.extensions["bpp"]
        r = client.get(
            "/api/v1/install/faces/progress",
            headers={"X-Auth-Token": ctx.auth_token},
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
        assert r.status_code == 409, (
            f"GET progress without prior POST should 409, got {r.status_code} {r.data}"
        )


# ─── Filesystem path filtering for LAN clients ──────────────
