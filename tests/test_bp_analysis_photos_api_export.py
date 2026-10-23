"""Tests for bp_analysis and bp_photos blueprints (uncovered endpoints) — part 2 of 3:
TestApiExport, TestApiExportEnrichedResponse, TestApiOpenFolder."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import os
from unittest.mock import patch

from tests._bp_analysis_photos_shared import (  # noqa: F401
    _real,
    _suppress_config,
    app_with_photos,
    bare_app,
)


class TestApiExport:
    """POST /api/export"""

    def test_export_no_analysis_returns_404(self, bare_app):
        app, d = bare_app
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/export",
                json={"outdir": os.path.join(d, "x")},
            )
            assert resp.status_code == 404

    def test_export_missing_outdir_returns_400(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            c.get("/api/v1/photos")
            resp = c.post("/api/v1/export", json={})
            assert resp.status_code == 400
            assert "outdir" in resp.get_json()["error"]

    def test_export_no_selected_returns_400(self, app_with_photos):
        app, d = app_with_photos
        outdir = os.path.join(d, "export_out")
        with app.test_client() as c:
            c.get("/api/v1/photos")
            resp = c.post(
                "/api/v1/export",
                json={"outdir": outdir, "selected_paths": []},
            )
            assert resp.status_code == 400
            assert "No photos" in resp.get_json()["error"]

    def test_export_path_traversal_returns_400(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            c.get("/api/v1/photos")
            resp = c.post(
                "/api/v1/export",
                json={
                    "outdir": "/etc/shadow_export",
                    "selected_paths": ["/fake/path"],
                },
            )
            assert resp.status_code == 400
            assert "outside" in resp.get_json()["error"]

    def test_export_successful(self, app_with_photos):
        app, d = app_with_photos
        outdir = os.path.join(d, "exported")
        photo_path = os.path.join(d, "photo_0.jpg")
        with app.test_client() as c:
            c.get("/api/v1/photos")
            resp = c.post(
                "/api/v1/export",
                json={
                    "outdir": outdir,
                    "selected_paths": [photo_path],
                    "gallery": False,
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "exported"
            assert data["count"] == 1
            assert os.path.isdir(outdir)

    def test_export_into_existing_outdir_merges(self, app_with_photos):
        """An existing destination must be merged into, not wiped.
        Files the user already had in the folder must survive."""
        app, d = app_with_photos
        outdir = os.path.join(d, "existing_export")
        os.makedirs(outdir)
        sentinel = os.path.join(outdir, "user_note.txt")
        with open(sentinel, "w") as f:
            f.write("user data")
        photo_path = os.path.join(d, "photo_0.jpg")
        with app.test_client() as c:
            c.get("/api/v1/photos")
            resp = c.post(
                "/api/v1/export",
                json={
                    "outdir": outdir,
                    "selected_paths": [photo_path],
                    "gallery": False,
                },
            )
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "exported"
            assert os.path.isfile(sentinel)
            with open(sentinel) as f:
                assert f.read() == "user data"

    def test_export_general_failure_returns_500(self, app_with_photos):
        app, d = app_with_photos
        outdir = os.path.join(d, "fail_export")
        photo_path = os.path.join(d, "photo_0.jpg")
        with app.test_client() as c:
            c.get("/api/v1/photos")
            with patch(
                "bpp.web.bp_export.export_selected",
                side_effect=RuntimeError("disk full"),
            ):
                resp = c.post(
                    "/api/v1/export",
                    json={
                        "outdir": outdir,
                        "selected_paths": [photo_path],
                    },
                )
                assert resp.status_code == 500
                err = resp.get_json()["error"].lower()
                assert "failed" in err


class TestApiExportEnrichedResponse:
    """Export API should return failed count in response."""

    def test_export_response_includes_failed_count(self, app_with_photos):
        app, d = app_with_photos
        outdir = os.path.join(d, "exported_enriched")
        photo_path = os.path.join(d, "photo_0.jpg")
        with app.test_client() as c:
            c.get("/api/v1/photos")
            resp = c.post(
                "/api/v1/export",
                json={
                    "outdir": outdir,
                    "selected_paths": [photo_path],
                    "gallery": False,
                },
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert "failed" in data
            assert data["failed"] == 0

    def test_export_partial_failure_reports_counts(self, app_with_photos):
        from bpp.output.export import ExportResult

        app, d = app_with_photos
        outdir = os.path.join(d, "partial_fail")
        photo_path = os.path.join(d, "photo_0.jpg")
        with app.test_client() as c:
            c.get("/api/v1/photos")
            # A disk-error fix changed export_selected's return
            # type from a plain tuple to ExportResult so the UI can
            # categorise disk_error. Patch with the new shape; the
            # endpoint reads .exported / .failed by name now.
            with patch(
                "bpp.web.bp_export.export_selected",
                return_value=ExportResult(exported=1, failed=2),
            ):
                resp = c.post(
                    "/api/v1/export",
                    json={
                        "outdir": outdir,
                        "selected_paths": [photo_path],
                        "gallery": False,
                    },
                )
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["count"] == 1
                assert data["failed"] == 2
                # disk_error stays None on non-fatal partial failure.
                assert data.get("disk_error") is None


class TestApiOpenFolder:
    """POST /api/open-folder"""

    def test_open_folder_missing_path_returns_400(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/open-folder", json={})
            assert resp.status_code == 400

    def test_open_folder_nonexistent_returns_404(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/open-folder",
                json={"path": "/nonexistent/dir"},
            )
            assert resp.status_code == 404

    def test_open_folder_success(self, app_with_photos, monkeypatch):
        import subprocess as _sp

        app, d = app_with_photos
        # Force a non-None launcher argv — CI Linux runners have no
        # DISPLAY, so the real `_open_folder_cmd` returns None and the
        # endpoint short-circuits to 501 before the subprocess mock runs.
        # `echo` is on PATH on both macOS and Linux — endpoint runs
        # shutil.which() before subprocess.run, so a real binary name
        # is required even though run is mocked.
        monkeypatch.setattr(
            "bpp.web.bp_os_integration._open_folder_cmd",
            lambda path: ["echo", path],
        )
        with app.test_client() as c, patch("bpp.web.bp_os_integration.subprocess") as mock_sub:
            # Endpoint switched from Popen (fire-and-forget) to run() with
            # returncode check — see _launch_os_handler. Provide a
            # successful CompletedProcess.
            mock_sub.run.return_value = _sp.CompletedProcess(args=[], returncode=0)
            resp = c.post(
                "/api/v1/open-folder",
                json={"path": d},
            )
            assert resp.status_code == 200
            mock_sub.run.assert_called_once()


class TestApiOverride:
    """POST /api/override"""

    def test_override_missing_filepath_returns_400(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post("/api/v1/override", json={})
            assert resp.status_code == 400
            assert "filepath" in resp.get_json()["error"]

    def test_override_unknown_photo_returns_404(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/override",
                json={"filepath": "/no/such/photo.jpg"},
            )
            assert resp.status_code == 404

    def test_override_include(self, app_with_photos):
        app, d = app_with_photos
        fp = os.path.join(d, "photo_0.jpg")
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/override",
                json={"filepath": fp, "mode": "include"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert "feedback_recorded" in data

    def test_override_exclude(self, app_with_photos):
        app, d = app_with_photos
        fp = os.path.join(d, "photo_1.jpg")
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/override",
                json={"filepath": fp, "mode": "exclude"},
            )
            assert resp.status_code == 200

    def test_override_clear(self, app_with_photos):
        app, d = app_with_photos
        fp = os.path.join(d, "photo_2.jpg")
        with app.test_client() as c:
            c.post(
                "/api/v1/override",
                json={"filepath": fp, "mode": "include"},
            )
            resp = c.post(
                "/api/v1/override",
                json={"filepath": fp, "mode": None},
            )
            assert resp.status_code == 200


class TestApiFavorite:
    """POST /api/favorite"""

    def test_favorite_missing_filepath_returns_400(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post("/api/v1/favorite", json={})
            assert resp.status_code == 400
            assert "filepath" in resp.get_json()["error"]

    def test_favorite_unknown_photo_returns_404(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/favorite",
                json={"filepath": "/no/such/photo.jpg"},
            )
            assert resp.status_code == 404

    def test_favorite_toggle_on(self, app_with_photos):
        app, d = app_with_photos
        fp = os.path.join(d, "photo_0.jpg")
        with app.test_client() as c:
            resp = c.post("/api/v1/favorite", json={"filepath": fp})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert data["favorite"] is True

    def test_favorite_toggle_off(self, app_with_photos):
        app, d = app_with_photos
        fp = os.path.join(d, "photo_0.jpg")
        with app.test_client() as c:
            c.post("/api/v1/favorite", json={"filepath": fp})
            resp = c.post("/api/v1/favorite", json={"filepath": fp})
            assert resp.status_code == 200
            assert resp.get_json()["favorite"] is False


class TestApiOverrides:
    """GET /api/overrides"""

    def test_overrides_empty(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/overrides")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["overrides"] == {}
            assert data["favorites"] == []

    def test_overrides_after_set(self, app_with_photos):
        app, d = app_with_photos
        fp0 = os.path.join(d, "photo_0.jpg")
        fp1 = os.path.join(d, "photo_1.jpg")
        with app.test_client() as c:
            c.post(
                "/api/v1/override",
                json={"filepath": fp0, "mode": "include"},
            )
            c.post("/api/v1/favorite", json={"filepath": fp1})
            resp = c.get("/api/v1/overrides")
            data = resp.get_json()
            assert data["overrides"][fp0] == "include"
            assert fp1 in data["favorites"]


class TestApiPhotosDeleted:
    """GET /api/photos/deleted"""

    def test_deleted_empty(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/photos/deleted")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total"] == 0
            assert data["photos"] == []
            assert data["limit"] == 200
            assert data["offset"] == 0

    def test_deleted_after_soft_delete(self, app_with_photos):
        app, d = app_with_photos
        fp = os.path.join(d, "photo_0.jpg")
        with app.test_client() as c:
            c.post(
                "/api/v1/photos/delete",
                json={"filepaths": [fp]},
            )
            resp = c.get("/api/v1/photos/deleted")
            data = resp.get_json()
            assert data["total"] == 1
            assert data["photos"][0]["filepath"] == fp
            assert "deleted_at" in data["photos"][0]

    def test_deleted_pagination(self, app_with_photos):
        """Pagination contract: ?limit=N&offset=M returns at most N rows,
        ``total`` reflects the full row count regardless of the page."""
        app, d = app_with_photos
        # Fixture seeds 3 photos. Soft-delete all of them so we can page.
        fps = [os.path.join(d, f"photo_{i}.jpg") for i in range(3)]
        with app.test_client() as c:
            c.post("/api/v1/photos/delete", json={"filepaths": fps})

            # limit=2, offset=0 → first 2 (most-recently-deleted first)
            page1 = c.get("/api/v1/photos/deleted?limit=2&offset=0").get_json()
            assert page1["total"] == 3
            assert page1["limit"] == 2
            assert page1["offset"] == 0
            assert len(page1["photos"]) == 2

            # limit=2, offset=2 → just the tail (1 remaining)
            page2 = c.get("/api/v1/photos/deleted?limit=2&offset=2").get_json()
            assert page2["total"] == 3
            assert len(page2["photos"]) == 1
            # No overlap with page1
            page1_paths = {p["filepath"] for p in page1["photos"]}
            page2_paths = {p["filepath"] for p in page2["photos"]}
            assert not (page1_paths & page2_paths)

            # offset past end → empty page, total still correct
            tail = c.get("/api/v1/photos/deleted?limit=10&offset=100").get_json()
            assert tail["total"] == 3
            assert tail["photos"] == []


class TestApiBatchOverride:
    """POST /api/batch/override"""

    def test_batch_override_empty_filepaths_returns_400(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/batch/override",
                json={"filepaths": [], "mode": "include"},
            )
            assert resp.status_code == 400

    def test_batch_override_success(self, app_with_photos):
        app, d = app_with_photos
        fps = [os.path.join(d, f"photo_{i}.jpg") for i in range(2)]
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/batch/override",
                json={"filepaths": fps, "mode": "exclude"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert data["count"] == 2
