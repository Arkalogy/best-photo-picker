"""Owner-only endpoints, continued: path filtering for paired devices,
library/ingest, host UI, status leaks and more admin endpoints (split
from test_owner_only_endpoints.py; same TESTING=False real-auth setup)."""

from __future__ import annotations

from tests.owner_only._owner import as_loopback


class TestPathFilteringForLAN:
    """Read-only health/status endpoints used to leak absolute
    filesystem paths (DB path, library_path, batch folder names)
    to authenticated LAN devices. A paired phone could learn the
    owner's username, drive layout, library location.

    The fix doesn't lock the endpoints owner-only — LAN clients
    legitimately need the boolean health/import flags. Instead,
    path-shaped fields are filtered for non-LOCAL_APP principals.
    """

    def test_health_owner_sees_paths(self, app):
        r = as_loopback(app, "GET", "/api/v1/health")
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
            "LAN device must not see absolute DB path — path filter broken"
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
        r = as_loopback(app, "GET", "/api/v1/library/status")
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


# ─── Library / analyze / import endpoints owner-only ───────


class TestLibraryAndIngestOwnerOnly:
    """Library registry mutation, library switching, analyze,
    and import are admin/host operations. A paired LAN device must
    not be able to:
      - register or remove libraries (registry mutation)
      - rename or move library folders on disk
      - redirect the active server to a different library
      - start an analyze run against an arbitrary host folder
      - start an import from a host filesystem path

    These were missed in the first owner-only sweep."""

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


# ─── Native host UI endpoints owner-only ───────────────────


class TestHostUIOwnerOnly:
    """Endpoints that spawn native host processes / OS file
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


# ─── Status / library registry path leaks ──────────────────


class TestStatusAndRegistryPathLeaks:
    """The path filter covered /api/v1/health and /api/v1/library/status,
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
        r = as_loopback(app, "GET", "/api/v1/status")
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
        r = as_loopback(app, "GET", "/api/v1/libraries")
        assert r.status_code == 200
        r2 = as_loopback(app, "GET", "/api/v1/libraries/active")
        assert r2.status_code == 200


# ─── More host-mutation endpoints owner-only ────────────────


class TestMoreAdminEndpointsOwnerOnly:
    """The earlier owner-only sweeps were incomplete. The pattern is
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


# ─── Host-mutation sweep ───────────────────────────────────────────────────
