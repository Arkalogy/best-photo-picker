"""Tests for bp_analysis and bp_photos blueprints (uncovered endpoints)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from PIL import Image

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _real(p):
    """Resolve macOS /var -> /private/var symlink."""
    return os.path.realpath(str(p))


@pytest.fixture()
def _suppress_config(tmp_path, monkeypatch):
    """Prevent importing user presets from ~/.config during tests."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture()
def bare_app(tmp_path, _suppress_config):
    """App with workdir/library but NO photos in DB."""
    from bpp.web.app import create_app

    d = _real(tmp_path)
    app = create_app(workdir=d, input_dir=d, library_path=d)
    app.config["TESTING"] = True
    return app, d


@pytest.fixture()
def app_with_photos(tmp_path, _suppress_config):
    """App seeded with 3 analysed photos (real JPEG files)."""
    d = _real(tmp_path)
    for i in range(3):
        p = os.path.join(d, f"photo_{i}.jpg")
        Image.new("RGB", (100, 100), "blue").save(p, "JPEG")

    from bpp.web.app import create_app

    app = create_app(workdir=d, input_dir=d, library_path=d)
    app.config["TESTING"] = True

    with app.app_context():
        from bpp.db.albums import sync_all_photos_album
        from bpp.db.photos import upsert_photo
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()
        for i in range(3):
            upsert_photo(
                conn,
                {
                    "filepath": os.path.join(d, f"photo_{i}.jpg"),
                    "aggregate_score": 0.5 + i * 0.1,
                    "blur_score": 0.7,
                    "blur_raw": 100.0,
                    "exposure_score": 0.8,
                    "face_score": 0.3,
                    "composition_score": 0.6,
                    "date": f"2024-01-{i + 1:02d}T12:00:00",
                    "date_day": f"2024-01-{i + 1:02d}",
                    "date_month": "2024-01",
                },
            )
        sync_all_photos_album(conn)
        ctx.invalidate_analysis()

    return app, d


# ===================================================================
# bp_analysis tests
# ===================================================================


class TestApiAnalyze:
    """POST /api/analyze"""

    def test_invalid_input_dir_returns_400(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/analyze",
                json={"input_dir": "/nonexistent/dir"},
            )
            assert resp.status_code == 400
            assert "Invalid" in resp.get_json()["error"]

    def test_null_input_dir_with_no_state_returns_400(self, tmp_path, _suppress_config):
        """When both body and state input_dir are None, returns 400."""
        from bpp.web.app import create_app

        d = _real(tmp_path)
        app = create_app(workdir=d, library_path=d)
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.post("/api/v1/analyze", json={})
            assert resp.status_code == 400

    def test_no_body_falls_back_to_state_input_dir(self, bare_app):
        """When no input_dir in body, uses ctx.state['input_dir']."""
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/analyze", json={})
            assert resp.status_code == 202
            assert resp.get_json()["status"] == "started"

    def test_valid_dir_starts_analysis(self, bare_app):
        app, d = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/analyze", json={"input_dir": d})
            assert resp.status_code == 202
            data = resp.get_json()
            assert data["status"] == "started"
            assert "workdir" in data

    def test_concurrent_start_returns_409(self, bare_app):
        from bpp.web.analyze_worker import AnalyzeWorker

        app, d = bare_app
        with app.test_client() as c:
            # Mock is_alive property to simulate a running worker
            alive_prop = property(lambda self: True)
            with patch.object(AnalyzeWorker, "is_alive", alive_prop):
                resp = c.post("/api/v1/analyze", json={"input_dir": d})
            assert resp.status_code == 409
            assert "already" in resp.get_json()["error"]

    def test_recursive_flag_passed(self, bare_app):
        app, d = bare_app
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/analyze",
                json={"input_dir": d, "recursive": True},
            )
            assert resp.status_code == 202

    def test_archive_file_accepted(self, bare_app):
        """An archive file with a valid extension is accepted."""
        import zipfile

        app, d = bare_app
        archive = os.path.join(d, "photos.zip")
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("dummy.txt", "data")
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/analyze",
                json={"input_dir": archive},
            )
            assert resp.status_code == 202


