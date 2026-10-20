"""Tests for the bpp.db package — part 3 of 3: TestSmartAlbums, TestSoftDelete."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

from bpp.db.albums import (
    add_photos_to_album,
    create_album,
    ensure_all_photos_album,
    get_album_photos,
    list_albums,
    sync_all_photos_album,
)
from bpp.db.photos import (
    get_all_photos,
    get_deleted_photos,
    get_photo,
    get_photo_count,
    permanent_delete_photos,
    purge_old_deleted,
    restore_photos,
    soft_delete_photos,
    upsert_photo,
)
from tests._db_shared import conn, db_path, sample_image, sample_photos  # noqa: F401

# --- Smart albums tests ---


class TestSmartAlbums:
    def test_refresh_time_albums(self, conn, sample_photos):
        """Time-based smart albums are created for each year."""
        for i, fp in enumerate(sample_photos):
            upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                    "date": f"202{i}-06-15T12:00:00",
                },
            )

        from bpp.db.smart_albums import _refresh_time_albums

        _refresh_time_albums(conn)
        albums = list_albums(conn)
        time_albums = [a for a in albums if a["album_type"] == "smart_time"]
        year_names = {a["name"] for a in time_albums if a["name"] != "Last 30 Days"}
        assert "2020" in year_names
        assert "2024" in year_names

    def test_refresh_score_album(self, conn, sample_photos):
        """Top Rated smart album is created."""
        for i, fp in enumerate(sample_photos):
            upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                    "aggregate_score": 0.5 + i * 0.1,
                },
            )

        from bpp.db.smart_albums import _refresh_score_album

        _refresh_score_album(conn)
        albums = list_albums(conn)
        score_albums = [a for a in albums if a["album_type"] == "smart_score"]
        assert len(score_albums) == 1
        assert score_albums[0]["name"] == "Top Rated"

    def test_refresh_person_albums(self, conn, sample_photos, tmp_path):
        """Person smart albums are created for face clusters."""
        # Insert photos into DB
        photo_ids = []
        for i, fp in enumerate(sample_photos):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            photo_ids.append(pid)

        # Two clusters: cluster 0 with 3 photos, cluster 1 with 2 photos
        for i in range(3):
            conn.execute(
                "INSERT INTO face_embeddings "
                "(photo_id, face_index, embedding, cluster_id) "
                "VALUES (?, 0, X'00', 0)",
                (photo_ids[i],),
            )
        for i in range(3, 5):
            conn.execute(
                "INSERT INTO face_embeddings "
                "(photo_id, face_index, embedding, cluster_id) "
                "VALUES (?, 0, X'00', 1)",
                (photo_ids[i],),
            )
        conn.commit()

        from bpp.db.smart_albums import _refresh_person_albums

        _refresh_person_albums(conn)
        albums = list_albums(conn)
        person_albums = [a for a in albums if a["album_type"] == "smart_person"]
        assert len(person_albums) == 2
        names = {a["name"] for a in person_albums}
        assert "Person 1" in names
        assert "Person 2" in names

    def test_dismissed_cluster_excluded(self, conn, sample_photos, tmp_path):
        """Clusters with cluster_id=-2 (dismissed) are excluded."""
        photo_ids = []
        for i, fp in enumerate(sample_photos[:3]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            photo_ids.append(pid)

        # Cluster 0: active, cluster -2: dismissed
        conn.execute(
            "INSERT INTO face_embeddings "
            "(photo_id, face_index, embedding, cluster_id) "
            "VALUES (?, 0, X'00', 0)",
            (photo_ids[0],),
        )
        conn.execute(
            "INSERT INTO face_embeddings "
            "(photo_id, face_index, embedding, cluster_id) "
            "VALUES (?, 0, X'00', -2)",
            (photo_ids[1],),
        )
        conn.commit()

        from bpp.db.smart_albums import _refresh_person_albums

        _refresh_person_albums(conn)
        albums = list_albums(conn)
        person_albums = [a for a in albums if a["album_type"] == "smart_person"]
        # Only cluster 0 should get an album, dismissed cluster excluded
        assert len(person_albums) == 1
        assert person_albums[0]["name"] == "Person 1"


class TestSoftDelete:
    def test_soft_delete_hides_from_get_all(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)

        assert get_photo_count(conn) == 5
        soft_delete_photos(conn, [ids[0], ids[1]])
        assert get_photo_count(conn) == 3
        assert len(get_all_photos(conn)) == 3

    def test_soft_delete_returns_count(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:2]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        assert soft_delete_photos(conn, ids) == 2
        # Deleting again returns 0 (already deleted)
        assert soft_delete_photos(conn, ids) == 0

    def test_get_deleted_photos(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:3]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        soft_delete_photos(conn, [ids[0]])
        deleted = get_deleted_photos(conn)
        assert len(deleted) == 1
        assert deleted[0]["filepath"] == sample_photos[0]
        assert deleted[0]["deleted_at"] is not None

    def test_restore_photos(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:2]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        soft_delete_photos(conn, ids)
        assert get_photo_count(conn) == 0
        count = restore_photos(conn, [ids[0]])
        assert count == 1
        assert get_photo_count(conn) == 1
        assert len(get_deleted_photos(conn)) == 1

    def test_permanent_delete(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:2]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        soft_delete_photos(conn, ids)
        paths = permanent_delete_photos(conn, [ids[0]])
        assert len(paths) == 1
        assert paths[0] == sample_photos[0]
        # Gone from both active and deleted
        assert get_photo_count(conn) == 0
        assert len(get_deleted_photos(conn)) == 1
        assert get_photo(conn, ids[0]) is None

    def test_permanent_delete_removes_album_links(self, conn, sample_photos):
        pid = upsert_photo(
            conn,
            {
                "filepath": sample_photos[0],
                "original_filename": "img.jpg",
                "file_size": 100,
                "file_mtime": 1000.0,
            },
        )
        album_id = create_album(conn, "Test Album")
        add_photos_to_album(conn, album_id, [pid])
        assert len(get_album_photos(conn, album_id)) == 1
        soft_delete_photos(conn, [pid])
        permanent_delete_photos(conn, [pid])
        # Photo gone from album too
        rows = conn.execute("SELECT * FROM album_photos WHERE photo_id=?", (pid,)).fetchall()
        assert len(rows) == 0

    def test_deleted_excluded_from_album_photos(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:3]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        album_id = create_album(conn, "Test")
        add_photos_to_album(conn, album_id, ids)
        assert len(get_album_photos(conn, album_id)) == 3
        soft_delete_photos(conn, [ids[0]])
        assert len(get_album_photos(conn, album_id)) == 2

    def test_purge_old_deleted(self, conn, sample_photos):
        pid = upsert_photo(
            conn,
            {
                "filepath": sample_photos[0],
                "original_filename": "img.jpg",
                "file_size": 100,
                "file_mtime": 1000.0,
            },
        )
        # Soft delete then backdate to 31 days ago
        soft_delete_photos(conn, [pid])
        conn.execute(
            "UPDATE photos SET deleted_at=datetime('now', '-31 days') WHERE id=?",
            (pid,),
        )
        conn.commit()
        paths = purge_old_deleted(conn, days=30)
        assert len(paths) == 1
        assert get_photo(conn, pid) is None

    def test_purge_keeps_recent_deletes(self, conn, sample_photos):
        pid = upsert_photo(
            conn,
            {
                "filepath": sample_photos[0],
                "original_filename": "img.jpg",
                "file_size": 100,
                "file_mtime": 1000.0,
            },
        )
        soft_delete_photos(conn, [pid])
        paths = purge_old_deleted(conn, days=30)
        assert len(paths) == 0
        assert len(get_deleted_photos(conn)) == 1

    def test_sync_all_photos_excludes_deleted(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:3]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        soft_delete_photos(conn, [ids[0]])
        sync_all_photos_album(conn)
        all_id = ensure_all_photos_album(conn)
        photos = get_album_photos(conn, all_id)
        filepaths = {p["filepath"] for p in photos}
        assert sample_photos[0] not in filepaths
        assert sample_photos[1] in filepaths

    def test_empty_list_operations(self, conn):
        assert soft_delete_photos(conn, []) == 0
        assert restore_photos(conn, []) == 0
        assert permanent_delete_photos(conn, []) == []

    def test_get_all_include_deleted(self, conn, sample_photos):
        ids = []
        for i, fp in enumerate(sample_photos[:3]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        soft_delete_photos(conn, [ids[0]])
        assert len(get_all_photos(conn)) == 2
        all_inc = get_all_photos(conn, include_deleted=True)
        assert len(all_inc) == 3
        deleted_paths = [p["filepath"] for p in all_inc if p["deleted_at"]]
        assert sample_photos[0] in deleted_paths

    def test_smart_deleted_album_returns_deleted_ids(self, conn, sample_photos):
        from bpp.db.smart_albums import get_smart_album_photo_ids

        album = {"album_type": "smart_deleted", "rule": {}}
        ids = []
        for i, fp in enumerate(sample_photos[:3]):
            pid = upsert_photo(
                conn,
                {
                    "filepath": fp,
                    "original_filename": f"img_{i}.jpg",
                    "file_size": 100,
                    "file_mtime": 1000.0 + i,
                },
            )
            ids.append(pid)
        # No deleted photos yet
        result = get_smart_album_photo_ids(conn, album)
        assert result == []
        # Delete one
        soft_delete_photos(conn, [ids[0]])
        result = get_smart_album_photo_ids(conn, album)
        assert result == [ids[0]]
        # Delete another
        soft_delete_photos(conn, [ids[2]])
        result = get_smart_album_photo_ids(conn, album)
        assert set(result) == {ids[0], ids[2]}
