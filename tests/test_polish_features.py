"""Tests for remaining polish features: stats, media filter, sort, date adjust, video hover."""

from __future__ import annotations

import sqlite3

import pytest

from bpp.db.schema import create_tables


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_tables(conn)
    return conn


def _insert_photo(conn, filepath, **kw):
    defaults = {
        "original_filename": filepath.rsplit("/", 1)[-1],
        "file_size": kw.pop("file_size", 1000),
        "file_mtime": 0.0,
        "is_video": 0,
        "is_raw": 0,
        "face_count": 0,
        "aggregate_score": 0.5,
    }
    defaults.update(kw)
    cols = ["filepath", *list(defaults.keys())]
    vals = [filepath, *list(defaults.values())]
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO photos ({', '.join(cols)}) VALUES ({placeholders})", vals)
    conn.commit()


# ═══════════════════════════════════════════
# Feature 1: Library Stats
# ═══════════════════════════════════════════


class TestLibraryStats:
    """GET /api/stats should return disk usage and format breakdown."""

    def test_stats_empty_library(self):
        from bpp.db.stats import get_library_stats

        conn = _make_db()
        stats = get_library_stats(conn)
        assert stats["total_count"] == 0
        assert stats["total_size"] == 0
        assert stats["photo_count"] == 0
        assert stats["video_count"] == 0
        assert stats["raw_count"] == 0
        assert stats["format_breakdown"] == {}

    def test_stats_mixed_library(self):
        from bpp.db.stats import get_library_stats

        conn = _make_db()
        _insert_photo(conn, "/lib/a.jpg", file_size=5000)
        _insert_photo(conn, "/lib/b.png", file_size=8000)
        _insert_photo(conn, "/lib/c.mp4", file_size=50000, is_video=1)
        _insert_photo(conn, "/lib/d.cr2", file_size=20000, is_raw=1)
        _insert_photo(conn, "/lib/e.jpg", file_size=3000)

        stats = get_library_stats(conn)
        assert stats["total_count"] == 5
        assert stats["total_size"] == 86000
        assert stats["photo_count"] == 3  # jpg + png (not video, not raw)
        assert stats["video_count"] == 1
        assert stats["raw_count"] == 1
        assert stats["format_breakdown"][".jpg"] == 2
        assert stats["format_breakdown"][".png"] == 1
        assert stats["format_breakdown"][".mp4"] == 1
        assert stats["format_breakdown"][".cr2"] == 1

    def test_stats_excludes_deleted(self):
        from bpp.db.stats import get_library_stats

        conn = _make_db()
        _insert_photo(conn, "/lib/a.jpg", file_size=5000)
        _insert_photo(conn, "/lib/b.jpg", file_size=3000)
        conn.execute("UPDATE photos SET deleted_at='2026-01-01' WHERE filepath='/lib/b.jpg'")
        conn.commit()

        stats = get_library_stats(conn)
        assert stats["total_count"] == 1
        assert stats["total_size"] == 5000

    def test_stats_avg_file_size(self):
        from bpp.db.stats import get_library_stats

        conn = _make_db()
        _insert_photo(conn, "/lib/a.jpg", file_size=4000)
        _insert_photo(conn, "/lib/b.jpg", file_size=6000)

        stats = get_library_stats(conn)
        assert stats["avg_file_size"] == 5000


# ═══════════════════════════════════════════
# Feature 2: Media Type Filter
# (Frontend-only; test the filter logic extracted to a helper)
# ═══════════════════════════════════════════


class TestMediaTypeFilter:
    """Filter by media type: photos-only, videos-only, raw-only."""

    def test_filter_videos_only(self):
        from bpp.web.filters import apply_media_filter

        items = [
            {"filepath": "a.jpg", "is_video": False, "is_raw": False},
            {"filepath": "b.mp4", "is_video": True, "is_raw": False},
            {"filepath": "c.cr2", "is_video": False, "is_raw": True},
        ]
        result = apply_media_filter(items, "videos")
        assert len(result) == 1
        assert result[0]["filepath"] == "b.mp4"

    def test_filter_raw_only(self):
        from bpp.web.filters import apply_media_filter

        items = [
            {"filepath": "a.jpg", "is_video": False, "is_raw": False},
            {"filepath": "b.mp4", "is_video": True, "is_raw": False},
            {"filepath": "c.cr2", "is_video": False, "is_raw": True},
        ]
        result = apply_media_filter(items, "raw")
        assert len(result) == 1
        assert result[0]["filepath"] == "c.cr2"

    def test_filter_photos_only(self):
        from bpp.web.filters import apply_media_filter

        items = [
            {"filepath": "a.jpg", "is_video": False, "is_raw": False},
            {"filepath": "b.mp4", "is_video": True, "is_raw": False},
            {"filepath": "c.cr2", "is_video": False, "is_raw": True},
        ]
        result = apply_media_filter(items, "photos")
        assert len(result) == 1
        assert result[0]["filepath"] == "a.jpg"

    def test_filter_all_passes_through(self):
        from bpp.web.filters import apply_media_filter

        items = [
            {"filepath": "a.jpg", "is_video": False, "is_raw": False},
            {"filepath": "b.mp4", "is_video": True, "is_raw": False},
        ]
        result = apply_media_filter(items, "all")
        assert len(result) == 2


