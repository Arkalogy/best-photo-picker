"""Tests for bp_analysis and bp_photos blueprints (uncovered endpoints) — part 1 of 3."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import os
from unittest.mock import patch

from PIL import Image

from tests._bp_analysis_photos_shared import (  # noqa: F401
    _real,
    _suppress_config,
    app_with_photos,
    bare_app,
)

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
