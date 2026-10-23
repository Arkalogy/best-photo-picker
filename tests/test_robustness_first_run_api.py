"""Tests for robustness features: backup, integrity, WAL, relocation, atomic import — part 3 of 3:
TestFirstRunAPI, TestStartupFileHealth."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import hashlib
import os
import time

import pytest

from bpp.db.connection import (
    close_all_connections,
    init_db,
)
from bpp.db.photos import (
    upsert_photo,
)
from tests._robustness_shared import conn, db_path  # noqa: F401

# ===========================================================================
# first_run API test
# ===========================================================================


class TestFirstRunAPI:
    @pytest.fixture
    def empty_app(self, tmp_path):
        """Create a Flask app with an empty library (no photos)."""
        from bpp.web.app import create_app

        wd = str(tmp_path / "data")
        os.makedirs(wd, exist_ok=True)
        app = create_app(workdir=wd)
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def populated_app(self, tmp_path):
        """Create a Flask app with some photos (real files so missing=0)."""
        from bpp.web.app import create_app

        wd = str(tmp_path / "data")
        os.makedirs(wd, exist_ok=True)
        # Create real image files so upsert_photo sets missing=0
        photos_dir = tmp_path / "photos"
        photos_dir.mkdir()
        db_p = os.path.join(wd, "photopicker.db")
        conn = init_db(db_p)
        for i in range(5):
            img = photos_dir / f"photo_{i}.jpg"
            img.write_bytes(b"\xff\xd8\xff\xe0" + bytes([i]) * 100)
            upsert_photo(conn, {"filepath": str(img)})
        conn.close()
        close_all_connections()

        app = create_app(workdir=wd)
        app.config["TESTING"] = True
        return app

    def test_first_run_true_fresh_library(self, empty_app):
        """A freshly created library (DB just initialised, 0 photos) shows first_run=true."""
        with empty_app.test_client() as client:
            resp = client.get("/api/v1/status")
            data = resp.get_json()
            assert data["first_run"] is True
            assert data["has_analysis"] is False

    def test_first_run_false_with_photos(self, populated_app):
        with populated_app.test_client() as client:
            resp = client.get("/api/v1/status")
            data = resp.get_json()
            assert data["first_run"] is False
            assert data["has_analysis"] is True
            assert data["image_count"] == 5

    def test_status_includes_library_path(self, empty_app):
        with empty_app.test_client() as client:
            resp = client.get("/api/v1/status")
            data = resp.get_json()
            assert "library_path" in data


# ===========================================================================
# Startup file health checks
# ===========================================================================


@pytest.mark.real_health_checks
class TestStartupFileHealth:
    def test_startup_scan_detects_missing(self, tmp_path):
        """Startup scan should mark files as missing."""
        from bpp.web.state import WebAppState

        lib = tmp_path / "lib"
        lib.mkdir()
        photos = lib / "photos"
        photos.mkdir()
        data = lib / "data"
        data.mkdir()

        # Create a real file, insert it, then delete it
        img = photos / "real.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        db_p = str(data / "photopicker.db")
        conn = init_db(db_p)
        upsert_photo(conn, {"filepath": str(img)})
        upsert_photo(conn, {"filepath": "/fake/gone.jpg"})
        conn.close()
        close_all_connections()

        # Create WebAppState in serve mode
        state = WebAppState(workdir=str(data), library_path=str(lib))

        # Wait a moment for the background thread to complete
        time.sleep(1)

        conn = state.get_conn()
        # The real file should still be present
        row = conn.execute("SELECT missing FROM photos WHERE filepath=?", (str(img),)).fetchone()
        assert row[0] == 0

        # The fake file should be missing
        row = conn.execute(
            "SELECT missing FROM photos WHERE filepath=?",
            ("/fake/gone.jpg",),
        ).fetchone()
        assert row[0] == 1
        close_all_connections()

    def test_startup_scan_relocates_moved_file(self, tmp_path):
        """Startup scan should relocate a moved file by SHA-256."""
        from bpp.web.state import WebAppState

        lib = tmp_path / "lib"
        lib.mkdir()
        photos = lib / "photos"
        photos.mkdir()
        data = lib / "data"
        data.mkdir()

        # Create file at old location
        old_dir = photos / "old_batch"
        old_dir.mkdir()
        data_bytes = b"\xff\xd8\xff\xe0" + b"\xab" * 200
        old_file = old_dir / "moved.jpg"
        old_file.write_bytes(data_bytes)
        sha = hashlib.sha256(data_bytes).hexdigest()

        db_p = str(data / "photopicker.db")
        conn = init_db(db_p)
        upsert_photo(conn, {"filepath": str(old_file), "sha256": sha})
        conn.close()
        close_all_connections()

        # "Move" the file
        new_dir = photos / "new_batch"
        new_dir.mkdir()
        new_file = new_dir / "moved.jpg"
        old_file.rename(new_file)

        state = WebAppState(workdir=str(data), library_path=str(lib))
        time.sleep(1)

        conn = state.get_conn()
        row = conn.execute("SELECT filepath, missing FROM photos").fetchone()
        assert row[1] == 0  # Not missing
        assert row[0] == str(new_file)  # Relocated
        close_all_connections()

    def test_no_health_checks_without_serve_mode(self, tmp_path):
        """Health checks should not start when not in serve mode."""
        from bpp.web.state import WebAppState

        wd = str(tmp_path / "data")
        os.makedirs(wd, exist_ok=True)

        state = WebAppState(workdir=wd)
        # serve_mode should be False (no library_path given)
        assert state.serve_mode is False
        # _health_handle should not exist (threads never started)
        assert not hasattr(state, "_health_handle")
        close_all_connections()
