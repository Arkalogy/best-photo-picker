"""TDD tests for H-7: batch rename must not overwrite pre-existing files."""

from __future__ import annotations

import os
import sqlite3

import pytest

from bpp.db.batch_rename import apply_rename
from bpp.db.connection import init_db
from bpp.db.photos import upsert_photo


@pytest.fixture()
def setup(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    photo = tmp_path / "photo_a.jpg"
    photo.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
    pid = upsert_photo(
        conn,
        {
            "filepath": str(photo),
            "original_filename": "photo_a.jpg",
            "file_size": 53,
            "file_mtime": 1000.0,
        },
    )
    return conn, tmp_path, pid, str(photo)


class TestRenameOverwriteGuard:
    def test_overwrite_blocked_when_target_exists(self, setup):
        """If new_path already exists on disk, the rename must be skipped."""
        conn, tmp_path, pid, old_path = setup

        target = tmp_path / "existing.jpg"
        target.write_bytes(b"precious data")

        mapping = [
            {
                "id": pid,
                "old_filepath": old_path,
                "new_filepath": str(target),
                "changed": True,
            }
        ]
        results = apply_rename(conn, mapping)

        assert len(results) == 1
        assert results[0]["success"] is False
        assert "exists" in results[0]["error"].lower()

        assert os.path.isfile(old_path)
        assert target.read_bytes() == b"precious data"

    def test_rename_to_self_allowed(self, setup):
        """Renaming a file to itself (no-op) should not be blocked."""
        conn, _tmp_path, pid, old_path = setup

        mapping = [
            {
                "id": pid,
                "old_filepath": old_path,
                "new_filepath": old_path,
                "changed": False,
            }
        ]
        results = apply_rename(conn, mapping)
        if results:
            assert (
                results[0].get("success") is not False
                or "exists" not in (results[0].get("error") or "").lower()
            )

    def test_rename_to_new_path_works(self, setup):
        """Normal rename to a non-existing target must succeed."""
        conn, tmp_path, pid, old_path = setup

        new_path = str(tmp_path / "renamed.jpg")
        mapping = [
            {
                "id": pid,
                "old_filepath": old_path,
                "new_filepath": new_path,
                "changed": True,
            }
        ]
        results = apply_rename(conn, mapping)

        assert len(results) == 1
        assert results[0]["success"] is True
        assert os.path.isfile(new_path)
        assert not os.path.isfile(old_path)
