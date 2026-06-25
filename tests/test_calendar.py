"""Tests for calendar view backend."""

from __future__ import annotations

import pytest

from bpp.db.calendar import get_daily_counts, get_year_months
from bpp.db.connection import init_db


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = init_db(db_path)
    yield c
    c.close()


def _insert(conn, filepath, date, score=0.5):
    fname = filepath.split("/")[-1] if filepath else "unknown"
    conn.execute(
        "INSERT INTO photos (filepath, sha256, date, date_month, aggregate_score, "
        "original_filename, file_size, file_mtime) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (filepath, filepath, date, date[:7] if date else None, score, fname, 1000, 0.0),
    )
    conn.commit()


class TestGetDailyCounts:
    """Test daily photo counts for a given month."""

    def test_empty(self, conn):
        result = get_daily_counts(conn, 2024, 6)
        assert result == []

    def test_single_day(self, conn):
        _insert(conn, "/a.jpg", "2024-06-15 10:00:00")
        _insert(conn, "/b.jpg", "2024-06-15 14:00:00")
        result = get_daily_counts(conn, 2024, 6)
        assert len(result) == 1
        assert result[0]["day"] == 15
        assert result[0]["count"] == 2

    def test_multiple_days(self, conn):
        _insert(conn, "/a.jpg", "2024-06-10 10:00:00")
        _insert(conn, "/b.jpg", "2024-06-15 14:00:00")
        _insert(conn, "/c.jpg", "2024-06-15 16:00:00")
        _insert(conn, "/d.jpg", "2024-06-28 09:00:00")
        result = get_daily_counts(conn, 2024, 6)
        assert len(result) == 3
        days = {r["day"]: r["count"] for r in result}
        assert days[10] == 1
        assert days[15] == 2
        assert days[28] == 1

    def test_different_month_excluded(self, conn):
        _insert(conn, "/a.jpg", "2024-06-15 10:00:00")
        _insert(conn, "/b.jpg", "2024-07-15 14:00:00")
        result = get_daily_counts(conn, 2024, 6)
        assert len(result) == 1
        assert result[0]["day"] == 15

    def test_deleted_excluded(self, conn):
        _insert(conn, "/a.jpg", "2024-06-15 10:00:00")
        _insert(conn, "/b.jpg", "2024-06-16 10:00:00")
        conn.execute("UPDATE photos SET deleted_at = datetime('now') WHERE filepath='/b.jpg'")
        conn.commit()
        result = get_daily_counts(conn, 2024, 6)
        assert len(result) == 1

    def test_hidden_excluded(self, conn):
        _insert(conn, "/a.jpg", "2024-06-15 10:00:00")
        _insert(conn, "/b.jpg", "2024-06-16 10:00:00")
        conn.execute("UPDATE photos SET hidden_at = datetime('now') WHERE filepath='/b.jpg'")
        conn.commit()
        result = get_daily_counts(conn, 2024, 6)
        assert len(result) == 1

    def test_top_photo_hash(self, conn):
        """Each day should include the hash of the top-scored photo."""
        _insert(conn, "/a.jpg", "2024-06-15 10:00:00", 0.3)
        _insert(conn, "/b.jpg", "2024-06-15 14:00:00", 0.9)
        result = get_daily_counts(conn, 2024, 6)
        assert len(result) == 1
        assert result[0]["top_hash"] == "/b.jpg"  # sha256 = filepath in test

    def test_padded_month(self, conn):
        """Single-digit months should work correctly."""
        _insert(conn, "/a.jpg", "2024-03-05 10:00:00")
        result = get_daily_counts(conn, 2024, 3)
        assert len(result) == 1
        assert result[0]["day"] == 5

    def test_short_date_excluded(self, conn):
        """Photos with short/malformed dates should not break counts."""
        _insert(conn, "/a.jpg", "2024-06-15 10:00:00")
        # Insert photo with truncated date directly
        conn.execute(
            "INSERT INTO photos (filepath, sha256, date, date_month, aggregate_score, "
            "original_filename, file_size, file_mtime) VALUES (?,?,?,?,?,?,?,?)",
            ("/b.jpg", "b_hash", "2024-06", "2024-06", 0.5, "b.jpg", 1000, 0.0),
        )
        conn.commit()
        result = get_daily_counts(conn, 2024, 6)
        # Only the well-formed date should appear
        assert len(result) == 1
        assert result[0]["day"] == 15


class TestGetYearMonths:
    """Test getting all year-month combos that have photos."""

    def test_empty(self, conn):
        result = get_year_months(conn)
        assert result == []

    def test_single_month(self, conn):
        _insert(conn, "/a.jpg", "2024-06-15 10:00:00")
        result = get_year_months(conn)
        assert result == [{"year": 2024, "month": 6, "count": 1}]

    def test_multiple_months(self, conn):
        _insert(conn, "/a.jpg", "2024-01-15 10:00:00")
        _insert(conn, "/b.jpg", "2024-06-15 14:00:00")
        _insert(conn, "/c.jpg", "2024-06-20 14:00:00")
        result = get_year_months(conn)
        assert len(result) == 2
        assert result[0] == {"year": 2024, "month": 1, "count": 1}
        assert result[1] == {"year": 2024, "month": 6, "count": 2}

    def test_deleted_excluded(self, conn):
        _insert(conn, "/a.jpg", "2024-06-15 10:00:00")
        conn.execute("UPDATE photos SET deleted_at = datetime('now')")
        conn.commit()
        result = get_year_months(conn)
        assert result == []

    def test_sorted_chronologically(self, conn):
        _insert(conn, "/a.jpg", "2023-12-15 10:00:00")
        _insert(conn, "/b.jpg", "2024-01-15 10:00:00")
        _insert(conn, "/c.jpg", "2024-06-15 10:00:00")
        result = get_year_months(conn)
        assert len(result) == 3
        assert result[0]["year"] == 2023
        assert result[0]["month"] == 12
        assert result[2]["year"] == 2024
        assert result[2]["month"] == 6


class TestCalendarBlueprint:
    """Test calendar API endpoints."""

    @pytest.fixture
    def client(self, tmp_path):
        from bpp.web.app import create_app

        app = create_app(library_path=str(tmp_path))
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_get_calendar_months(self, client):
        resp = client.get("/api/v1/calendar/months")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "months" in data

    def test_get_calendar_days(self, client):
        resp = client.get("/api/v1/calendar/days?year=2024&month=6")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "days" in data

    def test_get_calendar_days_missing_params(self, client):
        resp = client.get("/api/v1/calendar/days")
        assert resp.status_code == 400

    def test_get_calendar_day_photos(self, client):
        resp = client.get("/api/v1/calendar/photos?date=2024-06-15")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "photos" in data

    def test_invalid_month(self, client):
        resp = client.get("/api/v1/calendar/days?year=2024&month=13")
        assert resp.status_code == 400

    def test_invalid_year(self, client):
        resp = client.get("/api/v1/calendar/days?year=0&month=6")
        assert resp.status_code == 400
