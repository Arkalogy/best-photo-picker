"""Integration / end-to-end tests for multi-step workflows — part 2 of 2:
TestLibrarySwitchWorkflow, TestConcurrentPrevention, TestClearLibrary."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import os

import pytest

from tests._integration_shared import (  # noqa: F401
    _SKIP_SLOW_ANALYZE,
    _create_test_images,
    _drain_queue,
    _real,
    _suppress_config,
    integration_app,
    source_photos,
)

# ===================================================================
# 6. Library Switch Workflow
# ===================================================================


class TestLibrarySwitchWorkflow:
    """Switch between libraries and verify correct data isolation."""

    @pytest.fixture()
    def two_libraries(self, tmp_path, _suppress_config):
        """Two library directories, each with different photos."""
        from bpp.db.library import ensure_library_dirs
        from bpp.web.app import create_app

        lib1 = _real(tmp_path / "lib1")
        lib2 = _real(tmp_path / "lib2")
        dirs1 = ensure_library_dirs(lib1)
        ensure_library_dirs(lib2)

        # Create source photos for each library
        src1 = str(tmp_path / "src1")
        src2 = str(tmp_path / "src2")
        os.makedirs(src1)
        os.makedirs(src2)
        _create_test_images(src1, count=3)
        _create_test_images(src2, count=7)

        # Explicitly set workdir to data/ subdir (like commands.py does)
        app = create_app(workdir=dirs1["data"], library_path=lib1)
        app.config["TESTING"] = True

        # Import into lib1
        with app.app_context():
            from bpp.db.library import import_folder
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            import_folder(conn, src1, lib1, batch_name="lib1_batch")

        return app, lib1, lib2, src2

    def test_switch_library_changes_photos(self, two_libraries):
        """Switching library should change the visible photo set."""
        app, lib1, lib2, src2 = two_libraries

        with app.app_context():
            from bpp.db.library import import_folder
            from bpp.db.photos import get_photo_count
            from bpp.web.state import get_ctx

            ctx = get_ctx()

            # lib1 has 3 photos
            conn = ctx.get_conn()
            assert get_photo_count(conn) == 3

            # Switch to lib2 (empty)
            ctx.switch_library(lib2)
            conn = ctx.get_conn()
            assert get_photo_count(conn) == 0

            # Import into lib2
            import_folder(conn, src2, lib2, batch_name="lib2_batch")
            assert get_photo_count(conn) == 7

            # Switch back to lib1
            ctx.switch_library(lib1)
            conn = ctx.get_conn()
            assert get_photo_count(conn) == 3

    def test_switch_library_via_api(self, two_libraries):
        """POST /api/libraries/switch changes the active library."""
        app, _lib1, lib2, _ = two_libraries

        with app.test_client() as c:
            # Start on lib1
            resp = c.get("/api/v1/status")
            data = resp.get_json()
            data["library_path"]

            # Switch to lib2
            resp = c.post("/api/v1/libraries/switch", json={"path": lib2})
            assert resp.status_code == 200

            # Verify library changed
            resp = c.get("/api/v1/status")
            data = resp.get_json()
            assert data["library_path"] == lib2

    def test_switch_resets_analysis_cache(self, two_libraries):
        """Switching library should invalidate analysis cache."""
        app, _lib1, lib2, _ = two_libraries

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            # Load analysis for lib1
            ctx.load_analysis_if_needed()
            assert ctx.state["analysis"] is not None

            # Switch to lib2 (empty)
            ctx.switch_library(lib2)
            # Analysis should be reset or empty
            assert ctx.state["analysis"] is None or len(ctx.state["analysis"]) == 0


# ===================================================================
# 7. Concurrent Worker Prevention
# ===================================================================


class TestConcurrentPrevention:
    """Multiple simultaneous workers should be prevented."""

    def test_double_import_returns_409(self, integration_app, source_photos):
        """Starting a second import while one is running should return 409."""
        app, _lib = integration_app
        src, _ = source_photos

        with app.test_client() as c:
            # Start first import
            resp = c.post("/api/v1/import", json={"source_dir": src})
            assert resp.status_code == 202

            # Immediately try second import
            resp2 = c.post("/api/v1/import", json={"source_dir": src})
            assert resp2.status_code == 409

            # Wait for first to finish
            with app.app_context():
                from bpp.web.state import get_ctx

                ctx = get_ctx()
                ctx.import_worker._thread.join(timeout=180)

    def test_double_analyze_returns_409(self, integration_app, source_photos):
        """Starting a second analysis while one is running should return 409."""
        app, _lib = integration_app
        src, _ = source_photos

        with app.test_client() as c:
            # Start analysis
            resp = c.post("/api/v1/analyze", json={"input_dir": src})
            assert resp.status_code == 202

            # Try second analysis
            resp2 = c.post("/api/v1/analyze", json={"input_dir": src})
            assert resp2.status_code == 409

            # Wait for first to finish
            with app.app_context():
                from bpp.web.state import get_ctx

                ctx = get_ctx()
                ctx.worker._thread.join(timeout=180)

    def test_import_worker_cannot_start_twice(self, tmp_path):
        """ImportWorker.start() returns False if already running."""
        from bpp.db.library import ensure_library_dirs
        from bpp.web.import_worker import ImportWorker

        src = str(tmp_path / "src")
        lib = str(tmp_path / "lib")
        os.makedirs(src)
        dirs = ensure_library_dirs(lib)

        # Create enough images to keep worker busy
        _create_test_images(src, count=10, size=(300, 300))

        w = ImportWorker()
        first = w.start(src, lib, dirs["data"], {"max_long_side": 512}, [".jpg"])
        assert first is True

        second = w.start(src, lib, dirs["data"], {"max_long_side": 512}, [".jpg"])
        assert second is False

        w._thread.join(timeout=180)


# ===================================================================
# 8. Clear Library
# ===================================================================


class TestClearLibrary:
    """DELETE /api/library removes all photos but preserves structure."""

    def test_clear_requires_confirmation(self, integration_app):
        """Clear without confirmation='delete' should fail."""
        app, _ = integration_app

        with app.test_client() as c:
            resp = c.delete("/api/v1/library", json={})
            assert resp.status_code == 400

    def test_clear_removes_photos(self, integration_app, source_photos):
        """Clear library removes all photos from DB and disk."""
        app, lib = integration_app
        src, _ = source_photos

        with app.app_context():
            from bpp.db.library import import_folder
            from bpp.db.photos import get_photo_count
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            import_folder(conn, src, lib, batch_name="to_clear")
            assert get_photo_count(conn) == 5

        with app.test_client() as c:
            resp = c.delete("/api/v1/library", json={"confirmation": "delete"})
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["photos_deleted"] == 5

        with app.app_context():
            from bpp.db.photos import get_photo_count
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            assert get_photo_count(conn) == 0


# ===================================================================
# 9. Settings Persistence
# ===================================================================


class TestSettingsPersistence:
    """Settings saved via API should persist across requests."""

    def test_settings_round_trip(self, integration_app):
        """Save settings then retrieve them."""
        app, _ = integration_app

        with app.test_client() as c:
            # Save
            resp = c.put(
                "/api/v1/settings",
                json={"face_cluster_threshold": "0.42", "max_long_side": "2048"},
            )
            assert resp.status_code == 200

            # Read back
            resp = c.get("/api/v1/settings")
            data = resp.get_json()
            assert data["face_cluster_threshold"] == "0.42"
            assert data["max_long_side"] == "2048"

    def test_settings_survive_analysis_reload(self, integration_app):
        """Settings should still be present after invalidating analysis cache."""
        app, _ = integration_app

        with app.test_client() as c:
            c.put("/api/v1/settings", json={"follow_symlinks": "true"})

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.invalidate_analysis()
            ctx.load_analysis_if_needed()

        with app.test_client() as c:
            resp = c.get("/api/v1/settings")
            data = resp.get_json()
            assert data["follow_symlinks"] == "true"
