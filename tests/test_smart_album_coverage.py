"""Comprehensive tests for smart_albums.py (groups), face worker utils, and thumbnails.py."""

from __future__ import annotations

import os

import numpy as np
import pytest
from PIL import Image

from bpp.constants import CLUSTER_UNASSIGNED
from bpp.db.albums import (
    create_album,
    get_album,
    list_albums,
    update_album,
)
from bpp.db.connection import get_db, init_db
from bpp.db.photos import upsert_photo
from bpp.db.smart_albums import (
    get_smart_album_photo_ids,
    refresh_smart_albums,
)
from bpp.web.thumbnails import ThumbnailCache

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary database and return its path."""
    path = str(tmp_path / "smarttest.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    """Return a connection to the test database."""
    return get_db(db_path)


def _make_photo(conn, tmp_path, name, **extra):
    """Create a dummy file on disk and upsert it into the photos table."""
    f = tmp_path / name
    f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    photo = {"filepath": str(f)}
    photo.update(extra)
    return upsert_photo(conn, photo)


def _add_face(conn, photo_id, face_index, cluster_id):
    """Insert a face embedding row for a given photo and cluster."""
    embedding = np.zeros(128, dtype=np.float32).tobytes()
    conn.execute(
        "INSERT OR REPLACE INTO face_embeddings "
        "(photo_id, face_index, embedding, cluster_id) VALUES (?, ?, ?, ?)",
        (photo_id, face_index, embedding, cluster_id),
    )
    conn.commit()


def _add_pet(conn, photo_id, pet_class, cluster_id, detection_index=0):
    """Insert a pet detection row for a given photo and cluster."""
    conn.execute(
        "INSERT INTO pet_detections "
        "(photo_id, detection_index, class, confidence, cluster_id) "
        "VALUES (?, ?, ?, ?, ?)",
        (photo_id, detection_index, pet_class, 0.9, cluster_id),
    )
    conn.commit()


def _make_jpeg(path):
    """Write a tiny valid JPEG via PIL so thumbnail generation can succeed."""
    img = Image.new("RGB", (100, 100), "red")
    img.save(str(path), "JPEG")


# ---------------------------------------------------------------------------
# Smart-album: group album tests
# ---------------------------------------------------------------------------


class TestGroupAlbums:
    """Tests for _refresh_group_albums via refresh_smart_albums."""

    def test_refresh_creates_group_albums(self, conn, tmp_path):
        """Two people appearing together in 3+ photos should produce a smart_group album."""
        # Create 4 photos, each with faces from cluster 0 and cluster 1
        for i in range(4):
            pid = _make_photo(conn, tmp_path, f"pair_{i}.jpg")
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)

        refresh_smart_albums(conn)

        albums = list_albums(conn)
        group_albums = [a for a in albums if a["album_type"] == "smart_group"]
        assert len(group_albums) >= 1, "Expected at least one group album"

    def test_group_album_name_from_person_names(self, conn, tmp_path):
        """Group album name should be derived from person album names."""
        for i in range(4):
            pid = _make_photo(conn, tmp_path, f"named_{i}.jpg")
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)

        # First refresh to create person albums
        refresh_smart_albums(conn)

        # Rename person albums
        albums = list_albums(conn)
        for a in albums:
            if a["album_type"] == "smart_person":
                rule = a.get("rule") or {}
                cid = rule.get("cluster_id")
                if cid == 0:
                    update_album(conn, a["id"], name="Alice")
                elif cid == 1:
                    update_album(conn, a["id"], name="Bob")

        # Refresh again to pick up renamed person albums
        # First delete existing group albums so they get recreated with new names
        for a in list_albums(conn):
            if a["album_type"] == "smart_group":
                conn.execute("DELETE FROM album_photos WHERE album_id=?", (a["id"],))
                conn.execute("DELETE FROM albums WHERE id=?", (a["id"],))
        conn.commit()

        refresh_smart_albums(conn)

        albums = list_albums(conn)
        group_albums = [a for a in albums if a["album_type"] == "smart_group"]
        assert len(group_albums) >= 1
        group_name = group_albums[0]["name"]
        assert "Alice" in group_name and "Bob" in group_name, (
            f"Expected group name to contain person names, got '{group_name}'"
        )

    def test_group_album_cleanup_on_dismiss(self, conn, tmp_path):
        """Dismissing a face cluster (setting cluster_id=-1) should remove the group album."""
        for i in range(4):
            pid = _make_photo(conn, tmp_path, f"dismiss_{i}.jpg")
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)

        refresh_smart_albums(conn)
        group_albums = [a for a in list_albums(conn) if a["album_type"] == "smart_group"]
        assert len(group_albums) >= 1, "Prerequisite: group album must exist"

        # Dismiss cluster 1 by setting all its face rows to unassigned
        conn.execute(
            f"UPDATE face_embeddings SET cluster_id={CLUSTER_UNASSIGNED} WHERE cluster_id=1"
        )
        conn.commit()

        refresh_smart_albums(conn)

        group_albums = [a for a in list_albums(conn) if a["album_type"] == "smart_group"]
        assert len(group_albums) == 0, "Group album should be removed after cluster dismissal"

    def test_no_group_album_below_threshold(self, conn, tmp_path):
        """Two people in only 2 photos should NOT produce a group album (min_photos=3)."""
        for i in range(2):
            pid = _make_photo(conn, tmp_path, f"few_{i}.jpg")
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)

        refresh_smart_albums(conn)

        group_albums = [a for a in list_albums(conn) if a["album_type"] == "smart_group"]
        assert len(group_albums) == 0, "No group album expected below threshold"

    def test_refresh_preserves_user_renamed_group(self, conn, tmp_path):
        """If user renames a group album, refresh should NOT overwrite the name."""
        for i in range(4):
            pid = _make_photo(conn, tmp_path, f"keep_{i}.jpg")
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)

        refresh_smart_albums(conn)

        albums = list_albums(conn)
        group_albums = [a for a in albums if a["album_type"] == "smart_group"]
        assert len(group_albums) >= 1
        gid = group_albums[0]["id"]

        # User renames the group
        update_album(conn, gid, name="My Family")

        # Refresh again
        refresh_smart_albums(conn)

        updated = get_album(conn, gid)
        assert updated is not None
        assert updated["name"] == "My Family", (
            f"User rename should be preserved, got '{updated['name']}'"
        )

    def test_smart_album_photo_ids_for_group(self, conn, tmp_path):
        """get_smart_album_photo_ids should return the intersection of group member photos."""
        # Photos shared by cluster 0 and 1
        shared_pids = []
        for i in range(4):
            pid = _make_photo(conn, tmp_path, f"shared_{i}.jpg")
            _add_face(conn, pid, 0, 0)
            _add_face(conn, pid, 1, 1)
            shared_pids.append(pid)

        # A photo only with cluster 0
        solo_pid = _make_photo(conn, tmp_path, "solo_0.jpg")
        _add_face(conn, solo_pid, 0, 0)

        refresh_smart_albums(conn)

        albums = list_albums(conn)
        group_albums = [a for a in albums if a["album_type"] == "smart_group"]
        assert len(group_albums) >= 1

        result_ids = get_smart_album_photo_ids(conn, group_albums[0])
        assert set(result_ids) == set(shared_pids), (
            "Group should contain only photos where ALL members appear"
        )
        assert solo_pid not in result_ids


# ---------------------------------------------------------------------------
# Smart-album: pet, unsorted, and time album tests
# ---------------------------------------------------------------------------


class TestPetAlbums:
    """Tests for _refresh_pet_albums via refresh_smart_albums."""

    def test_refresh_creates_pet_albums(self, conn, tmp_path):
        """Pet detections should produce smart_pet albums."""
        pid1 = _make_photo(conn, tmp_path, "cat1.jpg")
        pid2 = _make_photo(conn, tmp_path, "cat2.jpg")
        pid3 = _make_photo(conn, tmp_path, "dog1.jpg")
        _add_pet(conn, pid1, "cat", 0)
        _add_pet(conn, pid2, "cat", 0)
        _add_pet(conn, pid3, "dog", 1)

        refresh_smart_albums(conn)

        albums = list_albums(conn)
        pet_albums = [a for a in albums if a["album_type"] == "smart_pet"]
        pet_names = {a["name"] for a in pet_albums}
        assert "Cats" in pet_names, "Expected a 'Cats' smart_pet album"
        assert "Dogs" in pet_names, "Expected a 'Dogs' smart_pet album"

    def test_pet_album_removed_when_no_photos(self, conn, tmp_path):
        """Pet album should be cleaned up when no matching detections remain."""
        pid = _make_photo(conn, tmp_path, "only_cat.jpg")
        _add_pet(conn, pid, "cat", 0)

        refresh_smart_albums(conn)
        pet_albums = [a for a in list_albums(conn) if a["album_type"] == "smart_pet"]
        assert any(a["name"] == "Cats" for a in pet_albums)

        # Remove the pet detection
        conn.execute("DELETE FROM pet_detections WHERE photo_id=?", (pid,))
        conn.commit()

        refresh_smart_albums(conn)
        pet_albums = [a for a in list_albums(conn) if a["album_type"] == "smart_pet"]
        cat_albums = [a for a in pet_albums if a["name"] == "Cats"]
        assert len(cat_albums) == 0, "Cats album should be removed when no cat detections exist"


class TestUnsortedAlbum:
    """Tests for _refresh_unsorted_album via refresh_smart_albums."""

    def test_refresh_creates_unsorted_album(self, conn, tmp_path):
        """Photos not in any manual album should appear in the Unsorted smart album."""
        pid1 = _make_photo(conn, tmp_path, "unsorted1.jpg")
        pid2 = _make_photo(conn, tmp_path, "unsorted2.jpg")

        # Put pid1 in a manual album
        manual_id = create_album(conn, "My Album", album_type="manual")
        from bpp.db.albums import add_photos_to_album

        add_photos_to_album(conn, manual_id, [pid1])

        refresh_smart_albums(conn)

        albums = list_albums(conn)
        unsorted = [a for a in albums if a["album_type"] == "smart_unsorted"]
        assert len(unsorted) >= 1, "Expected an Unsorted smart album"

        unsorted_ids = get_smart_album_photo_ids(conn, unsorted[0])
        assert pid2 in unsorted_ids, "Photo not in manual album should be unsorted"
        assert pid1 not in unsorted_ids, "Photo in manual album should NOT be unsorted"


class TestTimeAlbums:
    """Tests for _refresh_time_albums via refresh_smart_albums."""

    def test_refresh_creates_time_albums(self, conn, tmp_path):
        """Photos with dates should produce year-based smart_time albums."""
        _make_photo(conn, tmp_path, "y2023.jpg", date="2023-06-15 12:00:00")
        _make_photo(conn, tmp_path, "y2024.jpg", date="2024-01-10 08:00:00")

        refresh_smart_albums(conn)

        albums = list_albums(conn)
        time_albums = [a for a in albums if a["album_type"] == "smart_time"]
        time_names = {a["name"] for a in time_albums}
        assert "2023" in time_names, "Expected year 2023 album"
        assert "2024" in time_names, "Expected year 2024 album"
        # "Last 30 Days" is always created
        assert "Last 30 Days" in time_names

    def test_time_album_photo_ids(self, conn, tmp_path):
        """get_smart_album_photo_ids for a year album returns correct photos."""
        pid_2023 = _make_photo(conn, tmp_path, "t2023.jpg", date="2023-08-01 10:00:00")
        _make_photo(conn, tmp_path, "t2024.jpg", date="2024-03-01 10:00:00")

        refresh_smart_albums(conn)

        albums = list_albums(conn)
        album_2023 = next(
            (a for a in albums if a["album_type"] == "smart_time" and a["name"] == "2023"),
            None,
        )
        assert album_2023 is not None
        ids = get_smart_album_photo_ids(conn, album_2023)
        assert pid_2023 in ids


# ---------------------------------------------------------------------------
# Smart-album: recently edited tests
# ---------------------------------------------------------------------------


class TestRecentlyEditedAlbum:
    """Tests for _refresh_recently_edited_album via refresh_smart_albums."""

    def test_no_album_when_no_edits(self, conn, tmp_path):
        """No 'Recently Edited' album should exist when no photos have edits."""
        _make_photo(conn, tmp_path, "unedited.jpg")
        refresh_smart_albums(conn)

        albums = list_albums(conn)
        edited = [a for a in albums if a["album_type"] == "smart_edited"]
        assert len(edited) == 0, "No edited album expected when no edits exist"

    def test_album_created_when_edits_exist(self, conn, tmp_path):
        """Photos with edits should produce a 'Recently Edited' smart album."""
        from bpp.db.edits import save_photo_edits

        pid = _make_photo(conn, tmp_path, "edited1.jpg")
        save_photo_edits(conn, pid, {"brightness": 1.2})

        refresh_smart_albums(conn)

        albums = list_albums(conn)
        edited = [a for a in albums if a["album_type"] == "smart_edited"]
        assert len(edited) == 1
        assert edited[0]["name"] == "Recently Edited"

    def test_album_contains_edited_photos_only(self, conn, tmp_path):
        """Only photos with edits should appear in the Recently Edited album."""
        from bpp.db.edits import save_photo_edits

        pid1 = _make_photo(conn, tmp_path, "has_edit.jpg")
        pid2 = _make_photo(conn, tmp_path, "no_edit.jpg")
        save_photo_edits(conn, pid1, {"contrast": 1.3})

        refresh_smart_albums(conn)

        albums = list_albums(conn)
        edited = [a for a in albums if a["album_type"] == "smart_edited"]
        assert len(edited) == 1

        ids = get_smart_album_photo_ids(conn, edited[0])
        assert pid1 in ids
        assert pid2 not in ids

    def test_album_removed_when_edits_cleared(self, conn, tmp_path):
        """Clearing all edits should remove the Recently Edited album."""
        from bpp.db.edits import reset_photo_edits, save_photo_edits

        pid = _make_photo(conn, tmp_path, "will_reset.jpg")
        save_photo_edits(conn, pid, {"brightness": 1.5})

        refresh_smart_albums(conn)
        edited = [a for a in list_albums(conn) if a["album_type"] == "smart_edited"]
        assert len(edited) == 1

        reset_photo_edits(conn, pid)
        refresh_smart_albums(conn)

        edited = [a for a in list_albums(conn) if a["album_type"] == "smart_edited"]
        assert len(edited) == 0, "Edited album should be removed when no edits remain"

    def test_dynamic_reevaluation(self, conn, tmp_path):
        """get_smart_album_photo_ids should dynamically return current edited photos."""
        from bpp.db.edits import save_photo_edits

        pid1 = _make_photo(conn, tmp_path, "dyn1.jpg")
        pid2 = _make_photo(conn, tmp_path, "dyn2.jpg")
        save_photo_edits(conn, pid1, {"saturation": 1.4})

        refresh_smart_albums(conn)
        albums = list_albums(conn)
        edited = [a for a in albums if a["album_type"] == "smart_edited"]
        assert len(edited) == 1

        # Before adding pid2's edits
        ids = get_smart_album_photo_ids(conn, edited[0])
        assert pid1 in ids
        assert pid2 not in ids

        # Add edits for pid2
        save_photo_edits(conn, pid2, {"warmth": 0.3})
        ids = get_smart_album_photo_ids(conn, edited[0])
        assert pid1 in ids
        assert pid2 in ids


# ---------------------------------------------------------------------------
# ThumbnailCache tests
# ---------------------------------------------------------------------------


class TestThumbnailCache:
    """Tests for the ThumbnailCache class."""

    def test_register_and_get_hash(self, tmp_path):
        """Registering a file via build_map then get_hash should return a consistent hash."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img_path = tmp_path / "test.jpg"
        _make_jpeg(img_path)

        cache.build_map([{"filepath": str(img_path)}])
        h = cache.get_hash(str(img_path))
        assert h is not None
        assert len(h) == 32  # sha256[:32]

    def test_get_filepath_returns_original(self, tmp_path):
        """After build_map, get_filepath should return the original path."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img_path = tmp_path / "original.jpg"
        _make_jpeg(img_path)
        fp = str(img_path)

        cache.build_map([{"filepath": fp}])
        h = cache.get_hash(fp)
        assert cache.get_filepath(h) == fp

    def test_unknown_hash_returns_none(self, tmp_path):
        """get_thumbnail and get_filepath for an unknown hash should return None."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        assert cache.get_thumbnail("nonexistent1234") is None
        assert cache.get_filepath("nonexistent1234") is None

    def test_clear_removes_thumbnails(self, tmp_path):
        """After clear(), cached thumbnail files should be gone."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img_path = tmp_path / "clearme.jpg"
        _make_jpeg(img_path)

        cache.build_map([{"filepath": str(img_path)}])
        h = cache.get_hash(str(img_path))

        # Generate the thumbnail
        thumb_result = cache.get_thumbnail(h)
        assert thumb_result is not None
        assert os.path.exists(thumb_result)

        # Clear
        removed = cache.clear()
        assert removed >= 1
        assert not os.path.exists(thumb_result)

    def test_register_same_file_twice(self, tmp_path):
        """Registering the same file twice should produce the same hash."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img_path = tmp_path / "dupe.jpg"
        _make_jpeg(img_path)
        fp = str(img_path)

        cache.build_map([{"filepath": fp}])
        h1 = cache.get_hash(fp)

        cache.build_map([{"filepath": fp}])
        h2 = cache.get_hash(fp)

        assert h1 == h2

    def test_get_thumbnail_generates_file(self, tmp_path):
        """get_thumbnail should lazily generate a thumbnail JPEG on disk."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img_path = tmp_path / "gen.jpg"
        _make_jpeg(img_path)

        cache.build_map([{"filepath": str(img_path)}])
        h = cache.get_hash(str(img_path))

        thumb = cache.get_thumbnail(h)
        assert thumb is not None
        assert os.path.isfile(thumb)
        assert thumb.endswith(".jpg")

    def test_get_thumbnail_returns_cached(self, tmp_path):
        """Second call to get_thumbnail should return the cached version."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img_path = tmp_path / "cached.jpg"
        _make_jpeg(img_path)

        cache.build_map([{"filepath": str(img_path)}])
        h = cache.get_hash(str(img_path))

        thumb1 = cache.get_thumbnail(h)
        thumb2 = cache.get_thumbnail(h)
        assert thumb1 == thumb2
        assert os.path.isfile(thumb2)

    def test_multiple_files_distinct_hashes(self, tmp_path):
        """Different files should have different hashes."""
        cache_dir = str(tmp_path / "thumbs")
        cache = ThumbnailCache(cache_dir)

        img1 = tmp_path / "a.jpg"
        img2 = tmp_path / "b.jpg"
        _make_jpeg(img1)
        _make_jpeg(img2)

        cache.build_map(
            [
                {"filepath": str(img1)},
                {"filepath": str(img2)},
            ]
        )
        h1 = cache.get_hash(str(img1))
        h2 = cache.get_hash(str(img2))
        assert h1 != h2

    def test_cache_version_clears_on_mismatch(self, tmp_path):
        """If cache version file is stale, __init__ should clear old thumbnails."""
        cache_dir = str(tmp_path / "thumbs")
        os.makedirs(cache_dir, exist_ok=True)

        # Write a stale version
        with open(os.path.join(cache_dir, ".version"), "w") as f:
            f.write("0")

        # Place a fake cached file
        fake_thumb = os.path.join(cache_dir, "old_thumb.jpg")
        with open(fake_thumb, "w") as f:
            f.write("stale")

        assert os.path.exists(fake_thumb)

        # Creating cache should clear stale files
        ThumbnailCache(cache_dir)
        assert not os.path.exists(fake_thumb), (
            "Stale thumbnails should be cleared on version mismatch"
        )
