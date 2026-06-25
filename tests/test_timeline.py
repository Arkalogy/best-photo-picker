"""TDD tests for date range browsing (P0-3): timeline DB functions and API."""

from __future__ import annotations

import os
import tempfile

import pytest
from PIL import Image

# ── Helpers ──


def _make_photo(tmp_path, name="photo.jpg"):
    """Create a minimal JPEG and return its path."""
    path = str(tmp_path / name)
    Image.new("RGB", (10, 10)).save(path, "JPEG")
    return path


# ── DB: get_date_distribution ──


class TestDateDistribution:
    """Tests for get_date_distribution() aggregation function."""

    @pytest.fixture()
    def db(self, tmp_path):
        from bpp.db.connection import init_db

        return init_db(str(tmp_path / "test.db"))

    def _add(self, db, path, date, month, deleted=False):
        from bpp.db.photos import upsert_photo

        pid = upsert_photo(db, {"filepath": path, "date": date, "date_month": month})
        if deleted:
            db.execute("UPDATE photos SET deleted_at=datetime('now') WHERE id=?", (pid,))
            db.commit()
        return pid

    def test_empty_db(self, db):
        from bpp.db.photos import get_date_distribution

        assert get_date_distribution(db) == []

    def test_single_month(self, db, tmp_path):
        from bpp.db.photos import get_date_distribution

        self._add(db, _make_photo(tmp_path, "a.jpg"), "2024-01-15 10:00:00", "2024-01")
        result = get_date_distribution(db)
        assert result == [{"month": "2024-01", "count": 1}]

    def test_multiple_months_aggregated(self, db, tmp_path):
        from bpp.db.photos import get_date_distribution

        self._add(db, _make_photo(tmp_path, "a.jpg"), "2024-01-15 10:00:00", "2024-01")
        self._add(db, _make_photo(tmp_path, "b.jpg"), "2024-01-20 12:00:00", "2024-01")
        self._add(db, _make_photo(tmp_path, "c.jpg"), "2024-03-05 09:00:00", "2024-03")
        result = get_date_distribution(db)
        assert len(result) == 2
        assert result[0] == {"month": "2024-01", "count": 2}
        assert result[1] == {"month": "2024-03", "count": 1}

    def test_excludes_deleted(self, db, tmp_path):
        from bpp.db.photos import get_date_distribution

        self._add(db, _make_photo(tmp_path, "a.jpg"), "2024-01-15 10:00:00", "2024-01")
        self._add(
            db,
            _make_photo(tmp_path, "b.jpg"),
            "2024-01-20 12:00:00",
            "2024-01",
            deleted=True,
        )
        result = get_date_distribution(db)
        assert result == [{"month": "2024-01", "count": 1}]

    def test_excludes_missing(self, db):
        from bpp.db.photos import get_date_distribution, upsert_photo

        upsert_photo(
            db,
            {
                "filepath": "/nonexistent/x.jpg",
                "date": "2024-02-01 10:00:00",
                "date_month": "2024-02",
            },
        )
        assert get_date_distribution(db) == []

    def test_null_month_excluded(self, db, tmp_path):
        from bpp.db.photos import get_date_distribution

        self._add(db, _make_photo(tmp_path, "a.jpg"), None, None)
        assert get_date_distribution(db) == []

    def test_ordered_chronologically(self, db, tmp_path):
        from bpp.db.photos import get_date_distribution

        self._add(db, _make_photo(tmp_path, "a.jpg"), "2024-06-01 10:00:00", "2024-06")
        self._add(db, _make_photo(tmp_path, "b.jpg"), "2024-01-01 10:00:00", "2024-01")
        result = get_date_distribution(db)
        assert [r["month"] for r in result] == ["2024-01", "2024-06"]

    def test_album_filter(self, db, tmp_path):
        from bpp.db.albums import add_photos_to_album, create_album
        from bpp.db.photos import get_date_distribution

        id1 = self._add(db, _make_photo(tmp_path, "a.jpg"), "2024-01-01 10:00:00", "2024-01")
        self._add(db, _make_photo(tmp_path, "b.jpg"), "2024-02-01 10:00:00", "2024-02")
        album_id = create_album(db, "Test")
        add_photos_to_album(db, album_id, [id1])
        result = get_date_distribution(db, album_id=album_id)
        assert result == [{"month": "2024-01", "count": 1}]


