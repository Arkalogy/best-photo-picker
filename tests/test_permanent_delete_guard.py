"""TDD tests for H-6: permanent delete must require prior soft-delete."""

from __future__ import annotations

import sqlite3

import pytest

from bpp.db.connection import init_db
from bpp.db.photos import (
    get_photo,
    permanent_delete_photos,
    soft_delete_photos,
    upsert_photo,
)


@pytest.fixture()
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _insert(conn, fp="/tmp/photo.jpg"):
    return upsert_photo(
        conn,
        {
            "filepath": fp,
            "original_filename": "photo.jpg",
            "file_size": 100,
            "file_mtime": 1000.0,
        },
    )


class TestPermanentDeleteRequiresSoftDelete:
    def test_cannot_permanently_delete_live_photo(self, conn):
        """A photo that was NOT soft-deleted must not be permanently deleted."""
        pid = _insert(conn)
        paths = permanent_delete_photos(conn, [pid])
        # Should return empty — photo was not in recycle bin
        assert paths == []
        # Photo must still exist
        assert get_photo(conn, pid) is not None

    def test_can_permanently_delete_soft_deleted_photo(self, conn):
        """A photo that WAS soft-deleted can be permanently deleted."""
        pid = _insert(conn)
        soft_delete_photos(conn, [pid])
        paths = permanent_delete_photos(conn, [pid])
        assert len(paths) == 1
        assert get_photo(conn, pid) is None

    def test_mixed_ids_only_deletes_soft_deleted(self, conn):
        """Given a mix of live and soft-deleted IDs, only soft-deleted are removed."""
        pid_live = _insert(conn, "/tmp/live.jpg")
        pid_deleted = _insert(conn, "/tmp/deleted.jpg")
        soft_delete_photos(conn, [pid_deleted])

        paths = permanent_delete_photos(conn, [pid_live, pid_deleted])
        assert len(paths) == 1
        assert "/tmp/deleted.jpg" in paths[0]
        # Live photo untouched
        assert get_photo(conn, pid_live) is not None
        # Deleted photo gone
        assert get_photo(conn, pid_deleted) is None

    def test_empty_ids_returns_empty(self, conn):
        assert permanent_delete_photos(conn, []) == []
