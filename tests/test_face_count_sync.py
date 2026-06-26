"""Tests for _sync_face_counts_for_photos — the helper that keeps
photos.face_count in agreement with face_embeddings after reassign/dismiss.

Regression: prior implementation ran SELECT + UPDATE per photo in a
Python loop (N+1). New version uses a single correlated-subquery UPDATE.
"""

from __future__ import annotations

import numpy as np
import pytest

from bpp.constants import CLUSTER_DISMISSED
from bpp.db.connection import get_db, init_db


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test_face_count.db")
    init_db(db_path)
    return get_db(db_path)


def _make_photo(conn, filepath: str, face_count: int = 0) -> int:
    cur = conn.execute(
        "INSERT INTO photos"
        " (filepath, original_filename, file_size, file_mtime, face_count)"
        " VALUES (?, ?, 1000, 1000000, ?)",
        (filepath, filepath.rsplit("/", 1)[-1], face_count),
    )
    conn.commit()
    return cur.lastrowid


def _add_face(conn, photo_id: int, cluster_id: int, idx: int = 0) -> int:
    emb = np.random.randn(128).astype(np.float32).tobytes()
    cur = conn.execute(
        "INSERT INTO face_embeddings"
        " (photo_id, face_index, embedding, cluster_id,"
        "  bbox_x, bbox_y, bbox_w, bbox_h)"
        " VALUES (?, ?, ?, ?, 10, 20, 50, 60)",
        (photo_id, idx, emb, cluster_id),
    )
    conn.commit()
    return cur.lastrowid


def test_sync_face_counts_batches_into_single_update(conn):
    """_sync_face_counts_for_photos must update all given photos in one SQL call."""
    from bpp.web.bp_faces_manage import _sync_face_counts_for_photos

    p1 = _make_photo(conn, "/tmp/a.jpg", face_count=3)
    p2 = _make_photo(conn, "/tmp/b.jpg", face_count=3)
    p3 = _make_photo(conn, "/tmp/c.jpg", face_count=3)

    _add_face(conn, p1, cluster_id=0, idx=0)
    _add_face(conn, p2, cluster_id=0, idx=0)
    _add_face(conn, p2, cluster_id=1, idx=1)
    # p3 has no active faces (all dismissed)
    _add_face(conn, p3, cluster_id=CLUSTER_DISMISSED, idx=0)

    _sync_face_counts_for_photos(conn, [p1, p2, p3])

    counts = {
        r["id"]: r["face_count"]
        for r in conn.execute(
            "SELECT id, face_count FROM photos WHERE id IN (?, ?, ?)",
            (p1, p2, p3),
        ).fetchall()
    }
    assert counts[p1] == 1
    assert counts[p2] == 2
    assert counts[p3] == 0


def test_sync_face_counts_drains_no_faces_photo(conn):
    """When a photo has all faces dismissed, face_count should drop to 0
    so it moves into the 'No Faces Detected' smart album."""
    from bpp.web.bp_faces_manage import _sync_face_counts_for_photos

    p1 = _make_photo(conn, "/tmp/a.jpg", face_count=2)
    _add_face(conn, p1, cluster_id=CLUSTER_DISMISSED, idx=0)
    _add_face(conn, p1, cluster_id=CLUSTER_DISMISSED, idx=1)

    _sync_face_counts_for_photos(conn, [p1])

    face_count = conn.execute("SELECT face_count FROM photos WHERE id=?", (p1,)).fetchone()[0]
    assert face_count == 0


def test_sync_face_counts_empty_list_noop(conn):
    """Empty input must not touch DB."""
    from bpp.web.bp_faces_manage import _sync_face_counts_for_photos

    p1 = _make_photo(conn, "/tmp/a.jpg", face_count=5)
    _sync_face_counts_for_photos(conn, [])

    face_count = conn.execute("SELECT face_count FROM photos WHERE id=?", (p1,)).fetchone()[0]
    assert face_count == 5  # Untouched