# ── DB: get_photos_by_date_range ──


class TestDateRangeFilter:
    """Tests for get_photos_by_date_range() query function."""

    @pytest.fixture()
    def db(self, tmp_path):
        from bpp.db.connection import init_db

        return init_db(str(tmp_path / "test.db"))

    def _add(self, db, path, date, month):
        from bpp.db.photos import upsert_photo

        return upsert_photo(db, {"filepath": path, "date": date, "date_month": month})

    def test_filters_within_range(self, db, tmp_path):
        from bpp.db.photos import get_photos_by_date_range

        # Use ISO 8601 T-separator — matches the real photo date format written by EXIF parsing.
        self._add(db, _make_photo(tmp_path, "a.jpg"), "2024-01-15T10:00:00", "2024-01")
        self._add(db, _make_photo(tmp_path, "b.jpg"), "2024-03-05T09:00:00", "2024-03")
        self._add(db, _make_photo(tmp_path, "c.jpg"), "2024-06-10T14:00:00", "2024-06")
        result = get_photos_by_date_range(db, "2024-01-01T00:00:00", "2024-03-31T23:59:59")
        assert len(result) == 2

    def test_empty_range(self, db, tmp_path):
        from bpp.db.photos import get_photos_by_date_range

        self._add(db, _make_photo(tmp_path, "a.jpg"), "2024-01-15T10:00:00", "2024-01")
        assert get_photos_by_date_range(db, "2025-01-01T00:00:00", "2025-12-31T23:59:59") == []

    def test_iso_t_separator_not_missed_by_space_bound(self, db, tmp_path):
        """Regression: space-delimited end bound misses T-delimited photo timestamps.

        'T' (ASCII 84) > ' ' (ASCII 32), so '2024-01-15T10:00:00' > '2024-01-15 23:59:59'
        and the photo would be excluded by a space-delimited range.
        """
        from bpp.db.photos import get_photos_by_date_range

        self._add(db, _make_photo(tmp_path, "a.jpg"), "2024-01-15T10:00:00", "2024-01")
        # Space-delimited bounds (the old bug): must return 0
        assert get_photos_by_date_range(db, "2024-01-15 00:00:00", "2024-01-15 23:59:59") == [], (
            "space-delimited bound should NOT match T-delimited timestamps"
        )
        # T-delimited bounds (the fix): must return 1
        result = get_photos_by_date_range(db, "2024-01-15T00:00:00", "2024-01-15T23:59:59")
        assert len(result) == 1, "T-delimited bound must match T-delimited timestamps"

    def test_excludes_deleted(self, db, tmp_path):
        from bpp.db.photos import get_photos_by_date_range

        path = _make_photo(tmp_path, "a.jpg")
        pid = self._add(db, path, "2024-01-15T10:00:00", "2024-01")
        db.execute("UPDATE photos SET deleted_at=datetime('now') WHERE id=?", (pid,))
        db.commit()
        assert get_photos_by_date_range(db, "2024-01-01T00:00:00", "2024-12-31T23:59:59") == []

    def test_ordered_by_date(self, db, tmp_path):
        from bpp.db.photos import get_photos_by_date_range

        self._add(db, _make_photo(tmp_path, "a.jpg"), "2024-03-05T09:00:00", "2024-03")
        self._add(db, _make_photo(tmp_path, "b.jpg"), "2024-01-15T10:00:00", "2024-01")
        result = get_photos_by_date_range(db, "2024-01-01T00:00:00", "2024-12-31T23:59:59")
        assert result[0]["date_month"] == "2024-01"
        assert result[1]["date_month"] == "2024-03"


