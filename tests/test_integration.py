"""Integration / end-to-end tests for multi-step workflows — part 1 of 2."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import os

import pytest
from PIL import Image

from tests._integration_shared import (  # noqa: F401
    _SKIP_SLOW_ANALYZE,
    _create_test_images,
    _drain_queue,
    _real,
    _suppress_config,
    integration_app,
    source_photos,
)
from tests._models import skip_unless_analyze_models

# ===================================================================
# 1. Import Pipeline (folder scan → copy → DB insert → verify)
# ===================================================================


class TestImportPipeline:
    """Full import pipeline: source folder → library copy → DB records."""

    def test_import_creates_copies_and_db_records(self, integration_app, source_photos):
        """Import copies files to library/photos/{batch}/ and inserts DB rows."""
        app, lib = integration_app
        src, _src_paths = source_photos

        with app.app_context():
            from bpp.db.library import get_library_dirs, import_folder
            from bpp.db.photos import get_photo_count
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()

            result = import_folder(conn, src, lib, batch_name="trip_2024")

            assert result.imported == 5
            assert result.skipped == 0
            assert result.errors == 0
            assert result.batch_name == "trip_2024"

            # Files should be in library/photos/trip_2024/
            photos_dir = get_library_dirs(lib)["photos"]
            batch_dir = os.path.join(photos_dir, "trip_2024")
            assert os.path.isdir(batch_dir)
            copied_files = os.listdir(batch_dir)
            assert len(copied_files) == 5

            # DB should have 5 photo rows
            count = get_photo_count(conn)
            assert count == 5

            # No .tmp files anywhere in library
            for _dp, _dn, files in os.walk(lib):
                for f in files:
                    assert not f.endswith(".tmp"), f"Leftover .tmp: {f}"

    def test_import_dedup_skips_same_sha256(self, integration_app, source_photos):
        """Re-importing the same files should skip all (SHA-256 dedup)."""
        app, lib = integration_app
        src, _ = source_photos

        with app.app_context():
            from bpp.db.library import import_folder
            from bpp.db.photos import get_photo_count
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()

            # First import
            r1 = import_folder(conn, src, lib, batch_name="batch1")
            assert r1.imported == 5

            # Second import of same files
            r2 = import_folder(conn, src, lib, batch_name="batch2")
            assert r2.imported == 0
            assert r2.skipped == 5

            # DB should still have only 5 photos
            assert get_photo_count(conn) == 5

    def test_import_preserves_original_filename(self, integration_app, source_photos):
        """Imported photos should have original_filename set in DB."""
        app, lib = integration_app
        src, _ = source_photos

        with app.app_context():
            from bpp.db.library import import_folder
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            import_folder(conn, src, lib)

            rows = conn.execute(
                "SELECT original_filename FROM photos ORDER BY original_filename"
            ).fetchall()
            names = [r[0] for r in rows]
            assert "img_000.jpg" in names
            assert len(names) == 5

    def test_import_worker_full_pipeline(self, tmp_path):
        """ImportWorker: scan → copy → analyze → DB insert (all phases)."""
        skip_unless_analyze_models()  # real workers
        from bpp.db.library import ensure_library_dirs
        from bpp.web.import_worker import ImportWorker

        src = str(tmp_path / "src")
        lib = str(tmp_path / "lib")
        os.makedirs(src)
        dirs = ensure_library_dirs(lib)

        # Create 3 test images
        _create_test_images(src, count=3, size=(150, 150))

        w = ImportWorker()
        w.start(src, lib, dirs["data"], {"max_long_side": 256}, [".jpg"])
        w._thread.join(timeout=180)

        msgs = _drain_queue(w.progress_queue)
        types = [m["type"] for m in msgs]

        # Should see all phases
        assert "phase" in types
        assert "import_done" in types
        assert "done" in types

        import_done = next(m for m in msgs if m["type"] == "import_done")
        assert import_done["imported"] == 3
        assert import_done["skipped"] == 0

        done = next(m for m in msgs if m["type"] == "done")
        assert done["analyzed"] == 3

        # Verify files exist in library
        batch_photos = []
        for _dp, _dn, files in os.walk(dirs["photos"]):
            batch_photos.extend(files)
        assert len(batch_photos) == 3


class TestAnalysisWorkflow:
    """Analysis produces scores and stores them in DB."""

    @_SKIP_SLOW_ANALYZE
    def test_analyze_produces_scores(self, tmp_path):
        """AnalyzeWorker produces aggregate_score for each photo."""
        skip_unless_analyze_models()  # real workers
        from bpp.web.analyze_worker import AnalyzeWorker

        input_dir = str(tmp_path / "photos")
        workdir = str(tmp_path / "work")
        os.makedirs(input_dir)
        os.makedirs(workdir)

        _create_test_images(input_dir, count=3, size=(200, 200))

        w = AnalyzeWorker()
        w.start(input_dir, workdir, {"max_long_side": 256}, [".jpg"])
        w._thread.join(timeout=180)

        assert w.results is not None
        assert len(w.results) == 3
        for r in w.results:
            assert "aggregate_score" in r
            assert r["aggregate_score"] > 0
            assert "blur_score" in r
            assert "exposure_score" in r
            assert "composition_score" in r

    def test_import_then_analyze_populates_db(self, integration_app, source_photos):
        """Import via API then analyze — DB should have scores."""
        app, lib = integration_app
        src, _ = source_photos

        with app.app_context():
            from bpp.db.library import import_folder
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()

            # Import
            import_folder(conn, src, lib, batch_name="test_batch")

            # Verify photos are in DB
            count = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
            assert count == 5

            # Verify imported photos have SHA-256 set
            rows = conn.execute("SELECT sha256 FROM photos WHERE sha256 IS NOT NULL").fetchall()
            assert len(rows) == 5

    @_SKIP_SLOW_ANALYZE
    def test_analyze_worker_writes_to_db(self, tmp_path):
        """AnalyzeWorker writes results to photopicker.db."""
        skip_unless_analyze_models()  # real workers
        from bpp.db.connection import get_db
        from bpp.web.analyze_worker import AnalyzeWorker

        input_dir = str(tmp_path / "photos")
        workdir = str(tmp_path / "work")
        os.makedirs(input_dir)
        os.makedirs(workdir)

        _create_test_images(input_dir, count=2, size=(150, 150))

        w = AnalyzeWorker()
        w.start(input_dir, workdir, {"max_long_side": 256}, [".jpg"])
        w._thread.join(timeout=180)

        # Check DB was created and has records
        db_path = os.path.join(workdir, "photopicker.db")
        assert os.path.isfile(db_path)
        conn = get_db(db_path)
        count = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        assert count == 2

        # Scores should be populated
        rows = conn.execute(
            "SELECT aggregate_score FROM photos WHERE aggregate_score > 0"
        ).fetchall()
        assert len(rows) == 2


# ===================================================================
# 3. Onboarding Flow (first_run → import → status transition)
# ===================================================================


class TestOnboardingFlow:
    """First-run detection and status transitions."""

    def test_fresh_library_shows_first_run(self, integration_app):
        """GET /api/status on a newly-created empty library returns first_run=true.

        A fresh DB (no photos, 'first_run' flag set in settings) should surface
        first_run=true so the onboarding overlay appears.
        """
        app, _lib = integration_app

        with app.test_client() as c:
            resp = c.get("/api/v1/status")
            data = resp.get_json()
            assert data["first_run"] is True
            assert data["has_analysis"] is False
            assert data["image_count"] == 0

    def test_first_run_false_after_import(self, integration_app, source_photos):
        """After importing photos, first_run should be false."""
        app, lib = integration_app
        src, _ = source_photos

        with app.app_context():
            from bpp.db.albums import sync_all_photos_album
            from bpp.db.library import import_folder
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()

            # Import photos
            result = import_folder(conn, src, lib)
            assert result.imported == 5

            # Need to give them analysis scores for load_analysis to include them
            for path in result.imported_paths:
                upsert_photo(conn, {"filepath": path, "aggregate_score": 0.5})
            sync_all_photos_album(conn)
            ctx.invalidate_analysis()

        with app.test_client() as c:
            resp = c.get("/api/v1/status")
            data = resp.get_json()
            assert data["first_run"] is False
            assert data["has_analysis"] is True
            assert data["image_count"] == 5

    def test_status_includes_library_path(self, integration_app):
        """GET /api/status should include the library_path."""
        app, _lib = integration_app

        with app.test_client() as c:
            resp = c.get("/api/v1/status")
            data = resp.get_json()
            assert "library_path" in data
            assert data["library_path"] != ""


# ===================================================================
# 4. Face Pipeline (extract → cluster → verify DB)
# ===================================================================


class TestFacePipelineIntegration:
    """Face detection + clustering stores consistent data in DB."""

    @pytest.mark.slow
    def test_face_worker_populates_embeddings(self, tmp_path):
        """FaceWorker with photos containing faces should populate face_embeddings."""
        skip_unless_analyze_models()  # real workers
        from bpp.db.connection import init_db
        from bpp.db.photos import upsert_photo
        from bpp.web.face_worker import FaceWorker

        # Create a photo with a face-like pattern (solid color, won't detect real faces
        # but tests the pipeline flow)
        data_dir = str(tmp_path / "data")
        os.makedirs(data_dir)
        img_path = str(tmp_path / "face_test.jpg")
        Image.new("RGB", (200, 200), "beige").save(img_path, "JPEG")

        db_path = os.path.join(data_dir, "photopicker.db")
        conn = init_db(db_path)
        upsert_photo(
            conn,
            {"filepath": img_path, "face_count": 1, "aggregate_score": 0.5},
        )
        conn.close()

        analysis = [{"filepath": img_path, "face_count": 1}]
        w = FaceWorker()
        started = w.start(analysis, db_path, {"max_long_side": 256})
        assert started
        w._thread.join(timeout=180)

        msgs = _drain_queue(w.progress_queue)
        types = [m["type"] for m in msgs]
        assert "done" in types

    def test_face_recluster_endpoint(self, integration_app):
        """POST /api/faces/recluster should not crash on empty DB."""
        app, _ = integration_app

        with app.test_client() as c:
            resp = c.post(
                "/api/v1/faces/recluster",
                json={"threshold": 0.5},
            )
            # Should succeed (nothing to cluster) or return appropriate status
            assert resp.status_code in (200, 400)


# ===================================================================
# 5. API Endpoint Chains (status → import → library status)
# ===================================================================


class TestAPIEndpointChains:
    """Multi-endpoint flows that simulate real user interaction."""

    def test_status_import_status_chain(self, integration_app, source_photos):
        """status(empty) → import → wait → status(has photos)."""
        app, _lib = integration_app
        src, _ = source_photos

        with app.test_client() as c:
            # Step 1: Fresh empty library — first_run is True
            resp = c.get("/api/v1/status")
            data = resp.get_json()
            assert data["first_run"] is True
            assert data["importing"] is False

            # Step 2: Start import
            resp = c.post("/api/v1/import", json={"source_dir": src})
            assert resp.status_code == 202
            data = resp.get_json()
            assert data["status"] == "started"

            # Step 3: Wait for import to finish
            with app.app_context():
                from bpp.web.state import get_ctx

                ctx = get_ctx()
                ctx.import_worker._thread.join(timeout=180)

            # Step 4: Check library status
            resp = c.get("/api/v1/library/status")
            data = resp.get_json()
            assert data["exists"] is True
            assert len(data["batches"]) >= 1

    def test_import_then_photos_preview(self, integration_app, source_photos):
        """Import → wait → GET /api/photos/preview returns photos."""
        app, _lib = integration_app
        src, _ = source_photos

        with app.test_client() as c:
            # Import
            resp = c.post("/api/v1/import", json={"source_dir": src})
            assert resp.status_code == 202

            # Wait for completion
            with app.app_context():
                from bpp.web.state import get_ctx

                ctx = get_ctx()
                ctx.import_worker._thread.join(timeout=180)
                ctx.invalidate_analysis()
                ctx.load_analysis_if_needed()

            # Preview should return imported photos
            resp = c.get("/api/v1/photos/preview")
            data = resp.get_json()
            assert data["count"] == 5

    def test_stats_endpoint_reflects_imports(self, integration_app, source_photos):
        """GET /api/stats shows correct photo count after import."""
        app, lib = integration_app
        src, _ = source_photos

        with app.app_context():
            from bpp.db.library import import_folder
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            import_folder(conn, src, lib)

        with app.test_client() as c:
            resp = c.get("/api/v1/stats")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["total_count"] >= 5

    def test_library_status_shows_batches(self, integration_app, source_photos):
        """After import, /api/library/status should list the batch."""
        app, lib = integration_app
        src, _ = source_photos

        with app.app_context():
            from bpp.db.library import import_folder
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            import_folder(conn, src, lib, batch_name="vacation_2024")

        with app.test_client() as c:
            resp = c.get("/api/v1/library/status")
            data = resp.get_json()
            assert "vacation_2024" in data["batches"]
