"""TDD tests for P0-5: GPS / Map view — DB functions and API."""

from __future__ import annotations

import json
import os
import tempfile

import pytest
from PIL import Image


def _make_photo(tmp_path, name="photo.jpg"):
    path = str(tmp_path / name)
    Image.new("RGB", (10, 10)).save(path, "JPEG")
    return path


class TestPhotosWithGPS:
    """Tests for get_photos_with_gps() query function."""

    @pytest.fixture()
    def db(self, tmp_path):
        from bpp.db.connection import init_db

        return init_db(str(tmp_path / "test.db"))

    def _add(self, db, path, gps_lat=None, gps_lon=None, deleted=False):
        from bpp.db.photos import upsert_photo

        exif = {}
        if gps_lat is not None:
            exif["gps_lat"] = gps_lat
        if gps_lon is not None:
            exif["gps_lon"] = gps_lon
        pid = upsert_photo(
            db,
            {
                "filepath": path,
                "exif_json": json.dumps(exif) if exif else None,
            },
        )
        if deleted:
            db.execute("UPDATE photos SET deleted_at=datetime('now') WHERE id=?", (pid,))
            db.commit()
        return pid

    def test_empty_db(self, db):
        from bpp.db.photos import get_photos_with_gps

        assert get_photos_with_gps(db) == []

    def test_photo_with_gps(self, db, tmp_path):
        from bpp.db.photos import get_photos_with_gps

        self._add(db, _make_photo(tmp_path, "a.jpg"), 40.7128, -74.0060)
        result = get_photos_with_gps(db)
        assert len(result) == 1
        assert result[0]["gps_lat"] == pytest.approx(40.7128)
        assert result[0]["gps_lon"] == pytest.approx(-74.0060)

    def test_excludes_no_gps(self, db, tmp_path):
        from bpp.db.photos import get_photos_with_gps

        self._add(db, _make_photo(tmp_path, "a.jpg"), 40.7128, -74.0060)
        self._add(db, _make_photo(tmp_path, "b.jpg"))  # no GPS
        result = get_photos_with_gps(db)
        assert len(result) == 1

    def test_excludes_deleted(self, db, tmp_path):
        from bpp.db.photos import get_photos_with_gps

        self._add(db, _make_photo(tmp_path, "a.jpg"), 40.7128, -74.0060, deleted=True)
        assert get_photos_with_gps(db) == []

    def test_multiple_photos(self, db, tmp_path):
        from bpp.db.photos import get_photos_with_gps

        self._add(db, _make_photo(tmp_path, "a.jpg"), 40.7128, -74.0060)
        self._add(db, _make_photo(tmp_path, "b.jpg"), 48.8566, 2.3522)
        self._add(db, _make_photo(tmp_path, "c.jpg"), 35.6762, 139.6503)
        result = get_photos_with_gps(db)
        assert len(result) == 3

    def test_album_filter(self, db, tmp_path):
        from bpp.db.albums import add_photos_to_album, create_album
        from bpp.db.photos import get_photos_with_gps

        id1 = self._add(db, _make_photo(tmp_path, "a.jpg"), 40.7128, -74.0060)
        self._add(db, _make_photo(tmp_path, "b.jpg"), 48.8566, 2.3522)
        album_id = create_album(db, "NYC")
        add_photos_to_album(db, album_id, [id1])
        result = get_photos_with_gps(db, album_id=album_id)
        assert len(result) == 1
        assert result[0]["gps_lat"] == pytest.approx(40.7128)


class TestMapAPI:
    """Tests for the map API endpoint."""

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

    def _add_photo(self, app, gps_lat=None, gps_lon=None):
        with app.app_context():
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                Image.new("RGB", (10, 10)).save(f, "JPEG")
                path = f.name
            exif = {}
            if gps_lat is not None:
                exif["gps_lat"] = gps_lat
            if gps_lon is not None:
                exif["gps_lon"] = gps_lon
            photo_id = upsert_photo(
                conn,
                {
                    "filepath": path,
                    "exif_json": json.dumps(exif) if exif else None,
                },
            )
            return photo_id, path

    def test_endpoint_exists(self, client):
        resp = client.get("/api/v1/photos/map")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "photos" in data

    def test_returns_gps_photos(self, client, app):
        _, p1 = self._add_photo(app, 40.7128, -74.0060)
        _, p2 = self._add_photo(app)  # no GPS
        resp = client.get("/api/v1/photos/map")
        data = resp.get_json()
        assert len(data["photos"]) == 1
        assert data["photos"][0]["gps_lat"] == pytest.approx(40.7128)
        os.unlink(p1)
        os.unlink(p2)

    def test_returns_thumb_hash(self, client, app):
        _, p1 = self._add_photo(app, 40.7128, -74.0060)
        resp = client.get("/api/v1/photos/map")
        data = resp.get_json()
        assert "thumb_hash" in data["photos"][0]
        os.unlink(p1)


