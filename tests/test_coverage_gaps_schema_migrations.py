"""Tests targeting coverage gaps in blueprints, settings, schema, and workers — part 2 of 3:
TestSchemaMigrations, TestFacesExtractRetry, TestFacesTagEndpoints."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import sqlite3

from tests._coverage_gaps_shared import (  # noqa: F401
    _real,
    _suppress_config,
    app_with_photos,
    bare_app,
)

# ===================================================================
# Schema Migrations (62% → 80%+)
# ===================================================================


class TestSchemaMigrations:
    """Test that schema migrations run correctly from older versions."""

    def _create_minimal_db(self, db_path, version):
        """Create a minimal DB at a given schema version.

        Includes all columns that would exist after migrations up to *version*
        have been applied, so that higher migrations (e.g. _backfill_exif_json)
        don't crash on missing columns.
        """
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        # Build column list progressively.
        # Columns that have always been in TABLES_SQL (never added via migration)
        # must be present at every version so INDEXES_SQL can reference them.
        photo_cols = [
            "id INTEGER PRIMARY KEY AUTOINCREMENT",
            "filepath TEXT UNIQUE NOT NULL",
            "original_filename TEXT",
            "import_batch TEXT",
            "sha256 TEXT",
            "file_size INTEGER DEFAULT 0",
            "file_mtime REAL DEFAULT 0",
            "missing INTEGER DEFAULT 0",
            "date TEXT",
            "date_day TEXT",
            "date_month TEXT",
            "blur_raw REAL DEFAULT 0.0",
            "exposure_score REAL DEFAULT 0.0",
            "face_score REAL DEFAULT 0.0",
            "face_count INTEGER DEFAULT 0",
            "largest_face_ratio REAL",
            "face_center_dist REAL",
            "composition_score REAL DEFAULT 0.0",
            "skin_score REAL",
            "nudity_score REAL",
            "blur_score REAL DEFAULT 0.0",
            "aggregate_score REAL DEFAULT 0.0",
            "phash INTEGER",
            "ahash INTEGER",
            "cluster_size INTEGER DEFAULT 1",
            "analyzed_at TEXT",
            "created_at TEXT DEFAULT (datetime('now'))",
        ]
        if version >= 3:
            photo_cols.append("deleted_at TEXT")
        if version >= 4:
            photo_cols += [
                "pet_count INTEGER DEFAULT 0",
                "has_cat INTEGER DEFAULT 0",
                "has_dog INTEGER DEFAULT 0",
            ]
        if version >= 6:
            photo_cols.append("exif_json TEXT")
        if version >= 8:
            photo_cols.append("is_video BOOLEAN DEFAULT 0")
        if version >= 9:
            photo_cols.append("is_raw BOOLEAN DEFAULT 0")
        if version >= 11:
            photo_cols.append("hidden_at TEXT")
        if version >= 15:
            photo_cols.append("video_duration REAL")
        if version >= 16:
            photo_cols += [
                "video_width INTEGER",
                "video_height INTEGER",
                "video_fps REAL",
                "video_codec TEXT",
            ]

        conn.execute(f"CREATE TABLE IF NOT EXISTS photos ({', '.join(photo_cols)})")

        album_cols = [
            "id INTEGER PRIMARY KEY AUTOINCREMENT",
            "name TEXT NOT NULL",
            "album_type TEXT DEFAULT 'manual'",
            "rule_json TEXT",
        ]
        if version >= 10:
            album_cols.append("parent_id INTEGER REFERENCES albums(id) ON DELETE SET NULL")
        conn.execute(f"CREATE TABLE IF NOT EXISTS albums ({', '.join(album_cols)})")

        # pet_detections table needed for v5+ migration
        if version >= 5:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS pet_detections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    photo_id INTEGER REFERENCES photos(id),
                    label TEXT,
                    confidence REAL,
                    bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
                    cluster_id INTEGER DEFAULT -1
                )"""
            )

        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
        return conn

    def test_migration_from_version_2(self, tmp_path):
        """Migrate from v2: should add deleted_at column."""
        db_path = str(tmp_path / "test.db")
        conn = self._create_minimal_db(db_path, 2)
        conn.close()

        from bpp.db.connection import init_db

        conn = init_db(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)").fetchall()}
        assert "deleted_at" in cols

    def test_migration_from_version_3(self, tmp_path):
        """Migrate from v3: should add pet columns."""
        db_path = str(tmp_path / "test.db")
        conn = self._create_minimal_db(db_path, 3)
        conn.close()

        from bpp.db.connection import init_db

        conn = init_db(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)").fetchall()}
        assert "pet_count" in cols
        assert "has_cat" in cols
        assert "has_dog" in cols

    def test_migration_adds_video_columns(self, tmp_path):
        """Migrate from v7: should add is_video."""
        db_path = str(tmp_path / "test.db")
        conn = self._create_minimal_db(db_path, 7)
        conn.close()

        from bpp.db.connection import init_db

        conn = init_db(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)").fetchall()}
        assert "is_video" in cols
        assert "is_raw" in cols

    def test_migration_adds_album_parent(self, tmp_path):
        """Migrate from v9: should add parent_id to albums."""
        db_path = str(tmp_path / "test.db")
        conn = self._create_minimal_db(db_path, 9)
        conn.close()

        from bpp.db.connection import init_db

        conn = init_db(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(albums)").fetchall()}
        assert "parent_id" in cols

    def test_migration_adds_hidden_at(self, tmp_path):
        """Migrate from v10: should add hidden_at."""
        db_path = str(tmp_path / "test.db")
        conn = self._create_minimal_db(db_path, 10)
        conn.close()

        from bpp.db.connection import init_db

        conn = init_db(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)").fetchall()}
        assert "hidden_at" in cols

    def test_migration_adds_video_metadata(self, tmp_path):
        """Migrate from v14: should add video_duration, width, height, fps, codec."""
        db_path = str(tmp_path / "test.db")
        conn = self._create_minimal_db(db_path, 14)
        conn.close()

        from bpp.db.connection import init_db

        conn = init_db(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)").fetchall()}
        assert "video_duration" in cols
        assert "video_width" in cols
        assert "video_height" in cols
        assert "video_fps" in cols
        assert "video_codec" in cols

    def test_fresh_schema_has_all_columns(self, tmp_path):
        """A fresh DB should have all columns from all migrations."""
        from bpp.db.connection import init_db

        db_path = str(tmp_path / "fresh.db")
        conn = init_db(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)").fetchall()}
        for expected in (
            "deleted_at",
            "pet_count",
            "exif_json",
            "is_video",
            "is_raw",
            "hidden_at",
            "video_duration",
            "video_width",
        ):
            assert expected in cols, f"Missing column: {expected}"


# ===================================================================
# Faces Blueprint - Extract/Retry/Tag (67% → 80%+)
# ===================================================================


class TestFacesExtractRetry:
    """Cover face extract and retry endpoints."""

    def test_extract_no_analysis(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/faces/extract")
            assert resp.status_code == 404

    def test_retry_no_analysis(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/faces/retry")
            assert resp.status_code == 404

    def test_extract_with_photos(self, app_with_photos):
        app, _ = app_with_photos
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.load_analysis_if_needed()

        with app.test_client() as c:
            resp = c.post("/api/v1/faces/extract")
            # Should start or report face_recognition not available
            assert resp.status_code in (202, 400)

    def test_retry_with_photos(self, app_with_photos):
        app, _ = app_with_photos
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.load_analysis_if_needed()

        with app.test_client() as c:
            resp = c.post("/api/v1/faces/retry")
            assert resp.status_code in (202, 400, 409)


class TestFacesTagEndpoints:
    """Cover face tag and untag endpoints."""

    def test_tag_missing_params(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/faces/tag", json={})
            assert resp.status_code == 400

    def test_tag_invalid_cluster_id(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/faces/tag",
                json={"path_hash": "abc", "cluster_id": -1},
            )
            assert resp.status_code == 400

    def test_tag_no_thumbnails(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post(
                "/api/v1/faces/tag",
                json={"path_hash": "abc", "cluster_id": 0},
            )
            assert resp.status_code == 404

    def test_untag_missing_params(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.delete("/api/v1/faces/tag", json={})
            assert resp.status_code == 400

    def test_face_crop_no_thumbnails(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/faces/crop/abc123/0")
            assert resp.status_code == 404


class TestFacesClipEndpoints:
    """Cover CLIP extraction endpoint."""

    def test_clip_extract_no_analysis(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/clip/extract")
            assert resp.status_code == 404


# ===================================================================
# Analysis Blueprint - SSE, Cancel (70% → 85%+)
# ===================================================================


class TestAnalysisBlueprintGaps:
    """Cover analysis progress SSE and cancel endpoints."""

    def test_cancel_when_not_running(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/analyze/cancel")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "not_running"

    def test_import_cancel_when_not_running(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/import/cancel")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["status"] == "not_running"

    def test_clear_library_no_confirmation(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.delete("/api/v1/library", json={})
            assert resp.status_code == 400

    def test_clear_library_workers_running(self, app_with_photos):
        from unittest.mock import PropertyMock, patch

        app, _ = app_with_photos
        with (
            app.test_client() as c,
            patch(
                "bpp.web.analyze_worker.AnalyzeWorker.is_alive",
                new_callable=PropertyMock,
                return_value=True,
            ),
        ):
            resp = c.delete("/api/v1/library", json={"confirmation": "delete"})
            assert resp.status_code == 409

    def test_import_invalid_source(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/import", json={"source_dir": "/nonexistent/path"})
            assert resp.status_code == 400

    def test_analyze_invalid_input(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/analyze", json={"input_dir": "/nonexistent/path"})
            assert resp.status_code == 400

    def test_library_status_empty(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/library/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "library_path" in data
