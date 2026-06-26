"""Tests for On This Day feature."""

from __future__ import annotations

from datetime import date

import pytest

from bpp.db.calendar import get_on_this_day
from bpp.db.connection import init_db


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = init_db(db_path)
    yield c
    c.close()


def _insert(conn, filepath, date_str, score=0.5, *, face_count=0):
    fname = filepath.split("/")[-1] if filepath else "unknown"
    conn.execute(
        "INSERT INTO photos (filepath, sha256, date, date_day, date_month, aggregate_score, "
        "face_count, original_filename, file_size, file_mtime) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            filepath,
            filepath,
            date_str,
            date_str[:10] if date_str else None,
            date_str[:7] if date_str else None,
            score,
            face_count,
            fname,
            1000,
            0.0,
        ),
    )
    conn.commit()


class TestGetOnThisDay:
    """Test On This Day query — same month+day across different years."""

    def test_empty_library(self, conn):
        result = get_on_this_day(conn, month=6, day=15)
        assert result == []

    def test_finds_photos_from_past_years(self, conn):
        _insert(conn, "/2020.jpg", "2020-06-15 10:00:00", 0.8)
        _insert(conn, "/2022.jpg", "2022-06-15 14:00:00", 0.6)
        _insert(conn, "/2024.jpg", "2024-06-15 09:00:00", 0.9)
        result = get_on_this_day(conn, month=6, day=15)
        assert len(result) == 3
        # Should be ordered by year descending (most recent first)
        assert result[0]["year"] == 2024
        assert result[1]["year"] == 2022
        assert result[2]["year"] == 2020

    def test_excludes_current_year(self, conn):
        """On This Day is about nostalgia — current year photos aren't memories yet."""
        today = date.today()
        _insert(conn, "/old.jpg", f"2020-{today.month:02d}-{today.day:02d} 10:00:00", 0.8)
        _insert(conn, "/new.jpg", f"{today.year}-{today.month:02d}-{today.day:02d} 10:00:00", 0.7)
        result = get_on_this_day(conn, month=today.month, day=today.day)
        assert len(result) == 1
        assert result[0]["year"] == 2020

    def test_excludes_different_dates(self, conn):
        _insert(conn, "/match.jpg", "2020-06-15 10:00:00", 0.8)
        _insert(conn, "/other_day.jpg", "2020-06-16 10:00:00", 0.5)
        _insert(conn, "/other_month.jpg", "2020-07-15 10:00:00", 0.5)
        result = get_on_this_day(conn, month=6, day=15)
        assert len(result) == 1

    def test_excludes_deleted(self, conn):
        _insert(conn, "/a.jpg", "2020-06-15 10:00:00")
        _insert(conn, "/b.jpg", "2021-06-15 10:00:00")
        conn.execute("UPDATE photos SET deleted_at = datetime('now') WHERE filepath='/b.jpg'")
        conn.commit()
        result = get_on_this_day(conn, month=6, day=15)
        assert len(result) == 1

    def test_excludes_hidden(self, conn):
        _insert(conn, "/a.jpg", "2020-06-15 10:00:00")
        _insert(conn, "/b.jpg", "2021-06-15 10:00:00")
        conn.execute("UPDATE photos SET hidden_at = datetime('now') WHERE filepath='/b.jpg'")
        conn.commit()
        result = get_on_this_day(conn, month=6, day=15)
        assert len(result) == 1

    def test_excludes_missing(self, conn):
        _insert(conn, "/a.jpg", "2020-06-15 10:00:00")
        _insert(conn, "/b.jpg", "2021-06-15 10:00:00")
        conn.execute("UPDATE photos SET missing = 1 WHERE filepath='/b.jpg'")
        conn.commit()
        result = get_on_this_day(conn, month=6, day=15)
        assert len(result) == 1

    def test_groups_by_year(self, conn):
        """Each year entry should have photos list and hero (best-scored)."""
        _insert(conn, "/a.jpg", "2020-06-15 10:00:00", 0.3)
        _insert(conn, "/b.jpg", "2020-06-15 14:00:00", 0.9)
        _insert(conn, "/c.jpg", "2020-06-15 18:00:00", 0.5)
        result = get_on_this_day(conn, month=6, day=15)
        assert len(result) == 1
        entry = result[0]
        assert entry["year"] == 2020
        assert entry["count"] == 3
        assert entry["hero_hash"] == "/b.jpg"  # highest scored
        assert len(entry["photos"]) == 3

    def test_photo_fields(self, conn):
        """Each photo in the result should have expected fields."""
        _insert(conn, "/a.jpg", "2020-06-15 10:00:00", 0.8, face_count=2)
        result = get_on_this_day(conn, month=6, day=15)
        photo = result[0]["photos"][0]
        assert "id" in photo
        assert photo["hash"] == "/a.jpg"
        assert photo["score"] == 0.8
        assert "filename" in photo
        assert "date" in photo

    def test_years_ago_label(self, conn):
        """Each year entry should include a 'years_ago' count."""
        today = date.today()
        past_year = today.year - 3
        _insert(conn, "/a.jpg", f"{past_year}-{today.month:02d}-{today.day:02d} 10:00:00")
        result = get_on_this_day(conn, month=today.month, day=today.day)
        assert len(result) == 1
        assert result[0]["years_ago"] == 3

    def test_limit_parameter(self, conn):
        """Should respect max_per_year limit on photos per year."""
        for i in range(10):
            _insert(conn, f"/p{i}.jpg", f"2020-06-15 {10 + i}:00:00", 0.1 * i)
        result = get_on_this_day(conn, month=6, day=15, max_per_year=3)
        assert len(result) == 1
        # Should return top 3 by score
        assert result[0]["count"] == 10  # total count
        assert len(result[0]["photos"]) == 3
        # Photos should be best-scored first
        scores = [p["score"] for p in result[0]["photos"]]
        assert scores == sorted(scores, reverse=True)

    def test_default_uses_today(self, conn):
        """When no month/day given, should use today's date."""
        today = date.today()
        past_year = today.year - 2
        _insert(conn, "/a.jpg", f"{past_year}-{today.month:02d}-{today.day:02d} 10:00:00")
        result = get_on_this_day(conn)
        assert len(result) == 1


class TestOnThisDayBlueprint:
    """Test /api/on-this-day endpoint."""

    @pytest.fixture
    def client(self, tmp_path):
        from bpp.web.app import create_app

        app = create_app(library_path=str(tmp_path))
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_endpoint_exists(self, client):
        resp = client.get("/api/v1/on-this-day")
        assert resp.status_code == 200

    def test_response_shape(self, client):
        resp = client.get("/api/v1/on-this-day")
        data = resp.get_json()
        assert "years" in data
        assert "month" in data
        assert "day" in data

    def test_custom_date(self, client):
        resp = client.get("/api/v1/on-this-day?month=6&day=15")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["month"] == 6
        assert data["day"] == 15

    def test_invalid_month(self, client):
        resp = client.get("/api/v1/on-this-day?month=13&day=1")
        assert resp.status_code == 400

    def test_invalid_day(self, client):
        resp = client.get("/api/v1/on-this-day?month=6&day=32")
        assert resp.status_code == 400
