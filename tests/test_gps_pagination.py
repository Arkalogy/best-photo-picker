"""M4 regression: GPS map endpoint must paginate at the DB layer.

Before the fix, /api/v1/photos/map fetched ALL GPS photos into memory then
sliced in Python. On a 100k-photo library this materialised the full result
set unnecessarily. After the fix, get_photos_with_gps() accepts limit/offset
and the endpoint passes them through, so the DB does the slicing.

Tests verify:
- count_photos_with_gps returns correct totals (no limit effect)
- get_photos_with_gps with limit/offset returns only the requested slice
- The HTTP endpoint returns consistent total + paginated photos
"""

from __future__ import annotations

import os

import pytest

from bpp.db.connection import init_db
from bpp.db.photos import count_photos_with_gps, get_photos_with_gps, upsert_photo


@pytest.fixture()
def gps_conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = init_db(db_path)
    # Insert 10 photos with GPS, 5 without
    for i in range(10):
        upsert_photo(
            conn,
            {
                "filepath": f"/tmp/gps_{i}.jpg",
                "gps_lat": 40.0 + i * 0.1,
                "gps_lon": -74.0 + i * 0.1,
                "aggregate_score": 0.5,
                "missing": 0,
            },
        )
    for i in range(5):
        upsert_photo(
            conn,
            {
                "filepath": f"/tmp/nogps_{i}.jpg",
                "aggregate_score": 0.5,
                "missing": 0,
            },
        )
    conn.commit()
    yield conn
    conn.close()


class TestCountPhotosWithGps:
    def test_counts_only_gps_photos(self, gps_conn):
        assert count_photos_with_gps(gps_conn) == 10

    def test_count_unaffected_by_limit(self, gps_conn):
        """count_photos_with_gps returns total, not a paged subset."""
        total = count_photos_with_gps(gps_conn)
        paged = get_photos_with_gps(gps_conn, limit=3)
        assert total == 10
        assert len(paged) == 3  # limit respected in data, not count


class TestGetPhotosWithGpsPagination:
    def test_no_limit_returns_all(self, gps_conn):
        rows = get_photos_with_gps(gps_conn)
        assert len(rows) == 10

    def test_limit(self, gps_conn):
        rows = get_photos_with_gps(gps_conn, limit=4)
        assert len(rows) == 4

    def test_offset(self, gps_conn):
        page1 = get_photos_with_gps(gps_conn, limit=3, offset=0)
        page2 = get_photos_with_gps(gps_conn, limit=3, offset=3)
        # Pages don't overlap
        ids1 = {r["id"] for r in page1}
        ids2 = {r["id"] for r in page2}
        assert not ids1 & ids2
        # Together they cover distinct rows
        assert len(ids1 | ids2) == 6

    def test_offset_beyond_end(self, gps_conn):
        rows = get_photos_with_gps(gps_conn, limit=10, offset=100)
        assert rows == []

    def test_rows_have_gps_fields(self, gps_conn):
        rows = get_photos_with_gps(gps_conn, limit=1)
        assert len(rows) == 1
        r = rows[0]
        assert "gps_lat" in r and "gps_lon" in r
        assert r["gps_lat"] is not None


class TestMapApiPagination:
    """HTTP-level test: endpoint passes limit/offset to DB layer."""

    @pytest.fixture()
    def map_app(self, tmp_path):
        from bpp.web.app import create_app

        wd = str(tmp_path / "wd")
        os.makedirs(wd)
        app = create_app(workdir=wd)
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.db.photos import upsert_photo as up
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            for i in range(12):
                up(
                    conn,
                    {
                        "filepath": f"/tmp/m_{i}.jpg",
                        "gps_lat": 40.0 + i * 0.1,
                        "gps_lon": -74.0 + i * 0.1,
                        "aggregate_score": 0.5,
                        "missing": 0,
                    },
                )
            conn.commit()
        return app

    def test_total_reflects_all_photos(self, map_app):
        with map_app.test_client() as c:
            r = c.get("/api/v1/photos/map?limit=5&offset=0")
            data = r.get_json()
            assert r.status_code == 200
            assert data["total"] == 12
            assert len(data["photos"]) == 5
            assert data["has_more"] is True

    def test_last_page_has_no_more(self, map_app):
        with map_app.test_client() as c:
            r = c.get("/api/v1/photos/map?limit=5&offset=10")
            data = r.get_json()
            assert len(data["photos"]) == 2
            assert data["has_more"] is False

    def test_pages_non_overlapping(self, map_app):
        with map_app.test_client() as c:
            p1 = c.get("/api/v1/photos/map?limit=6&offset=0").get_json()
            p2 = c.get("/api/v1/photos/map?limit=6&offset=6").get_json()
        ids1 = {p["id"] for p in p1["photos"]}
        ids2 = {p["id"] for p in p2["photos"]}
        assert not ids1 & ids2
        assert len(ids1 | ids2) == 12