# ═══════════════════════════════════════════
# Feature 3: Additional Sort Options
# ═══════════════════════════════════════════


class TestAdditionalSort:
    """Sort by file_size, face_count."""

    def test_sort_by_file_size_desc(self):
        from bpp.web.filters import apply_sort

        items = [
            {"filepath": "a.jpg", "file_size": 1000},
            {"filepath": "b.jpg", "file_size": 5000},
            {"filepath": "c.jpg", "file_size": 3000},
        ]
        result = apply_sort(items, "size-desc")
        assert [p["filepath"] for p in result] == ["b.jpg", "c.jpg", "a.jpg"]

    def test_sort_by_file_size_asc(self):
        from bpp.web.filters import apply_sort

        items = [
            {"filepath": "a.jpg", "file_size": 1000},
            {"filepath": "b.jpg", "file_size": 5000},
            {"filepath": "c.jpg", "file_size": 3000},
        ]
        result = apply_sort(items, "size-asc")
        assert [p["filepath"] for p in result] == ["a.jpg", "c.jpg", "b.jpg"]

    def test_sort_by_face_count_desc(self):
        from bpp.web.filters import apply_sort

        items = [
            {"filepath": "a.jpg", "face_count": 0},
            {"filepath": "b.jpg", "face_count": 3},
            {"filepath": "c.jpg", "face_count": 1},
        ]
        result = apply_sort(items, "faces-desc")
        assert [p["filepath"] for p in result] == ["b.jpg", "c.jpg", "a.jpg"]

    def test_sort_preserves_existing_options(self):
        """Existing sorts (date, score, name) still work."""
        from bpp.web.filters import apply_sort

        items = [
            {"filepath": "b.jpg", "aggregate_score": 0.8},
            {"filepath": "a.jpg", "aggregate_score": 0.3},
        ]
        result = apply_sort(items, "score-desc")
        assert [p["filepath"] for p in result] == ["b.jpg", "a.jpg"]


# ═══════════════════════════════════════════
# Feature 4: Date/Time Adjustment
# ═══════════════════════════════════════════


class TestDateAdjust:
    """Adjust a photo's date in the DB."""

    def test_update_photo_date(self):
        from bpp.db.photos import update_photo_date

        conn = _make_db()
        _insert_photo(conn, "/lib/a.jpg", date="2024-01-15T10:00:00")
        photo = conn.execute("SELECT id FROM photos WHERE filepath='/lib/a.jpg'").fetchone()

        update_photo_date(conn, photo["id"], "2025-06-20T14:30:00")

        row = conn.execute(
            "SELECT date, date_day, date_month FROM photos WHERE id=?", (photo["id"],)
        ).fetchone()
        assert row["date"] == "2025-06-20T14:30:00"
        assert row["date_day"] == "2025-06-20"
        assert row["date_month"] == "2025-06"

    def test_update_photo_date_invalid(self):
        from bpp.db.photos import update_photo_date

        conn = _make_db()
        _insert_photo(conn, "/lib/a.jpg")

        with pytest.raises(ValueError, match="Invalid date"):
            update_photo_date(conn, 1, "not-a-date")

    def test_update_photo_date_nonexistent(self):
        from bpp.db.photos import update_photo_date

        conn = _make_db()
        with pytest.raises(ValueError, match="not found"):
            update_photo_date(conn, 999, "2025-01-01T00:00:00")

    def test_date_adjust_api_endpoint(self, tmp_path):
        """POST /api/photos/<id>/date updates the date."""
        from unittest.mock import patch

        from bpp.web.app import create_app

        conn = _make_db()
        _insert_photo(conn, "/lib/a.jpg", date="2024-01-01T00:00:00")
        photo_id = conn.execute("SELECT id FROM photos WHERE filepath='/lib/a.jpg'").fetchone()[
            "id"
        ]

        app = create_app()
        app.config["TESTING"] = True

        class FakeCtx:
            def get_conn(self):
                return conn

            def invalidate_analysis(self):
                pass

            state: dict = {"analysis": None}  # noqa: RUF012

        with (
            patch("bpp.web.bp_photos_manage.get_ctx", return_value=FakeCtx()),
            app.test_client() as client,
        ):
            resp = client.post(
                f"/api/v1/photos/{photo_id}/date",
                json={"date": "2025-12-25T10:00:00"},
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["date"] == "2025-12-25T10:00:00"


# ═══════════════════════════════════════════
# Feature 5: Animated Video Hover Thumbnail
# ═══════════════════════════════════════════


class TestVideoHoverPreview:
    """Video hover preview endpoint returns a short clip or sprite sheet."""

    def test_video_preview_endpoint_exists(self):
        """GET /api/video/preview/<hash> should exist."""
        from bpp.web.app import create_app

        app = create_app()
        rules = [r.rule for r in app.url_map.iter_rules()]
        assert "/api/v1/video/preview/<path_hash>" in rules

    def test_video_preview_sprite_generation(self, tmp_path):
        """generate_video_sprite extracts N frames into a grid."""
        import inspect

        from bpp.utils.video import generate_video_sprite

        sig = inspect.signature(generate_video_sprite)
        params = list(sig.parameters.keys())
        assert "filepath" in params
        assert "output_path" in params
