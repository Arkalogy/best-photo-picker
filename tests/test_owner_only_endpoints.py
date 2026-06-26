"""Owner-only endpoints must reject paired LAN devices.

Background: `_require_local_app()` in `bpp/web/bp_core.py` blocks
non-LOCAL_APP principals from owner-mutating endpoints (device
approve/revoke). The audit caught three high-value endpoints that
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

import os

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limit_buckets():
    """The destructive-endpoint rate limiter is global module state
    (`bpp.web.share._DESTRUCTIVE_BUCKETS`). Without resetting between
    tests, a parametrized sweep over 60+ POST cases trips the
    60 req/min cap and subsequent tests get 429 instead of the
    expected 403."""
    from bpp.web.share import _reset_pair_request_buckets_for_tests

    _reset_pair_request_buckets_for_tests()
    yield
    _reset_pair_request_buckets_for_tests()


@pytest.fixture
def app(tmp_path):
    """Fresh Flask app with the auth middleware active."""
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = False
    return app


@pytest.fixture
def lan_device(app):
    """Approve a LAN device (PRINCIPAL_LAN_DEVICE) and return
    (share_token, fingerprint_cookie). Caller uses these to make
    requests as that paired-but-not-owner device."""
    from bpp.web.share import (
        approve_device,
        find_or_create_pending_device,
        get_share_token,
        set_lan_sharing_enabled,
    )

    ctx = app.extensions["bpp"]
    with app.app_context():
        set_lan_sharing_enabled(ctx.get_conn(), True)
        token = get_share_token(ctx.get_conn())
        d = find_or_create_pending_device(ctx.get_conn(), "fp-lan-1", "Phone", "192.168.1.50")
        approve_device(ctx.get_conn(), d["id"])
    return token, "fp-lan-1"


def _as_loopback(app, method, path, **kwargs):
    """Make a request as the local owner (LOCAL_APP principal)."""
    ctx = app.extensions["bpp"]
    client = app.test_client()
    headers = kwargs.pop("headers", {}) or {}
    headers["X-Auth-Token"] = ctx.auth_token
    return client.open(
        path,
        method=method,
        headers=headers,
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        **kwargs,
    )


# ─── /api/settings PUT ─────────────────────────────────────────────


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
        r = _as_loopback(app, "PUT", "/api/v1/settings", json={"theme": "dark"})
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
        # R11-M2: bound_host defaults to loopback now; pretend the
        # server bound a LAN interface so the restart-required gate
        # doesn't intercept this owner-allowed test.
        app.extensions["bpp"].bound_host = "0.0.0.0"
        r = _as_loopback(app, "POST", "/api/v1/share/toggle", json={"enabled": True})
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
        r = _as_loopback(app, "POST", "/api/v1/share/revoke")
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

        r = _as_loopback(app, "GET", "/api/v1/settings")
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
        """D-08: defense in depth on top of the denylist. A future
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
                f"Token-shaped key {key!r} leaked to LAN — D-08 pattern filter broken"
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
        r = _as_loopback(app, "GET", "/api/v1/share/devices")
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


# ─── Admin/install endpoints owner-only (D-01) ─────────────────────


class TestAdminEndpointsOwnerOnly:
    """A paired LAN device must not trigger admin operations:
    pip install (most consequential network op in the app), model
    redownload/uninstall (cache invalidation + bandwidth DoS), model
    toggles (could disable owner-mandated detection like NSFW
    filter), or library wipe (most destructive endpoint).

    Codex D-01: these were missed in the initial @requires_local_app
    sweep. The fix audit-grepped for `@bp.post|put|delete` decorators
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
        """Codex D-07 fold-in: GET /install/<key>/progress runs the
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
        from bpp.web import bp_models

        # Force `_install_running` to False (no POST has run)
        bp_models._install_running = False

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


# ─── Filesystem path filtering for LAN clients (D-05) ──────────────