# ── API: /api/photos/timeline ──


class TestTimelineAPI:
    """Tests for the timeline API endpoint."""

    @pytest.fixture()
    def app(self, tmp_path):
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir, exist_ok=True)
        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        return app

    @pytest.fixture()
    def client(self, app):
        return app.test_client()

    def test_endpoint_exists(self, client):
        resp = client.get("/api/v1/photos/timeline")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "months" in data

    def test_returns_distribution(self, client, app):
        with app.app_context():
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            paths = []
            for _name, date, month in [
                ("a.jpg", "2024-01-15 10:00:00", "2024-01"),
                ("b.jpg", "2024-01-20 12:00:00", "2024-01"),
                ("c.jpg", "2024-06-10 14:00:00", "2024-06"),
            ]:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                    Image.new("RGB", (10, 10)).save(f, "JPEG")
                    paths.append(f.name)
                upsert_photo(
                    conn,
                    {"filepath": paths[-1], "date": date, "date_month": month},
                )
        resp = client.get("/api/v1/photos/timeline")
        data = resp.get_json()
        assert len(data["months"]) == 2
        jan = next(m for m in data["months"] if m["month"] == "2024-01")
        assert jan["count"] == 2
        for p in paths:
            os.unlink(p)

    def test_with_album_filter(self, client, app):
        with app.app_context():
            from bpp.db.albums import add_photos_to_album, create_album
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            paths = []
            ids = []
            for _name, date, month in [
                ("a.jpg", "2024-01-15 10:00:00", "2024-01"),
                ("b.jpg", "2024-06-10 14:00:00", "2024-06"),
            ]:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                    Image.new("RGB", (10, 10)).save(f, "JPEG")
                    paths.append(f.name)
                ids.append(
                    upsert_photo(
                        conn,
                        {"filepath": paths[-1], "date": date, "date_month": month},
                    )
                )
            album_id = create_album(conn, "TestAlbum")
            add_photos_to_album(conn, album_id, [ids[0]])
        resp = client.get(f"/api/v1/photos/timeline?album_id={album_id}")
        data = resp.get_json()
        assert len(data["months"]) == 1
        assert data["months"][0]["month"] == "2024-01"
        for p in paths:
            os.unlink(p)


class TestTimeMonthsAPI:
    """Tests for the /api/albums/time/months endpoint."""

    @pytest.fixture()
    def app(self, tmp_path):
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir, exist_ok=True)
        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        return app

    @pytest.fixture()
    def client(self, app):
        return app.test_client()

    def test_missing_year_returns_400(self, client):
        resp = client.get("/api/v1/albums/time/months")
        assert resp.status_code == 400

    def test_invalid_year_returns_400(self, client):
        resp = client.get("/api/v1/albums/time/months?year=abc")
        assert resp.status_code == 400

    def test_returns_month_breakdown(self, client, app):
        with app.app_context():
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            paths = []
            for _name, date in [
                ("a.jpg", "2024-01-15 10:00:00"),
                ("b.jpg", "2024-01-20 12:00:00"),
                ("c.jpg", "2024-06-10 14:00:00"),
                ("d.jpg", "2024-06-11 14:00:00"),
                ("e.jpg", "2024-06-12 14:00:00"),
            ]:
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                    Image.new("RGB", (10, 10)).save(f, "JPEG")
                    paths.append(f.name)
                upsert_photo(conn, {"filepath": paths[-1], "date": date})
        resp = client.get("/api/v1/albums/time/months?year=2024")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["year"] == "2024"
        assert len(data["months"]) == 2
        jan = next(m for m in data["months"] if m["month"] == 1)
        jun = next(m for m in data["months"] if m["month"] == 6)
        assert jan["count"] == 2
        assert jun["count"] == 3
        for p in paths:
            os.unlink(p)

    def test_empty_year_returns_empty(self, client):
        resp = client.get("/api/v1/albums/time/months?year=1999")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["months"] == []
