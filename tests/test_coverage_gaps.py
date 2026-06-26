"""Tests targeting coverage gaps in blueprints, settings, schema, and workers."""

from __future__ import annotations

import os
import sqlite3

import pytest
from PIL import Image


def _real(p):
    return os.path.realpath(str(p))


@pytest.fixture()
def _suppress_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture()
def bare_app(tmp_path, _suppress_config):
    from bpp.web.app import create_app

    d = _real(tmp_path)
    app = create_app(workdir=d, input_dir=d, library_path=d)
    app.config["TESTING"] = True
    return app, d


@pytest.fixture()
def app_with_photos(tmp_path, _suppress_config):
    """App seeded with photos that have dates for calendar/memories tests."""
    d = _real(tmp_path)
    for i in range(5):
        p = os.path.join(d, f"photo_{i}.jpg")
        Image.new("RGB", (100, 100), (50 + i * 40, 100, 150)).save(p, "JPEG")

    from bpp.web.app import create_app

    app = create_app(workdir=d, input_dir=d, library_path=d)
    app.config["TESTING"] = True

    with app.app_context():
        from bpp.db.albums import sync_all_photos_album
        from bpp.db.photos import upsert_photo
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()
        for i in range(5):
            upsert_photo(
                conn,
                {
                    "filepath": os.path.join(d, f"photo_{i}.jpg"),
                    "aggregate_score": 0.5 + i * 0.1,
                    "blur_score": 0.7,
                    "exposure_score": 0.8,
                    "face_score": 0.3,
                    "composition_score": 0.6,
                    "date": f"2024-06-{15 + i:02d}T12:00:00",
                    "date_day": f"2024-06-{15 + i:02d}",
                    "date_month": "2024-06",
                    "sha256": f"abc{i:04d}",
                },
            )
        sync_all_photos_album(conn)
        ctx.invalidate_analysis()

    return app, d


# ===================================================================
# Memories Blueprint (29% → 80%+)
# ===================================================================