class TestApiAnalyzeCancel:
    """POST /api/analyze/cancel"""

    def test_cancel_not_running(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/analyze/cancel")
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "not_running"

    def test_cancel_running(self, bare_app):
        app, d = bare_app
        with app.test_client() as c:
            c.post("/api/v1/analyze", json={"input_dir": d})
            resp = c.post("/api/v1/analyze/cancel")
            assert resp.status_code == 200
            assert resp.get_json()["status"] in (
                "cancelling",
                "not_running",
            )


class TestApiImportCancel:
    """POST /api/import/cancel"""

    def test_cancel_not_running(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/import/cancel")
            assert resp.status_code == 200
            assert resp.get_json()["status"] == "not_running"


class TestApiClearLibrary:
    """DELETE /api/library"""

    def test_missing_confirmation_returns_400(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.delete("/api/v1/library", json={})
            assert resp.status_code == 400
            assert "confirmation" in resp.get_json()["error"]

    def test_wrong_confirmation_returns_400(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.delete(
                "/api/v1/library",
                json={"confirmation": "yes"},
            )
            assert resp.status_code == 400

    def test_clear_while_worker_running_returns_409(self, bare_app):
        app, d = bare_app
        with app.test_client() as c:
            c.post("/api/v1/analyze", json={"input_dir": d})
            resp = c.delete(
                "/api/v1/library",
                json={"confirmation": "delete"},
            )
            # Worker may finish fast for empty dir
            if resp.status_code == 409:
                assert "running" in resp.get_json()["error"]
            else:
                assert resp.status_code == 200

    def test_clear_empty_library(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.delete(
                "/api/v1/library",
                json={"confirmation": "delete"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "cleared"
            assert "photos_deleted" in data
            assert "folders_removed" in data

    def test_clear_seeded_library(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.delete(
                "/api/v1/library",
                json={"confirmation": "delete"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "cleared"
            assert data["photos_deleted"] == 3


class TestApiImport:
    """POST /api/import"""

    def test_missing_source_dir_returns_400(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/import", json={})
            assert resp.status_code == 400
            assert "Invalid" in resp.get_json()["error"]

    def test_nonexistent_source_dir_returns_400(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/import",
                json={"source_dir": "/does/not/exist"},
            )
            assert resp.status_code == 400

    def test_valid_source_dir_starts_import(self, bare_app):
        app, d = bare_app
        source = os.path.join(d, "source_photos")
        os.makedirs(source)
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/import",
                json={"source_dir": source},
            )
            assert resp.status_code == 202
            data = resp.get_json()
            assert data["status"] == "started"
            assert "library_path" in data

    def test_concurrent_import_returns_409(self, bare_app):
        app, d = bare_app
        source = os.path.join(d, "source_photos")
        os.makedirs(source)
        img = os.path.join(source, "test.jpg")
        Image.new("RGB", (200, 200), "red").save(img, "JPEG")

        with app.test_client() as c:
            resp1 = c.post(
                "/api/v1/import",
                json={"source_dir": source},
            )
            assert resp1.status_code == 202
            resp2 = c.post(
                "/api/v1/import",
                json={"source_dir": source},
            )
            assert resp2.status_code in (202, 409)

    def test_import_with_batch_name(self, bare_app):
        app, d = bare_app
        source = os.path.join(d, "src")
        os.makedirs(source)
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/import",
                json={
                    "source_dir": source,
                    "batch_name": "vacation_2024",
                },
            )
            assert resp.status_code == 202


class TestApiLibraryStatus:
    """GET /api/library/status"""

    def test_status_returns_fields(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/library/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "library_path" in data
            assert "exists" in data
            assert "batches" in data
            assert "importing" in data
            assert isinstance(data["batches"], list)
            assert data["importing"] is False

    def test_status_with_batches(self, bare_app):
        app, d = bare_app
        os.makedirs(os.path.join(d, "photos", "batch_a"), exist_ok=True)
        os.makedirs(os.path.join(d, "photos", "batch_b"), exist_ok=True)
        with app.test_client() as c:
            resp = c.get("/api/v1/library/status")
            data = resp.get_json()
            assert data["exists"] is True
            assert "batch_a" in data["batches"]
            assert "batch_b" in data["batches"]

    def test_status_nonexistent_library(self, tmp_path, _suppress_config):
        from bpp.web.app import create_app

        d = _real(tmp_path)
        nonexist = os.path.join(d, "no_such_lib")
        app = create_app(workdir=d, library_path=nonexist)
        app.config["TESTING"] = True
        with app.test_client() as c:
            resp = c.get("/api/v1/library/status")
            data = resp.get_json()
            assert data["exists"] is False
            assert data["batches"] == []


# ===================================================================
# bp_photos tests
# ===================================================================


class TestApiPhotosPreview:
    """GET /api/photos/preview"""

    def test_preview_empty_db(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/photos/preview")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["photos"] == []
            assert data["count"] == 0

    def test_preview_with_photos(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/photos/preview")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 3
            photo = data["photos"][0]
            assert "filepath" in photo
            assert "filename" in photo
            assert "analyzed" in photo
            assert "aggregate_score" in photo
            assert "blur_score" in photo

    def test_preview_analysed_flag_true(self, app_with_photos):
        """Photos with aggregate_score show analyzed=True."""
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/photos/preview")
            data = resp.get_json()
            for photo in data["photos"]:
                assert photo["analyzed"] is True, (
                    f"photo {photo.get('filepath')} has aggregate_score "
                    f"{photo.get('aggregate_score')} but analyzed={photo.get('analyzed')}"
                )

    def test_preview_unanalysed_photos(self, tmp_path, _suppress_config):
        """Photos inserted without scores show analyzed=False."""
        d = _real(tmp_path)
        img = os.path.join(d, "unanalysed.jpg")
        Image.new("RGB", (50, 50), "green").save(img, "JPEG")

        from bpp.web.app import create_app

        app = create_app(workdir=d, input_dir=d, library_path=d)
        app.config["TESTING"] = True
        with app.app_context():
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            upsert_photo(conn, {"filepath": img})
            conn.commit()

        with app.test_client() as c:
            resp = c.get("/api/v1/photos/preview")
            data = resp.get_json()
            assert data["count"] == 1
            assert data["photos"][0]["analyzed"] is False


class TestApiOptimize:
    """POST /api/optimize"""

    def test_optimize_no_analysis_returns_404(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/optimize", json={})
            assert resp.status_code == 404
            assert "No analysis" in resp.get_json()["error"]

    def test_optimize_with_analysis(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post("/api/v1/optimize", json={"k": 2})
            assert resp.status_code == 200
            data = resp.get_json()
            assert "settings" in data
            assert "breakdown" in data
            s = data["settings"]
            assert "blur_weight" in s
            assert "exposure_weight" in s
            assert "composition_weight" in s
            assert "face_weight" in s


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
        """UAT: an existing destination must be merged into, not wiped.
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
            # The H1 release-audit fix changed export_selected's return
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


class TestApiBatchFavorite:
    """POST /api/batch/favorite"""

    def test_batch_favorite_empty_filepaths_returns_400(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/batch/favorite",
                json={"filepaths": []},
            )
            assert resp.status_code == 400

    def test_batch_favorite_success(self, app_with_photos):
        app, d = app_with_photos
        fps = [os.path.join(d, f"photo_{i}.jpg") for i in range(3)]
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/batch/favorite",
                json={"filepaths": fps, "favorite": True},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "ok"
            assert data["count"] == 3


class TestApiPhotosDelete:
    """POST /api/photos/delete (soft-delete)"""

    def test_delete_missing_filepaths_returns_400(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/photos/delete",
                json={"filepaths": []},
            )
            assert resp.status_code == 400

    def test_soft_delete_success(self, app_with_photos):
        app, d = app_with_photos
        fp = os.path.join(d, "photo_0.jpg")
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/photos/delete",
                json={"filepaths": [fp]},
            )
            assert resp.status_code == 200
            assert resp.get_json()["count"] == 1


class TestApiPhotosRestore:
    """POST /api/photos/restore"""

    def test_restore_missing_filepaths_returns_400(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/photos/restore",
                json={"filepaths": []},
            )
            assert resp.status_code == 400

    def test_restore_success(self, app_with_photos):
        app, d = app_with_photos
        fp = os.path.join(d, "photo_0.jpg")
        with app.test_client() as c:
            c.post(
                "/api/v1/photos/delete",
                json={"filepaths": [fp]},
            )
            resp = c.post(
                "/api/v1/photos/restore",
                json={"filepaths": [fp]},
            )
            assert resp.status_code == 200
            assert resp.get_json()["count"] == 1
            deleted = c.get("/api/v1/photos/deleted").get_json()
            assert deleted["total"] == 0


class TestApiPhotosDeletePermanent:
    """POST /api/photos/delete-permanent"""

    def test_permanent_delete_missing_filepaths_returns_400(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/photos/delete-permanent",
                json={"filepaths": [], "confirmation": "delete"},
            )
            assert resp.status_code == 400

    def test_permanent_delete_removes_from_db_and_disk(self, app_with_photos):
        app, d = app_with_photos
        fp = os.path.join(d, "photo_0.jpg")
        assert os.path.isfile(fp)
        with app.test_client() as c:
            # Must soft-delete first — permanent delete requires it
            c.post("/api/v1/photos/delete", json={"filepaths": [fp]})
            resp = c.post(
                "/api/v1/photos/delete-permanent",
                json={"filepaths": [fp], "confirmation": "delete"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 1
            assert data["files_removed"] == 1
            assert not os.path.isfile(fp)


class TestApiRecompute:
    """POST /api/recompute"""

    def test_recompute_no_analysis_returns_404(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/recompute", json={})
            assert resp.status_code == 404

    def test_recompute_returns_photos(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post("/api/v1/recompute", json={"k": 2})
            assert resp.status_code == 200
            data = resp.get_json()
            assert "photos" in data
            assert "selected_paths" in data
            assert "stats" in data
            assert len(data["photos"]) == 3

    def test_recompute_delta_mode(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/recompute",
                json={"k": 2, "delta": True},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert "scores" in data
            assert "selected_paths" in data
            assert "photos" not in data

    def test_recompute_with_weights(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/recompute",
                json={
                    "k": 2,
                    "blur_weight": 0.5,
                    "exposure_weight": 0.3,
                    "face_weight": 0.1,
                    "composition_weight": 0.1,
                },
            )
            assert resp.status_code == 200
            selected = resp.get_json()["selected_paths"]
            assert len(selected) <= 2

    def test_recompute_stats_fields(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.post("/api/v1/recompute", json={"k": 50})
            stats = resp.get_json()["stats"]
            assert "total" in stats
            assert "after_dedupe" in stats
            assert "total_selected" in stats
            assert "dedup_mode" in stats

    def test_recompute_sensitive_in_picks_exclude_filters(self, app_with_photos):
        """End-to-end: sensitive_in_picks='exclude' in the POST body threads
        into cfg and drops the sensitive photo from selection; 'allow' keeps
        it. Guards the bp_recompute → recompute() string-param wiring (the
        param does NOT ride the float weight-key path)."""
        app, d = app_with_photos
        sensitive_fp = os.path.join(d, "photo_2.jpg")
        with app.app_context():
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            # Distinct phash AND ahash (pairwise hamming > 8) so hash-dedup
            # keeps all three as separate candidates — isolates the sensitive
            # filter from dedup collapse (the fixture's 3 images are
            # identical, and a NULL ahash otherwise merges them).
            hashes = [0x0, 0x7FFFFFFF00000000, 0x000000007FFFFFFF]
            for i in range(3):
                fp = os.path.join(d, f"photo_{i}.jpg")
                row = {
                    "filepath": fp,
                    # Sensitive photo is the top scorer, so in "allow" mode it
                    # is unambiguously selected; only the policy can drop it.
                    "aggregate_score": 0.99 if fp == sensitive_fp else 0.5 + i * 0.1,
                    "blur_score": 0.7,
                    "blur_raw": 100.0,
                    "exposure_score": 0.8,
                    "face_score": 0.3,
                    "composition_score": 0.6,
                    "phash": hashes[i],
                    "ahash": hashes[i],
                    "date": f"2024-01-{i + 1:02d}T12:00:00",
                    "date_day": f"2024-01-{i + 1:02d}",
                    "date_month": "2024-01",
                }
                if fp == sensitive_fp:
                    row["nudity_score"] = 0.95  # over SENSITIVE_NUDITY_THRESHOLD
                upsert_photo(ctx.get_conn(), row)
            ctx.invalidate_analysis()

        with app.test_client() as c:
            excl = c.post(
                "/api/v1/recompute",
                json={"k": 50, "delta": True, "sensitive_in_picks": "exclude"},
            ).get_json()["selected_paths"]
            assert sensitive_fp not in excl
            assert len(excl) == 2  # the two non-sensitive photos still picked

            allow = c.post(
                "/api/v1/recompute",
                json={"k": 50, "delta": True, "sensitive_in_picks": "allow"},
            ).get_json()["selected_paths"]
            assert sensitive_fp in allow

    def test_recompute_413_when_full_payload_too_large(self, app_with_photos, monkeypatch):
        """R5-M1 + R6-M1: the cap on /api/v1/recompute fires BEFORE
        recompute() runs. Previously the check was post-recompute, so
        a 50k-photo library still paid the full CPU/RAM cost just to
        be 413'd. Inject 5001 fake analysis entries and assert
        recompute() is never called."""
        from bpp.web import bp_recompute as bp_photos

        app, _ = app_with_photos

        fake_analysis = [
            {
                "id": i,
                "filepath": f"/tmp/fake_{i}.jpg",
                "aggregate_score": 0.5,
                "blur_raw": 100.0,
                "deleted_at": None,
            }
            for i in range(5001)
        ]

        with app.app_context():
            ctx = app.extensions["bpp"]
            monkeypatch.setattr(ctx, "load_analysis_if_needed", lambda: fake_analysis)

        # If the cap moves back to post-recompute, this monkeypatch
        # will fail loudly with pytest.fail and the test will fail.
        def _must_not_run(_opts):
            pytest.fail("recompute() was called even though payload exceeds the cap")

        monkeypatch.setattr(bp_photos, "recompute", _must_not_run)

        with app.test_client() as c:
            resp = c.post("/api/v1/recompute", json={"k": 5})
        assert resp.status_code == 413, (
            f"Expected 413 for >5000-photo non-delta payload, got {resp.status_code}"
        )
        data = resp.get_json()
        assert data.get("delta_required") is True
        assert "delta" in data["error"].lower()
        assert data.get("photo_count") == 5001

    def test_recompute_413_skipped_in_delta_mode(self, app_with_photos, monkeypatch):
        """Inverse: delta mode bypasses the cap and runs recompute()
        normally — clients above the size threshold use delta on
        purpose. Tripping the cap in delta mode would defeat the
        whole fallback path."""
        from bpp.web import bp_recompute as bp_photos

        app, _ = app_with_photos

        fake_analysis = [
            {
                "id": i,
                "filepath": f"/tmp/fake_{i}.jpg",
                "aggregate_score": 0.5,
                "blur_raw": 100.0,
                "deleted_at": None,
            }
            for i in range(5001)
        ]

        with app.app_context():
            ctx = app.extensions["bpp"]
            monkeypatch.setattr(ctx, "load_analysis_if_needed", lambda: fake_analysis)

        # Stub recompute to a cheap response so the test doesn't
        # actually run the selection pipeline on 5001 fake entries.
        recompute_calls: list[int] = []

        def _stub_recompute(opts):
            recompute_calls.append(len(opts.analysis))
            return {
                "selected_paths": [],
                "photos": opts.analysis,
                "stats": {"total": len(opts.analysis), "total_selected": 0},
            }

        monkeypatch.setattr(bp_photos, "recompute", _stub_recompute)

        with app.test_client() as c:
            resp = c.post("/api/v1/recompute", json={"k": 5, "delta": True})
        assert resp.status_code == 200
        data = resp.get_json()
        assert "photos" not in data
        assert "selected_paths" in data
        assert "scores" in data
        assert len(recompute_calls) == 1, "recompute() must run in delta mode"


class TestApiPhotos:
    """GET /api/photos"""

    def test_no_analysis_returns_404(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/photos")
            assert resp.status_code == 404

    def test_with_analysis(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/photos")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 3
            assert len(data["photos"]) == 3

    def test_photo_fields(self, app_with_photos):
        """Verify all expected fields are present in photo dicts."""
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/photos")
            photo = resp.get_json()["photos"][0]
            for field in (
                "filepath",
                "filename",
                "date",
                "blur_score",
                "exposure_score",
                "face_score",
                "composition_score",
                "aggregate_score",
                "thumb_hash",
                "cluster_size",
            ):
                assert field in photo, f"missing {field}"

    # ── Pagination ─────────────────────────────────────────────────

    def test_pagination_metadata_present(self, app_with_photos):
        """Response must include total/limit/offset/has_more so
        clients know whether to fetch more."""
        app, _ = app_with_photos
        with app.test_client() as c:
            data = c.get("/api/v1/photos").get_json()
            for k in ("total", "limit", "offset", "has_more", "count"):
                assert k in data, f"missing pagination field {k}"
            assert data["total"] == 3
            assert data["offset"] == 0
            assert data["has_more"] is False  # 3 photos all fit in default page

    def test_pagination_limit(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            data = c.get("/api/v1/photos?limit=2").get_json()
            assert data["count"] == 2
            assert len(data["photos"]) == 2
            assert data["total"] == 3
            assert data["limit"] == 2
            assert data["has_more"] is True

    def test_pagination_offset(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            page1 = c.get("/api/v1/photos?limit=2&offset=0").get_json()
            page2 = c.get("/api/v1/photos?limit=2&offset=2").get_json()
            assert page1["count"] == 2
            assert page2["count"] == 1
            assert page2["has_more"] is False
            # Pages are disjoint: filepaths shouldn't overlap
            page1_paths = {p["filepath"] for p in page1["photos"]}
            page2_paths = {p["filepath"] for p in page2["photos"]}
            assert page1_paths.isdisjoint(page2_paths)

    def test_pagination_offset_past_end_is_empty_not_error(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            data = c.get("/api/v1/photos?offset=999").get_json()
            assert data["count"] == 0
            assert data["photos"] == []
            assert data["has_more"] is False
            assert data["total"] == 3

    def test_pagination_clamps_bad_input(self, app_with_photos):
        """Negative offset / non-numeric limit / oversized limit are
        clamped to safe defaults rather than 400-ing — listing APIs
        are forgiving."""
        app, _ = app_with_photos
        with app.test_client() as c:
            # Negative offset → clamped to 0
            r1 = c.get("/api/v1/photos?offset=-5").get_json()
            assert r1["offset"] == 0
            # Non-numeric → default
            r2 = c.get("/api/v1/photos?limit=banana&offset=banana").get_json()
            assert r2["limit"] == 5000
            assert r2["offset"] == 0
            # Oversized limit → capped at 50000
            r3 = c.get("/api/v1/photos?limit=999999").get_json()
            assert r3["limit"] == 50000

    def test_default_limit_caps_response(self, app_with_photos):
        """Without explicit limit, default cap is 5000 (so a
        100k-photo library doesn't ship the world in one response)."""
        app, _ = app_with_photos
        with app.test_client() as c:
            data = c.get("/api/v1/photos").get_json()
            assert data["limit"] == 5000
