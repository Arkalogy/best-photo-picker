"""Each face-extraction chunk clusters its new faces against the whole
library. Before 0.2.2 a chunk knew only its own records, so a fresh chunk
numbered its clusters from zero and different people shared an id."""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from bpp.constants import CLUSTER_UNASSIGNED
from bpp.db.face_identity_remap import _assign_new_faces, _snapshot_cluster_photos
from bpp.web.face_extraction_phase6 import cluster_faces


def _schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE face_embeddings (id INTEGER PRIMARY KEY AUTOINCREMENT, photo_id INTEGER,"
        " face_index INTEGER, bbox_x INTEGER, bbox_y INTEGER, bbox_w INTEGER, bbox_h INTEGER,"
        f" embedding BLOB, quality REAL, cluster_id INTEGER NOT NULL DEFAULT {CLUSTER_UNASSIGNED},"
        " UNIQUE(photo_id, face_index))"
    )
    conn.execute(
        "CREATE TABLE face_cluster_feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT,"
        " cluster_id_a INTEGER, cluster_id_b INTEGER, distance REAL, created_at INTEGER)"
    )
    conn.execute(
        "CREATE TABLE face_hard_negatives (cluster_id_a INTEGER, cluster_id_b INTEGER,"
        " count INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT,"
        " PRIMARY KEY (cluster_id_a, cluster_id_b))"
    )
    conn.commit()


def _vector(sign: float) -> np.ndarray:
    v = np.zeros(128, dtype=np.float32)
    v[0] = sign
    return v


def _chunk(conn: sqlite3.Connection, pids: list[int], vector: np.ndarray) -> None:
    """Phase 5 wrote this chunk's rows unassigned; phase 6 clusters them."""
    conn.executemany(
        "INSERT INTO face_embeddings (photo_id, face_index, bbox_w, embedding, quality, cluster_id)"
        " VALUES (?, 0, 100, ?, 1.0, ?)",
        [(pid, vector.tobytes(), CLUSTER_UNASSIGNED) for pid in pids],
    )
    conn.commit()
    records = [(f"/p{pid}.jpg", pid, 0, vector.copy()) for pid in pids]
    cluster_faces(
        conn,
        records,
        {"face_cluster_threshold": 0.6},
        assign_new_faces_fn=_assign_new_faces,
        post_cluster_dedup=False,
    )


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    _schema(c)
    yield c
    c.close()


def test_a_later_chunk_does_not_reuse_an_earlier_chunks_cluster_id(conn):
    _chunk(conn, [1, 2], _vector(1.0))  # Alice, chunk 1
    _chunk(conn, [3, 4], _vector(-1.0))  # Bob, chunk 2: as different as a face can be
    clusters = _snapshot_cluster_photos(conn)
    assert len(clusters) == 2, clusters
    assert {frozenset(p) for p in clusters.values()} == {frozenset({1, 2}), frozenset({3, 4})}


def test_a_later_chunk_joins_the_same_person_from_an_earlier_chunk(conn):
    _chunk(conn, [1, 2], _vector(1.0))
    _chunk(conn, [5], _vector(1.0))  # Alice again, next chunk
    clusters = _snapshot_cluster_photos(conn)
    assert clusters == {0: {1, 2, 5}}


def test_a_row_of_another_size_still_reserves_its_cluster_id(conn):
    """A 512-d row at cluster 0 (another embedder's leftovers) cannot be a
    centroid for 128-d faces, but id 0 is taken all the same."""
    conn.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, bbox_w, embedding, quality, cluster_id)"
        " VALUES (9, 0, 100, ?, 1.0, 0)",
        (np.ones(512, dtype=np.float32).tobytes(),),
    )
    conn.commit()
    _chunk(conn, [1, 2], _vector(1.0))
    clusters = _snapshot_cluster_photos(conn)
    assert clusters[0] == {9}
    assert {1, 2} in [set(p) for c, p in clusters.items() if c != 0]


def test_a_damaged_sample_does_not_poison_its_cluster(conn):
    bad = np.full(128, np.nan, dtype=np.float32)
    conn.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, bbox_w, embedding, quality, cluster_id)"
        " VALUES (9, 0, 100, ?, 1.0, 0)",
        (bad.tobytes(),),
    )
    conn.commit()
    _chunk(conn, [1, 2], _vector(1.0))  # Alice becomes cluster 1 (0 is reserved)
    _chunk(conn, [3], _vector(1.0))  # Alice again: must join 1, not float to 2
    clusters = _snapshot_cluster_photos(conn)
    assert clusters[1] == {1, 2, 3}, clusters


def test_cached_faces_in_the_chunk_do_not_change_the_assignment(conn):
    """The library centroid is the one representation of a cluster: a chunk
    that happens to hold some of its already-assigned faces must assign a
    new face the same way as a chunk that holds none."""

    def unit(theta: float) -> np.ndarray:
        v = np.zeros(128, dtype=np.float32)
        v[0], v[1] = np.cos(theta), np.sin(theta)
        return v

    rows = [(1, unit(0.0)), (2, unit(0.0)), (3, unit(0.0)), (4, unit(0.5))]
    conn.executemany(
        "INSERT INTO face_embeddings (photo_id, face_index, bbox_w, embedding, quality, cluster_id)"
        " VALUES (?, 0, 100, ?, 1.0, 0)",
        [(pid, v.tobytes()) for pid, v in rows],
    )
    conn.commit()
    new = unit(-0.4)

    def assign(records_with_cached: bool) -> int:
        conn.execute("DELETE FROM face_embeddings WHERE photo_id=5")
        conn.execute(
            "INSERT INTO face_embeddings"
            " (photo_id, face_index, bbox_w, embedding, quality, cluster_id)"
            " VALUES (5, 0, 100, ?, 1.0, ?)",
            (new.tobytes(), CLUSTER_UNASSIGNED),
        )
        conn.commit()
        records = [("/p5.jpg", 5, 0, new.copy())]
        if records_with_cached:
            records.insert(0, ("/p4.jpg", 4, 0, unit(0.5)))
        cluster_faces(
            conn,
            records,
            {"face_cluster_threshold": 0.6},
            assign_new_faces_fn=_assign_new_faces,
            post_cluster_dedup=False,
        )
        return conn.execute("SELECT cluster_id FROM face_embeddings WHERE photo_id=5").fetchone()[0]

    assert assign(False) == assign(True) == 0