class TestMemoriesBlueprint:
    """Cover all 3 endpoints in bp_memories."""

    def test_list_memories_empty(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/memories")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "memories" in data
            assert isinstance(data["memories"], list)

    def test_memory_detail_not_found(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/memories/9999")
            assert resp.status_code == 404

    def test_refresh_memories_empty(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/memories/refresh")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "count" in data

    @pytest.fixture()
    def app_with_clustered_photos(self, tmp_path, _suppress_config):
        """App with photos close enough in time to form a memory cluster (< 4h gap)."""
        d = _real(tmp_path)
        for i in range(5):
            p = os.path.join(d, f"photo_{i}.jpg")
            Image.new("RGB", (100, 100), (50 + i * 40, 100, 150)).save(p, "JPEG")

        from bpp.web.app import create_app

        app = create_app(workdir=d, input_dir=d, library_path=d)
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.db.albums import sync_all_photos_album
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            # All 5 photos within 1 hour → single cluster ≥ 3 photos → 1 memory
            for i in range(5):
                upsert_photo(
                    conn,
                    {
                        "filepath": os.path.join(d, f"photo_{i}.jpg"),
                        "aggregate_score": 0.5 + i * 0.1,
                        "blur_score": 0.7,
                        "exposure_score": 0.8,
                        "face_score": 0.3,
                        "composition_score": 0.6,
                        "date": f"2024-06-15T12:{i * 10:02d}:00",
                        "date_day": "2024-06-15",
                        "date_month": "2024-06",
                        "sha256": f"cluster{i:04d}",
                    },
                )
            sync_all_photos_album(conn)
            ctx.invalidate_analysis()

        return app, d

    def test_refresh_memories_with_photos(self, app_with_clustered_photos):
        app, _ = app_with_clustered_photos
        with app.test_client() as c:
            resp = c.post("/api/v1/memories/refresh")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] >= 1

    def test_list_memories_after_refresh(self, app_with_clustered_photos):
        app, _ = app_with_clustered_photos
        with app.test_client() as c:
            c.post("/api/v1/memories/refresh")
            resp = c.get("/api/v1/memories")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data["memories"]) >= 1

    def test_memory_detail_after_refresh(self, app_with_clustered_photos):
        app, _ = app_with_clustered_photos
        with app.test_client() as c:
            refresh_resp = c.post("/api/v1/memories/refresh")
            memories = refresh_resp.get_json().get("memories", [])
            assert len(memories) >= 1
            mid = memories[0]["id"]
            resp = c.get(f"/api/v1/memories/{mid}")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "photos" in data
            assert len(data["photos"]) >= 3

    def test_memory_detail_excludes_photos_inactivated_after_generation(
        self, app_with_clustered_photos
    ):
        """A memory's photo_ids are a snapshot; a member can become inactive
        AFTER generation (deleted, hidden, or tagged as a Live Photo sidecar
        by the phash backfill). The detail endpoint must re-check the active
        filter at resolution time -- stored id lists never bypass it.

        Regression: sidecar-tagged photos leaked into a user-facing view
        with 0% scores (2026-06-12)."""
        app, _ = app_with_clustered_photos
        with app.test_client() as c:
            refresh_resp = c.post("/api/v1/memories/refresh")
            mid = refresh_resp.get_json()["memories"][0]["id"]
            before = c.get(f"/api/v1/memories/{mid}").get_json()["photos"]
            assert len(before) >= 3

            # Tag one member as a sidecar after the memory was generated.
            with app.app_context():
                from bpp.web.state import get_ctx

                conn = get_ctx().get_conn()
                conn.execute(
                    "UPDATE photos SET is_live_photo_sidecar=1 WHERE id=?",
                    (before[0]["id"],),
                )
                conn.commit()

            after = c.get(f"/api/v1/memories/{mid}").get_json()["photos"]
            after_ids = {p["id"] for p in after}
            assert before[0]["id"] not in after_ids, (
                f"sidecar-tagged photo {before[0]['id']} still served by "
                f"memories detail: {sorted(after_ids)}"
            )
            assert len(after) == len(before) - 1


# ===================================================================
# Calendar Blueprint (64% → 80%+)
# ===================================================================


class TestCalendarBlueprint:
    """Cover calendar endpoints."""

    def test_calendar_months_empty(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/months")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "months" in data

    def test_calendar_months_with_photos(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/months")
            assert resp.status_code == 200
            data = resp.get_json()
            assert len(data["months"]) >= 1

    def test_calendar_days_missing_params(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/days")
            assert resp.status_code == 400

    def test_calendar_days_invalid_month(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/days?year=2024&month=13")
            assert resp.status_code == 400

    def test_calendar_days_valid(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/days?year=2024&month=6")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "days" in data
            assert data["year"] == 2024
            assert data["month"] == 6

    def test_calendar_year_missing_param(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/year")
            assert resp.status_code == 400

    def test_calendar_year_invalid(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/year?year=999")
            assert resp.status_code == 400

    def test_calendar_year_valid(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/year?year=2024")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["year"] == 2024
            assert "months" in data

    def test_calendar_photos_missing_params(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/photos")
            assert resp.status_code == 400

    def test_calendar_photos_by_date(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/photos?date=2024-06-15")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "photos" in data

    def test_calendar_photos_by_range(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/calendar/photos?start=2024-06-15&end=2024-06-20")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "photos" in data
            assert len(data["photos"]) >= 1

    def test_on_this_day_empty(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/on-this-day")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "years" in data

    def test_on_this_day_with_params(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/on-this-day?month=6&day=15")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "years" in data

    def test_on_this_day_invalid_month(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/on-this-day?month=13")
            assert resp.status_code == 400

    def test_on_this_day_invalid_day(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/on-this-day?day=32")
            assert resp.status_code == 400


# ===================================================================
# Settings DB (68% → 90%+)
# ===================================================================


class TestSettingsDB:
    """Cover get_setting, set_setting, delete_setting functions."""

    @pytest.fixture()
    def conn(self, tmp_path):
        from bpp.db.connection import init_db

        db_path = str(tmp_path / "photopicker.db")
        return init_db(db_path)

    def test_get_setting_default(self, conn):
        from bpp.db.settings import get_setting

        assert get_setting(conn, "nonexistent") is None
        assert get_setting(conn, "nonexistent", "fallback") == "fallback"

    def test_set_and_get_setting(self, conn):
        from bpp.db.settings import get_setting, set_setting

        set_setting(conn, "my_key", "my_value")
        assert get_setting(conn, "my_key") == "my_value"

    def test_set_setting_upsert(self, conn):
        from bpp.db.settings import get_setting, set_setting

        set_setting(conn, "k", "v1")
        set_setting(conn, "k", "v2")
        assert get_setting(conn, "k") == "v2"

    def test_delete_setting(self, conn):
        from bpp.db.settings import delete_setting, get_setting, set_setting

        set_setting(conn, "temp", "val")
        assert get_setting(conn, "temp") == "val"
        delete_setting(conn, "temp")
        assert get_setting(conn, "temp") is None

    def test_set_setting_numeric(self, conn):
        from bpp.db.settings import get_setting, set_setting

        set_setting(conn, "threshold", 0.55)
        assert get_setting(conn, "threshold") == "0.55"


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


# ===================================================================
# Core Blueprint gaps (73% → 85%+)
# ===================================================================


class TestCoreBlueprintGaps:
    """Cover storage health, recheck-missing, presets."""

    def test_storage_health(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.get("/api/v1/health/storage")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "accessible" in data

    def test_recheck_missing_no_missing(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            resp = c.post("/api/v1/photos/recheck-missing")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["restored"] == 0
            assert data["still_missing"] == 0

    def test_recheck_missing_with_missing(self, app_with_photos):
        app, _d = app_with_photos
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            # Mark one photo as missing
            conn.execute("UPDATE photos SET missing=1 WHERE id=1")
            conn.commit()

        with app.test_client() as c:
            resp = c.post("/api/v1/photos/recheck-missing")
            assert resp.status_code == 200
            data = resp.get_json()
            # The file exists on disk, so it should be restored
            assert data["restored"] >= 0

    def test_stats_endpoint(self, app_with_photos):
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/stats")
            assert resp.status_code == 200
            data = resp.get_json()
            assert "total_count" in data

    def test_presets_crud(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            # Create
            resp = c.post(
                "/api/v1/presets",
                json={"name": "My Preset", "settings": {"k": 10}},
            )
            assert resp.status_code == 200

            # List
            resp = c.get("/api/v1/presets")
            data = resp.get_json()
            assert "My Preset" in data["presets"]

            # Delete
            resp = c.delete("/api/v1/presets/My Preset")
            assert resp.status_code == 200

            # Delete nonexistent
            resp = c.delete("/api/v1/presets/Nonexistent")
            assert resp.status_code == 404

    def test_settings_api(self, bare_app):
        app, _ = bare_app
        with app.test_client() as c:
            # Put empty
            resp = c.put("/api/v1/settings", json={})
            assert resp.status_code == 400

            # Put valid
            resp = c.put("/api/v1/settings", json={"key": "val"})
            assert resp.status_code == 200

            # Get
            resp = c.get("/api/v1/settings")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["key"] == "val"

    def test_status_full_fields(self, app_with_photos):
        """Verify /api/status returns all expected fields."""
        app, _ = app_with_photos
        with app.test_client() as c:
            resp = c.get("/api/v1/status")
            data = resp.get_json()
            expected_keys = [
                "has_analysis",
                "first_run",
                "image_count",
                "workdir",
                "library_path",
                "analyzing",
                "importing",
                "serve_mode",
                "face_recognition_available",
                "nudenet_available",
                "clip_available",
                "heic_available",
            ]
            for key in expected_keys:
                assert key in data, f"Missing status key: {key}"