class TestMapPagination:
    """/api/v1/photos/map paginates with ?limit= + ?offset=.

    Why this exists: a 100k-photo library with GPS data would otherwise
    serialize multi-megabyte JSON in a single response, stalling the
    browser on parse and pinning the DB cursor open during build. The
    map.mjs frontend now loops with has_more; the contract must hold
    so that the loop terminates correctly even on giant libraries."""

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

    def _add_n_gps_photos(self, app, n: int) -> list[str]:
        """Insert n photos with distinct GPS coords. Returns paths
        for cleanup."""
        paths: list[str] = []
        with app.app_context():
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            for i in range(n):
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
                    Image.new("RGB", (10, 10)).save(f, "JPEG")
                    path = f.name
                paths.append(path)
                # Distinct coords so the response carries unique rows
                upsert_photo(
                    conn,
                    {
                        "filepath": path,
                        "exif_json": json.dumps({"gps_lat": 40.0 + i * 0.01, "gps_lon": -74.0}),
                    },
                )
        return paths

    def test_response_has_pagination_envelope(self, client, app):
        """Even on a small library the response must carry total/limit/
        offset/has_more so the JS pagination loop has predictable
        termination conditions."""
        paths = self._add_n_gps_photos(app, 3)
        try:
            resp = client.get("/api/v1/photos/map")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["count"] == 3
            assert data["total"] == 3
            assert data["offset"] == 0
            assert data["limit"] == 5000
            assert data["has_more"] is False
        finally:
            for p in paths:
                if os.path.exists(p):
                    os.unlink(p)

    def test_limit_caps_returned_rows(self, client, app):
        """?limit=2 against 5 rows returns 2 in `photos` but `total`=5
        and has_more=True so the client knows to keep paging."""
        paths = self._add_n_gps_photos(app, 5)
        try:
            resp = client.get("/api/v1/photos/map?limit=2")
            data = resp.get_json()
            assert len(data["photos"]) == 2
            assert data["total"] == 5
            assert data["limit"] == 2
            assert data["offset"] == 0
            assert data["has_more"] is True
        finally:
            for p in paths:
                if os.path.exists(p):
                    os.unlink(p)

    def test_offset_pages_through_results(self, client, app):
        """?limit=2&offset=2 returns rows 2..3; ?offset=4 returns the
        last row and has_more=False."""
        paths = self._add_n_gps_photos(app, 5)
        try:
            r1 = client.get("/api/v1/photos/map?limit=2&offset=0").get_json()
            r2 = client.get("/api/v1/photos/map?limit=2&offset=2").get_json()
            r3 = client.get("/api/v1/photos/map?limit=2&offset=4").get_json()
            ids_p1 = {p["id"] for p in r1["photos"]}
            ids_p2 = {p["id"] for p in r2["photos"]}
            ids_p3 = {p["id"] for p in r3["photos"]}
            # No overlap between pages
            assert ids_p1.isdisjoint(ids_p2)
            assert ids_p2.isdisjoint(ids_p3)
            # Total = sum of pages
            assert len(r1["photos"]) + len(r2["photos"]) + len(r3["photos"]) == 5
            # Last page reports done
            assert r3["has_more"] is False
        finally:
            for p in paths:
                if os.path.exists(p):
                    os.unlink(p)

    def test_offset_past_total_returns_empty(self, client, app):
        """A client that asks past the end gets photos=[] + has_more=False
        — the map.mjs `while` loop relies on this to terminate cleanly."""
        paths = self._add_n_gps_photos(app, 3)
        try:
            data = client.get("/api/v1/photos/map?offset=999").get_json()
            assert data["photos"] == []
            assert data["count"] == 0
            assert data["total"] == 3
            assert data["has_more"] is False
        finally:
            for p in paths:
                if os.path.exists(p):
                    os.unlink(p)

    def test_limit_clamped_to_max(self, client, app):
        """?limit=100000 clamps to 50000 instead of erroring — REST
        listing endpoints should be forgiving of out-of-bound values."""
        paths = self._add_n_gps_photos(app, 1)
        try:
            data = client.get("/api/v1/photos/map?limit=100000").get_json()
            assert data["limit"] == 50000
        finally:
            for p in paths:
                if os.path.exists(p):
                    os.unlink(p)

    def test_garbage_limit_falls_back_to_default(self, client, app):
        """?limit=abc falls back to the 5000 default rather than 400."""
        paths = self._add_n_gps_photos(app, 1)
        try:
            data = client.get("/api/v1/photos/map?limit=abc&offset=xyz").get_json()
            assert data["limit"] == 5000
            assert data["offset"] == 0
        finally:
            for p in paths:
                if os.path.exists(p):
                    os.unlink(p)
