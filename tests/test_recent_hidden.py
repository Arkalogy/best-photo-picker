"""TDD tests for Recently Added smart album and Hidden album features."""

from __future__ import annotations

import json
import os

import pytest

from bpp.db.albums import (
    list_albums,
)
from bpp.db.connection import get_db, init_db
from bpp.db.photos import (
    get_all_photos,
    get_photo_count,
    upsert_photo,
)
from bpp.db.smart_albums import (
    get_smart_album_photo_ids,
    refresh_smart_albums,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test_rh.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    return get_db(db_path)


def _make_photo(conn, tmp_path, name, **extra):
    f = tmp_path / name
    f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    photo = {"filepath": str(f)}
    photo.update(extra)
    return upsert_photo(conn, photo)


# ---------------------------------------------------------------------------
# Recently Added smart album tests
# ---------------------------------------------------------------------------


class TestRecentlyAdded:
    """Tests for the 'Recently Added' smart album (smart_recent type)."""

    def test_refresh_creates_recently_added_album(self, conn, tmp_path):
        """Refresh should create a 'Recently Added' smart_recent album."""
        _make_photo(conn, tmp_path, "new1.jpg")
        refresh_smart_albums(conn)

        albums = list_albums(conn)
        recent = [a for a in albums if a["album_type"] == "smart_recent"]
        assert len(recent) == 1
        assert recent[0]["name"] == "Recently Added"

    def test_recently_added_contains_recent_photos(self, conn, tmp_path):
        """Photos imported recently should appear in Recently Added."""
        pid = _make_photo(conn, tmp_path, "recent.jpg")
        refresh_smart_albums(conn)

        albums = list_albums(conn)
        recent = next(a for a in albums if a["album_type"] == "smart_recent")
        photo_ids = get_smart_album_photo_ids(conn, recent)
        assert pid in photo_ids

    def test_recently_added_excludes_old_photos(self, conn, tmp_path):
        """Photos imported >7 days ago should NOT appear in Recently Added."""
        pid = _make_photo(conn, tmp_path, "old.jpg")
        # Backdate the created_at to 10 days ago
        conn.execute(
            "UPDATE photos SET created_at=datetime('now', '-10 days') WHERE id=?",
            (pid,),
        )
        conn.commit()

        refresh_smart_albums(conn)

        albums = list_albums(conn)
        recent = next(a for a in albums if a["album_type"] == "smart_recent")
        photo_ids = get_smart_album_photo_ids(conn, recent)
        assert pid not in photo_ids

    def test_recently_added_excludes_deleted(self, conn, tmp_path):
        """Deleted photos should NOT appear in Recently Added."""
        pid = _make_photo(conn, tmp_path, "deleted.jpg")
        conn.execute("UPDATE photos SET deleted_at=datetime('now') WHERE id=?", (pid,))
        conn.commit()

        refresh_smart_albums(conn)

        albums = list_albums(conn)
        recent = next(a for a in albums if a["album_type"] == "smart_recent")
        photo_ids = get_smart_album_photo_ids(conn, recent)
        assert pid not in photo_ids

    def test_recently_added_excludes_hidden(self, conn, tmp_path):
        """Hidden photos should NOT appear in Recently Added."""
        pid = _make_photo(conn, tmp_path, "hidden.jpg")
        conn.execute("UPDATE photos SET hidden_at=datetime('now') WHERE id=?", (pid,))
        conn.commit()

        refresh_smart_albums(conn)

        albums = list_albums(conn)
        recent = next(a for a in albums if a["album_type"] == "smart_recent")
        photo_ids = get_smart_album_photo_ids(conn, recent)
        assert pid not in photo_ids


# ---------------------------------------------------------------------------
# Hidden album tests — DB layer
# ---------------------------------------------------------------------------


class TestHiddenDB:
    """Tests for the hidden photo DB operations."""

    def test_hide_photo(self, conn, tmp_path):
        """Hiding a photo sets hidden_at timestamp."""
        from bpp.db.photos import hide_photos

        pid = _make_photo(conn, tmp_path, "tohide.jpg")
        count = hide_photos(conn, [pid])
        assert count == 1

        row = conn.execute("SELECT hidden_at FROM photos WHERE id=?", (pid,)).fetchone()
        assert row[0] is not None

    def test_unhide_photo(self, conn, tmp_path):
        """Unhiding a photo clears hidden_at."""
        from bpp.db.photos import hide_photos, unhide_photos

        pid = _make_photo(conn, tmp_path, "tohide2.jpg")
        hide_photos(conn, [pid])
        count = unhide_photos(conn, [pid])
        assert count == 1

        row = conn.execute("SELECT hidden_at FROM photos WHERE id=?", (pid,)).fetchone()
        assert row[0] is None

    def test_get_hidden_photos(self, conn, tmp_path):
        """get_hidden_photos returns only hidden photos."""
        from bpp.db.photos import get_hidden_photos, hide_photos

        pid1 = _make_photo(conn, tmp_path, "vis.jpg")
        pid2 = _make_photo(conn, tmp_path, "hid.jpg")
        hide_photos(conn, [pid2])

        hidden = get_hidden_photos(conn)
        hidden_ids = {p["id"] for p in hidden}
        assert pid2 in hidden_ids
        assert pid1 not in hidden_ids

    def test_hidden_excluded_from_get_all_photos(self, conn, tmp_path):
        """get_all_photos should exclude hidden photos by default."""
        pid1 = _make_photo(conn, tmp_path, "normal.jpg")
        pid2 = _make_photo(conn, tmp_path, "hidden.jpg")
        from bpp.db.photos import hide_photos

        hide_photos(conn, [pid2])

        all_photos = get_all_photos(conn)
        all_ids = {p["id"] for p in all_photos}
        assert pid1 in all_ids
        assert pid2 not in all_ids

    def test_hidden_excluded_from_photo_count(self, conn, tmp_path):
        """get_photo_count should exclude hidden photos."""
        from bpp.db.photos import hide_photos

        _make_photo(conn, tmp_path, "c1.jpg")
        pid2 = _make_photo(conn, tmp_path, "c2.jpg")
        hide_photos(conn, [pid2])

        assert get_photo_count(conn) == 1

    def test_hide_already_hidden_noop(self, conn, tmp_path):
        """Hiding an already-hidden photo returns 0."""
        from bpp.db.photos import hide_photos

        pid = _make_photo(conn, tmp_path, "already.jpg")
        hide_photos(conn, [pid])
        count = hide_photos(conn, [pid])
        assert count == 0

    def test_unhide_not_hidden_noop(self, conn, tmp_path):
        """Unhiding a non-hidden photo returns 0."""
        from bpp.db.photos import unhide_photos

        pid = _make_photo(conn, tmp_path, "nothidden.jpg")
        count = unhide_photos(conn, [pid])
        assert count == 0


# ---------------------------------------------------------------------------
# Hidden smart album tests
# ---------------------------------------------------------------------------


class TestHiddenSmartAlbum:
    """Tests for the smart_hidden album type."""

    def test_refresh_creates_hidden_album_when_hidden_exist(self, conn, tmp_path):
        """Refresh should create a Hidden smart album when hidden photos exist."""
        from bpp.db.photos import hide_photos

        pid = _make_photo(conn, tmp_path, "h1.jpg")
        hide_photos(conn, [pid])
        refresh_smart_albums(conn)

        albums = list_albums(conn)
        hidden = [a for a in albums if a["album_type"] == "smart_hidden"]
        assert len(hidden) == 1
        assert hidden[0]["name"] == "Hidden"

    def test_hidden_album_contains_hidden_photos(self, conn, tmp_path):
        """Hidden smart album should contain hidden photos."""
        from bpp.db.photos import hide_photos

        pid1 = _make_photo(conn, tmp_path, "vis.jpg")
        pid2 = _make_photo(conn, tmp_path, "hid.jpg")
        hide_photos(conn, [pid2])
        refresh_smart_albums(conn)

        albums = list_albums(conn)
        hidden = next(a for a in albums if a["album_type"] == "smart_hidden")
        photo_ids = get_smart_album_photo_ids(conn, hidden)
        assert pid2 in photo_ids
        assert pid1 not in photo_ids

    def test_no_hidden_album_when_none_hidden(self, conn, tmp_path):
        """No Hidden album should exist when no photos are hidden."""
        _make_photo(conn, tmp_path, "normal.jpg")
        refresh_smart_albums(conn)

        albums = list_albums(conn)
        hidden = [a for a in albums if a["album_type"] == "smart_hidden"]
        assert len(hidden) == 0

    def test_hidden_album_removed_when_all_unhidden(self, conn, tmp_path):
        """Hidden album should be cleaned up when all photos are unhidden."""
        from bpp.db.photos import hide_photos, unhide_photos

        pid = _make_photo(conn, tmp_path, "temp_hide.jpg")
        hide_photos(conn, [pid])
        refresh_smart_albums(conn)

        # Verify it exists
        albums = list_albums(conn)
        assert any(a["album_type"] == "smart_hidden" for a in albums)

        # Unhide and refresh
        unhide_photos(conn, [pid])
        refresh_smart_albums(conn)

        albums = list_albums(conn)
        hidden = [a for a in albums if a["album_type"] == "smart_hidden"]
        assert len(hidden) == 0

    def test_hidden_excluded_from_all_photos_album(self, conn, tmp_path):
        """Hidden photos should not appear in the All Photos album."""
        from bpp.db.albums import ensure_all_photos_album, sync_all_photos_album
        from bpp.db.photos import hide_photos

        pid1 = _make_photo(conn, tmp_path, "vis2.jpg")
        pid2 = _make_photo(conn, tmp_path, "hid2.jpg")
        ensure_all_photos_album(conn)
        sync_all_photos_album(conn)
        hide_photos(conn, [pid2])
        refresh_smart_albums(conn)

        albums = list_albums(conn)
        all_album = next(a for a in albums if a["album_type"] == "all")
        photo_ids = get_smart_album_photo_ids(conn, all_album)
        assert pid1 in photo_ids
        assert pid2 not in photo_ids


# ---------------------------------------------------------------------------
# Hidden photos — API tests
# ---------------------------------------------------------------------------


class TestHiddenAPI:
    """Tests for the hide/unhide REST API endpoints."""

    @pytest.fixture
    def client(self, tmp_path):
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)

        # Create analysis.json with test photos
        analysis = []
        for i in range(3):
            fpath = str(tmp_path / f"img_{i}.jpg")
            (tmp_path / f"img_{i}.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
            analysis.append(
                {
                    "filepath": fpath,
                    "original_filename": f"img_{i}.jpg",
                    "date": f"2024-01-{i + 1:02d}T12:00:00",
                    "date_day": f"2024-01-{i + 1:02d}",
                    "date_month": "2024-01",
                    "file_size": 1024,
                    "file_mtime": 1700000000.0 + i,
                    "blur_raw": 100.0,
                    "blur_score": 0.5,
                    "exposure_score": 0.5,
                    "face_score": 0.3,
                    "face_count": 0,
                    "largest_face_ratio": 0.0,
                    "face_center_dist": 0.0,
                    "composition_score": 0.5,
                    "aggregate_score": 0.5,
                }
            )

        import json as json_mod

        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json_mod.dump(analysis, f)

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        client = app.test_client()
        # Init photos
        client.get("/api/v1/photos")
        return client

    def test_hide_endpoint(self, client, tmp_path):
        """POST /api/photos/hide should hide the specified photos."""
        filepaths = [str(tmp_path / "img_0.jpg")]
        resp = client.post(
            "/api/v1/photos/hide",
            data=json.dumps({"filepaths": filepaths}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1

    def test_unhide_endpoint(self, client, tmp_path):
        """POST /api/photos/unhide should unhide the specified photos."""
        filepaths = [str(tmp_path / "img_0.jpg")]
        # First hide
        client.post(
            "/api/v1/photos/hide",
            data=json.dumps({"filepaths": filepaths}),
            content_type="application/json",
        )
        # Then unhide
        resp = client.post(
            "/api/v1/photos/unhide",
            data=json.dumps({"filepaths": filepaths}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["count"] == 1

    def test_hidden_photos_endpoint(self, client, tmp_path):
        """GET /api/photos/hidden should return only hidden photos."""
        filepaths = [str(tmp_path / "img_0.jpg")]
        client.post(
            "/api/v1/photos/hide",
            data=json.dumps({"filepaths": filepaths}),
            content_type="application/json",
        )
        resp = client.get("/api/v1/photos/hidden")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["photos"]) == 1
        assert data["total"] == 1

    def test_hidden_excluded_from_photos_api(self, client, tmp_path):
        """Hidden photos should not appear in /api/photos."""
        filepaths = [str(tmp_path / "img_0.jpg")]
        client.post(
            "/api/v1/photos/hide",
            data=json.dumps({"filepaths": filepaths}),
            content_type="application/json",
        )
        resp = client.get("/api/v1/photos")
        data = resp.get_json()
        fps = [p["filepath"] for p in data["photos"]]
        assert str(tmp_path / "img_0.jpg") not in fps