class TestPathFilteringForLAN:
    """D-05: read-only health/status endpoints used to leak absolute
    filesystem paths (DB path, library_path, batch folder names)
    to authenticated LAN devices. A paired phone could learn the
    owner's username, drive layout, library location.

    The fix doesn't lock the endpoints owner-only — LAN clients
    legitimately need the boolean health/import flags. Instead,
    path-shaped fields are filtered for non-LOCAL_APP principals.
    """

    def test_health_owner_sees_paths(self, app):
        r = _as_loopback(app, "GET", "/api/v1/health")
        assert r.status_code == 200
        data = r.get_json()
        # Owner sees the DB path (it's there for diagnostic / support
        # workflows). Health response shape is {status, checks: {...}}.
        assert "path" in data["checks"]["db"], "LOCAL_APP must see DB path"

    def test_health_lan_does_not_see_db_path(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/health",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 200, r.data
        data = r.get_json()
        checks = data["checks"]
        assert "path" not in checks["db"], (
            "LAN device must not see absolute DB path — D-05 filter broken"
        )
        # And the storage block's path is also gone
        assert "path" not in checks["storage"], "LAN must not see library_path"
        # But health flags are still present (otherwise we broke the endpoint)
        assert "ok" in checks["db"]
        assert "accessible" in checks["storage"] or "error" in checks["storage"]

    def test_library_status_lan_does_not_see_library_path(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/library/status",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 200
        data = r.get_json()
        assert "library_path" not in data
        assert "batches" not in data
        # LAN-relevant fields still there
        assert "exists" in data
        assert "importing" in data

    def test_library_status_owner_sees_library_path(self, app):
        r = _as_loopback(app, "GET", "/api/v1/library/status")
        data = r.get_json()
        assert "library_path" in data
        assert "batches" in data

    def test_storage_health_lan_no_path(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/health/storage",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        data = r.get_json()
        # Path field should not appear in LAN response (current
        # check_storage_accessible doesn't return one, but the filter
        # future-proofs against new fields slipping in)
        assert "path" not in data


# ─── Library / analyze / import endpoints owner-only (R4-H1) ───────


class TestLibraryAndIngestOwnerOnly:
    """R4-H1: library registry mutation, library switching, analyze,
    and import are admin/host operations. A paired LAN device must
    not be able to:
      - register or remove libraries (registry mutation)
      - rename or move library folders on disk
      - redirect the active server to a different library
      - start an analyze run against an arbitrary host folder
      - start an import from a host filesystem path

    These were missed in the D-01 sweep."""

    def _lan_request(self, app, lan_device, method, path, **kw):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        return client.open(
            path,
            method=method,
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
            **kw,
        )

    def test_libraries_add_blocks_lan(self, app, lan_device):
        r = self._lan_request(app, lan_device, "POST", "/api/v1/libraries", json={"path": "/tmp/x"})
        assert r.status_code == 403

    def test_libraries_remove_blocks_lan(self, app, lan_device):
        r = self._lan_request(
            app, lan_device, "DELETE", "/api/v1/libraries", json={"path": "/tmp/x"}
        )
        assert r.status_code == 403

    def test_libraries_rename_blocks_lan(self, app, lan_device):
        r = self._lan_request(
            app,
            lan_device,
            "PUT",
            "/api/v1/libraries/rename",
            json={"path": "/tmp/x", "name": "NewName"},
        )
        assert r.status_code == 403

    def test_libraries_switch_blocks_lan(self, app, lan_device):
        r = self._lan_request(
            app, lan_device, "POST", "/api/v1/libraries/switch", json={"path": "/tmp/x"}
        )
        assert r.status_code == 403

    def test_analyze_blocks_lan(self, app, lan_device):
        r = self._lan_request(
            app, lan_device, "POST", "/api/v1/analyze", json={"input_dir": "/etc"}
        )
        assert r.status_code == 403

    def test_import_blocks_lan(self, app, lan_device):
        r = self._lan_request(
            app, lan_device, "POST", "/api/v1/import", json={"source_dir": "/etc"}
        )
        assert r.status_code == 403


# ─── Native host UI endpoints owner-only (R4-H2) ───────────────────


class TestHostUIOwnerOnly:
    """R4-H2: endpoints that spawn native host processes / OS file
    manager UI. Even a paired LAN device must not be able to pop
    UI on the owner's desktop or drive Finder/Explorer."""

    def _lan_request(self, app, lan_device, method, path, **kw):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        return client.open(
            path,
            method=method,
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
            **kw,
        )

    def test_pick_blocks_lan(self, app, lan_device):
        r = self._lan_request(app, lan_device, "POST", "/api/v1/pick", json={"mode": "folder"})
        assert r.status_code == 403, "LAN device must not pop a native picker on the host"

    def test_open_folder_blocks_lan(self, app, lan_device):
        r = self._lan_request(
            app, lan_device, "POST", "/api/v1/open-folder", json={"path": "/Users"}
        )
        assert r.status_code == 403

    def test_reveal_file_blocks_lan(self, app, lan_device):
        r = self._lan_request(
            app, lan_device, "POST", "/api/v1/reveal-file", json={"filepath": "/tmp/x.jpg"}
        )
        assert r.status_code == 403


# ─── R4-M2: status / library registry path leaks ──────────────────


class TestStatusAndRegistryPathLeaks:
    """R4-M2: D-05 filtered /api/v1/health and /api/v1/library/status,
    but missed three more endpoints that leak owner filesystem state
    to LAN clients:
      - /api/v1/status: workdir, input_dir, library_path
      - /api/v1/libraries: full registry of paths
      - /api/v1/libraries/active: active library absolute path

    Fix: filter the path fields on /api/v1/status; make the library
    registry endpoints owner-only outright (admin/config state, no
    LAN use case).
    """

    def test_status_owner_sees_paths(self, app):
        r = _as_loopback(app, "GET", "/api/v1/status")
        assert r.status_code == 200
        data = r.get_json()
        # Owner sees the filesystem fields
        assert "workdir" in data
        assert "input_dir" in data
        assert "library_path" in data

    def test_status_lan_does_not_see_paths(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/status",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 200, r.data
        data = r.get_json()
        for key in ("workdir", "input_dir", "library_path"):
            assert key not in data, (
                f"LAN device must not see filesystem path {key!r} on /api/v1/status"
            )
        # But health/availability flags are still there (didn't break the SPA)
        assert "image_count" in data
        assert "first_run" in data
        assert "face_recognition_available" in data

    def test_libraries_list_blocks_lan(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/libraries",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403, "Library registry is admin/config state — owner only"

    def test_libraries_active_blocks_lan(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/libraries/active",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403

    def test_libraries_owner_still_works(self, app):
        r = _as_loopback(app, "GET", "/api/v1/libraries")
        assert r.status_code == 200
        r2 = _as_loopback(app, "GET", "/api/v1/libraries/active")
        assert r2.status_code == 200


# ─── R5-H2: more host-mutation endpoints owner-only ────────────────


class TestMoreAdminEndpointsOwnerOnly:
    """R5-H2: the R4-H1/H2 sweep was incomplete. The pattern is
    'filesystem mutation, host process, model state, registry
    mutation, audit-log erasure = LOCAL_APP-only.' These endpoints
    fall into that bucket but were missed:

      POST   /api/v1/export             — writes to host filesystem
      POST   /api/v1/video/trim         — ffmpeg subprocess + overwrites
                                          original video file
      POST   /api/v1/batch/rename/apply — physical disk renames
      POST   /api/v1/thumbnails/clear   — destructive cache wipe
      DELETE /api/v1/analysis-cache     — destructive cache wipe
      GET    /api/v1/logs               — leaks paths/state
      POST   /api/v1/logs/clear         — wipes audit/diagnostic trail
    """

    def _lan_request(self, app, lan_device, method, path, **kw):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        return client.open(
            path,
            method=method,
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
            **kw,
        )

    def test_export_blocks_lan(self, app, lan_device):
        r = self._lan_request(
            app,
            lan_device,
            "POST",
            "/api/v1/export",
            json={"outdir": "/tmp/out", "selected_paths": []},
        )
        assert r.status_code == 403

    def test_video_trim_blocks_lan(self, app, lan_device):
        r = self._lan_request(
            app,
            lan_device,
            "POST",
            "/api/v1/video/trim",
            json={"filepath": "/tmp/v.mp4", "start": 0, "end": 1},
        )
        assert r.status_code == 403

    def test_batch_rename_apply_blocks_lan(self, app, lan_device):
        r = self._lan_request(
            app,
            lan_device,
            "POST",
            "/api/v1/batch/rename/apply",
            json={"mapping": []},
        )
        assert r.status_code == 403

    def test_thumbnails_clear_blocks_lan(self, app, lan_device):
        r = self._lan_request(app, lan_device, "POST", "/api/v1/thumbnails/clear")
        assert r.status_code == 403

    def test_analysis_cache_clear_blocks_lan(self, app, lan_device):
        r = self._lan_request(app, lan_device, "DELETE", "/api/v1/analysis-cache")
        assert r.status_code == 403

    def test_logs_get_blocks_lan(self, app, lan_device):
        r = self._lan_request(app, lan_device, "GET", "/api/v1/logs")
        assert r.status_code == 403

    def test_logs_clear_blocks_lan(self, app, lan_device):
        r = self._lan_request(app, lan_device, "POST", "/api/v1/logs/clear")
        assert r.status_code == 403


# ─── Round-9 sweep ───────────────────────────────────────────────────


class TestRound9HostMutationsBlocked:
    """Round-9 audit: ~55 host-mutation endpoints across 11 blueprints
    were missing `@requires_local_app`. The CSP-style closed-list
    invariant ("every mutation gated") had drifted away from reality.
    These tests sweep the new gates so a future endpoint that forgets
    the decorator gets caught at PR time, not in another audit round.

    Each endpoint asserts a 403 from a paired LAN device. Some
    endpoints validate input shape and short-circuit to 400 if the
    body is bad — but the decorator stack runs `@requires_local_app`
    BEFORE `@validate_json`, so a non-LOCAL_APP request always sees
    the 403 first."""

    def _lan_request(self, app, lan_device, method, path, **kw):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        return client.open(
            path,
            method=method,
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
            **kw,
        )

    @pytest.mark.parametrize(
        "method,path,body",
        [
            # bp_albums (11)
            ("POST", "/api/v1/albums", {"name": "x"}),
            ("PUT", "/api/v1/albums/1", {"name": "y"}),
            ("DELETE", "/api/v1/albums/1", None),
            ("POST", "/api/v1/albums/1/recompute", {}),
            ("POST", "/api/v1/albums/1/override", {"filepath": "/x", "value": 1}),
            ("POST", "/api/v1/albums/1/favorite", {"filepath": "/x"}),
            ("POST", "/api/v1/albums/1/batch/override", {"filepaths": [], "value": 1}),
            ("POST", "/api/v1/albums/1/batch/favorite", {"filepaths": []}),
            ("POST", "/api/v1/albums/1/add-photos", {"filepaths": []}),
            ("POST", "/api/v1/albums/1/remove-photos", {"filepaths": []}),
            ("POST", "/api/v1/albums/refresh-smart", {}),
            # bp_tags (7)
            ("POST", "/api/v1/tags", {"name": "x"}),
            ("PUT", "/api/v1/tags/1", {"name": "y"}),
            ("DELETE", "/api/v1/tags/1", None),
            ("POST", "/api/v1/photos/1/tags", {"tag_id": 1}),
            ("DELETE", "/api/v1/photos/1/tags/1", None),
            ("POST", "/api/v1/tags/batch", {"tag_id": 1, "filepaths": []}),
            ("POST", "/api/v1/tags/batch/remove", {"tag_id": 1, "filepaths": []}),
            # bp_faces_manage (10)
            ("POST", "/api/v1/faces/merge", {}),
            ("POST", "/api/v1/faces/dismiss", {}),
            ("POST", "/api/v1/faces/split", {}),
            ("POST", "/api/v1/faces/restore", {}),
            ("POST", "/api/v1/faces/recluster", {}),
            ("POST", "/api/v1/faces/tag", {}),
            ("DELETE", "/api/v1/faces/tag", None),
            ("POST", "/api/v1/faces/reassign", {}),
            ("POST", "/api/v1/clip/extract", {}),
            ("DELETE", "/api/v1/faces/purge", None),
            # bp_faces_review (2)
            ("POST", "/api/v1/faces/review-pairs/verdict", {}),
            ("POST", "/api/v1/faces/review-pairs/verdict/undo", {}),
            # bp_pets (3)
            ("POST", "/api/v1/pets/split", {}),
            ("POST", "/api/v1/pets/merge", {}),
            ("POST", "/api/v1/pets/dismiss", {}),
            # bp_photos (6)
            ("POST", "/api/v1/recompute", {}),
            ("POST", "/api/v1/optimize", {}),
            ("POST", "/api/v1/override", {"filepath": "/x"}),
            ("POST", "/api/v1/favorite", {"filepath": "/x"}),
            ("POST", "/api/v1/batch/override", {"filepaths": []}),
            ("POST", "/api/v1/batch/favorite", {"filepaths": []}),
            # bp_core (3)
            ("POST", "/api/v1/photos/recheck-missing", {}),
            ("POST", "/api/v1/presets", {"name": "x", "config": {}}),
            ("DELETE", "/api/v1/presets/x", None),
            # bp_memories (1)
            ("POST", "/api/v1/memories/refresh", {}),
            # bp_analysis (3)
            ("POST", "/api/v1/analyze/cancel", {}),
            ("POST", "/api/v1/import/cancel", {}),
            ("POST", "/api/v1/compute-hashes", {}),
            # bp_photos_manage (11) — api_photos_delete / restore are
            # already exercised by the @validate_json migration tests,
            # but pin them here so the decorator stack stays right.
            ("POST", "/api/v1/photos/delete", {"filepaths": []}),
            ("POST", "/api/v1/photos/restore", {"filepaths": []}),
            ("POST", "/api/v1/photos/delete-permanent", {}),
            ("POST", "/api/v1/photos/hide", {}),
            ("POST", "/api/v1/photos/unhide", {}),
            ("POST", "/api/v1/photos/enhance", {}),
            ("POST", "/api/v1/photos/reset-edits", {}),
            ("POST", "/api/v1/photos/save-edits", {}),
            ("POST", "/api/v1/photos/1/date", {}),
            ("POST", "/api/v1/photos/1/inpaint", {}),
            ("GET", "/api/v1/photos/1/auto_straighten", None),
        ],
    )
    def test_endpoint_blocks_lan_device(self, app, lan_device, method, path, body):
        kw = {"json": body} if body is not None else {}
        r = self._lan_request(app, lan_device, method, path, **kw)
        assert r.status_code == 403, (
            f"{method} {path} should reject LAN device (got {r.status_code} {r.data!r})"
        )


# ─── R10-H1: auth must run BEFORE @with_face_lock ────────────────────


class TestFaceLockNotAcquiredOnAuthFailure:
    """R10-H1: face-mutating endpoints stack `@requires_local_app` and
    `@with_face_lock`. Decorators apply bottom-up so the order
    matters: pre-fix the inner decorator was `@requires_local_app`
    and the outer was `@with_face_lock`, meaning the lock was
    acquired BEFORE the auth check. A paired LAN device's request
    would 403, but only after parking on `ctx.face_op_lock` —
    spamming the endpoint from a malicious phone could DoS the owner
    by holding the lock against legitimate clustering / merge
    operations.

    Post-fix: `@requires_local_app` is the outer decorator. The
    auth gate returns 403 before `with_face_lock` ever attempts
    `ctx.face_op_lock.__enter__`. This test pins the contract by
    counting lock acquisitions during a non-LOCAL_APP request."""

    @pytest.fixture
    def counting_lock(self, app):
        """Replace ctx.face_op_lock with an instrumented lock that
        counts every __enter__. Restores the original on teardown."""
        ctx = app.extensions["bpp"]
        original = ctx.face_op_lock
        acquired = {"count": 0}

        class _CountingLock:
            def __enter__(self):
                acquired["count"] += 1
                return self

            def __exit__(self, *exc):
                return False

        ctx.face_op_lock = _CountingLock()
        try:
            yield acquired
        finally:
            ctx.face_op_lock = original

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("POST", "/api/v1/faces/merge", {}),
            ("POST", "/api/v1/faces/dismiss", {}),
            ("POST", "/api/v1/faces/split", {}),
            ("POST", "/api/v1/faces/restore", {}),
            ("POST", "/api/v1/faces/recluster", {}),
            ("POST", "/api/v1/faces/tag", {}),
            ("DELETE", "/api/v1/faces/tag", None),
            ("POST", "/api/v1/faces/reassign", {}),
            ("DELETE", "/api/v1/faces/purge", {"confirm": "purge"}),
            ("POST", "/api/v1/faces/review-pairs/verdict", {}),
        ],
    )
    def test_lan_request_does_not_acquire_face_lock(
        self, app, lan_device, counting_lock, method, path, body
    ):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        kw = {"json": body} if body is not None else {}
        r = client.open(
            path,
            method=method,
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
            **kw,
        )
        assert r.status_code == 403, (
            f"{method} {path} should reject LAN device (got {r.status_code} {r.data!r})"
        )
        assert counting_lock["count"] == 0, (
            f"{method} {path} acquired ctx.face_op_lock "
            f"{counting_lock['count']} times BEFORE auth rejected — "
            "decorator order regressed; @requires_local_app must be "
            "outermost so a non-LOCAL_APP request never enters the "
            "lock."
        )


def test_face_endpoints_auth_decorator_is_outermost():
    """Source-scan complement to the runtime test above: every
    handler in bp_faces_manage.py / bp_faces_review.py that stacks
    `@with_face_lock` and `@requires_local_app` must list them in
    that order (auth outer, lock inner). Catches a future commit
    that swaps them by reflex."""
    from pathlib import Path

    for rel in ("bpp/web/bp_faces_manage.py", "bpp/web/bp_faces_review.py"):
        text = Path(rel).read_text()
        # `@with_face_lock\n@requires_local_app` is the BAD order
        # (auth inner, lock outer). Confirm zero occurrences.
        assert "@with_face_lock\n@requires_local_app" not in text, (
            f"{rel} has @with_face_lock above @requires_local_app — "
            "decorators apply bottom-up, so the lock is acquired "
            "BEFORE the auth check. Swap the order."
        )


# ─── /api/share/info GET ───────────────────────────────────────────


class TestShareInfoOwnerOnly:
    def test_lan_device_blocked(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/share/info",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403, f"share/info should reject LAN device (got {r.status_code})"

    def test_loopback_owner_allowed(self, app):
        r = _as_loopback(app, "GET", "/api/v1/share/info")
        assert r.status_code == 200


# ─── /api/share/qr GET ────────────────────────────────────────────


class TestShareQrOwnerOnly:
    def test_lan_device_blocked(self, app, lan_device):
        client = app.test_client()
        token, fp = lan_device
        client.set_cookie("bpp_share_fp", fp)
        r = client.get(
            "/api/v1/share/qr",
            headers={"X-Auth-Token": token},
            environ_overrides={"REMOTE_ADDR": "192.168.1.50"},
        )
        assert r.status_code == 403, f"share/qr should reject LAN device (got {r.status_code})"

    def test_loopback_owner_allowed(self, app):
        # QR returns 404 when LAN sharing is off; 200/404 both fine from owner
        r = _as_loopback(app, "GET", "/api/v1/share/qr")
        assert r.status_code in (200, 404), f"got {r.status_code}"
