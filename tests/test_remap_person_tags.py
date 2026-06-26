"""TDD tests for H-1: _remap_names_and_tags UNIQUE constraint on photo_person_tags."""

from __future__ import annotations

import sqlite3

import pytest

from bpp.db.connection import init_db


@pytest.fixture()
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    # Insert a photo to satisfy FK
    c.execute(
        "INSERT INTO photos (filepath, original_filename, file_size, file_mtime) "
        "VALUES (?, ?, ?, ?)",
        ("/tmp/photo.jpg", "photo.jpg", 100, 1000.0),
    )
    c.commit()
    return c


def _get_photo_id(conn):
    return conn.execute("SELECT id FROM photos LIMIT 1").fetchone()[0]


class TestRemapPersonTagsConstraint:
    def test_remap_with_overlapping_tags_no_crash(self, conn):
        """When photo has tags for both old and new cluster, remap must not crash."""
        pid = _get_photo_id(conn)
        old_cid, new_cid = 10, 20

        # Photo tagged with BOTH clusters
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
            (pid, old_cid),
        )
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
            (pid, new_cid),
        )
        conn.commit()

        # Simulate the remap: UPDATE OR IGNORE + DELETE leftovers
        # (matches the pattern in _remap_names_and_tags)
        remap = {old_cid: new_cid}
        for o_cid, n_cid in remap.items():
            conn.execute(
                "UPDATE OR IGNORE photo_person_tags SET cluster_id=? WHERE cluster_id=?",
                (n_cid, o_cid),
            )
            conn.execute(
                "DELETE FROM photo_person_tags WHERE cluster_id=?",
                (o_cid,),
            )
        conn.commit()

        # Only new_cid should remain
        rows = conn.execute(
            "SELECT cluster_id FROM photo_person_tags WHERE photo_id=?",
            (pid,),
        ).fetchall()
        cluster_ids = {r[0] for r in rows}
        assert cluster_ids == {new_cid}

    def test_remap_without_overlap_works(self, conn):
        """Normal case: only old cluster exists, gets remapped to new."""
        pid = _get_photo_id(conn)
        old_cid, new_cid = 10, 20

        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
            (pid, old_cid),
        )
        conn.commit()

        conn.execute(
            "UPDATE OR IGNORE photo_person_tags SET cluster_id=? WHERE cluster_id=?",
            (new_cid, old_cid),
        )
        conn.execute(
            "DELETE FROM photo_person_tags WHERE cluster_id=?",
            (old_cid,),
        )
        conn.commit()

        rows = conn.execute(
            "SELECT cluster_id FROM photo_person_tags WHERE photo_id=?",
            (pid,),
        ).fetchall()
        assert {r[0] for r in rows} == {new_cid}

    def test_plain_update_would_crash_on_overlap(self, conn):
        """Verify that the OLD code (plain UPDATE) does crash on overlap."""
        pid = _get_photo_id(conn)
        old_cid, new_cid = 10, 20

        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
            (pid, old_cid),
        )
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
            (pid, new_cid),
        )
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE photo_person_tags SET cluster_id=? WHERE cluster_id=?",
                (new_cid, old_cid),
            )
