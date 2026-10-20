"""Shared fixtures and helpers of the test_clustering_stability.py suite (split for the 500-LOC
cap)."""

from __future__ import annotations

import os
import re
import sqlite3
import time

import numpy as np

from bpp.constants import CLUSTER_DISMISSED, CLUSTER_UNASSIGNED
from bpp.db.connection import init_db
from bpp.scoring.face_cluster import cluster_faces

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_person_embeddings(person_seed: int, count: int, noise: float = 0.02) -> list[np.ndarray]:
    """Generate embeddings for one person, tight around a centroid.

    Returns float32 arrays — matches production write dtype (schema v35+)
    so round-tripping through tobytes() + frombuffer(dtype=np.float32)
    preserves shape (128,) instead of reinterpreting 1024 bytes as 256
    garbage floats.
    """
    rng = np.random.RandomState(person_seed)
    centroid = rng.randn(128)
    centroid = centroid / np.linalg.norm(centroid)
    return [(centroid + rng.randn(128) * noise).astype(np.float32) for _ in range(count)]


def _make_multi_person(
    n_people: int = 3,
    faces_per: int = 5,
    noise: float = 0.02,
    seed_base: int = 100,
) -> list[np.ndarray]:
    """Generate embeddings for multiple people. Returns flat list."""
    embeddings: list[np.ndarray] = []
    for p in range(n_people):
        embeddings.extend(_make_person_embeddings(seed_base + p, faces_per, noise))
    return embeddings


def _make_multi_person_with_truth(
    n_people: int = 3,
    faces_per: int = 5,
    noise: float = 0.02,
    seed_base: int = 100,
) -> tuple[list[np.ndarray], list[int]]:
    """Generate embeddings + ground truth labels."""
    embeddings: list[np.ndarray] = []
    labels: list[int] = []
    for p in range(n_people):
        embeddings.extend(_make_person_embeddings(seed_base + p, faces_per, noise))
        labels.extend([p] * faces_per)
    return embeddings, labels


def _groupings(labels: list[int]) -> set[frozenset[int]]:
    """Convert labels to grouping structure (set of frozensets of indices)."""
    groups: dict[int, set[int]] = {}
    for idx, label in enumerate(labels):
        groups.setdefault(label, set()).add(idx)
    return {frozenset(v) for v in groups.values()}


def _setup_face_db(tmp_path, n_photos: int = 10) -> tuple[sqlite3.Connection, str]:
    """Create a test DB with photo records."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    for i in range(n_photos):
        conn.execute(
            "INSERT INTO photos "
            "(filepath, original_filename, sha256, file_size, file_mtime) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                f"/test/photo_{i}.jpg",
                f"photo_{i}.jpg",
                f"sha_{i}",
                1000,
                1704067200.0,
            ),
        )
    conn.commit()
    return conn, db_path


def _insert_embeddings(
    conn: sqlite3.Connection,
    embs: list[np.ndarray],
    cluster_id: int = CLUSTER_UNASSIGNED,
) -> list[int]:
    """Insert embeddings into face_embeddings, return photo_ids used."""
    photo_ids = [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id").fetchall()]
    for i, emb in enumerate(embs):
        pid = photo_ids[i % len(photo_ids)]
        conn.execute(
            "INSERT OR REPLACE INTO face_embeddings "
            "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
            "embedding, cluster_id, quality) VALUES (?,?,?,?,?,?,?,?,?)",
            (pid, i // len(photo_ids), 0, 0, 50, 50, emb.tobytes(), cluster_id, 0.5),
        )
    conn.commit()
    return photo_ids


def _cluster_db_embeddings(conn: sqlite3.Connection, threshold: float = 0.55) -> list[int]:
    """Load embeddings from DB, cluster, and update. Return labels."""
    rows = conn.execute(
        f"SELECT id, embedding FROM face_embeddings WHERE cluster_id != {CLUSTER_DISMISSED}"
    ).fetchall()
    fe_ids = [r[0] for r in rows]
    db_embs = [np.frombuffer(r[1], dtype=np.float32) for r in rows]
    labels = cluster_faces(db_embs, threshold=threshold)
    conn.executemany(
        "UPDATE face_embeddings SET cluster_id=? WHERE id=?",
        zip(labels, fe_ids, strict=True),
    )
    conn.commit()
    return labels


_FETCH_ASSIGNMENTS = (
    "SELECT photo_id, face_index, cluster_id FROM face_embeddings ORDER BY photo_id, face_index"
)


def _sandbox_extract_one(
    filepath,
    max_long_side,
    min_confidence=0.2,
    embedding_confidence=0.65,
    min_embedding_quality=0.25,
    method=None,
):
    """Thread-safe extraction: deterministic embeddings per filepath + random delay.

    Designed for real ThreadPoolExecutor tests. The random delay forces
    as_completed to return futures in non-deterministic order, exposing
    the ordering bug in extract_and_cluster_faces.

    Photos are grouped into "people" (5 photos per person) so clustering
    produces meaningful multi-person groupings.
    """
    time.sleep(os.urandom(1)[0] / 1000.0)  # 0-255ms random delay
    m = re.search(r"photo_(\d+)", filepath)
    if not m:
        return []
    idx = int(m.group(1))
    person_id = idx // 5
    rng_person = np.random.RandomState(1000 + person_id)
    centroid = rng_person.randn(128)
    centroid = centroid / np.linalg.norm(centroid)
    rng_photo = np.random.RandomState(2000 + idx)
    emb = (centroid + rng_photo.randn(128) * 0.02).astype(np.float32)
    return [{"bbox": (10, 10, 50, 50), "embedding": emb}]


# ===========================================================================
# 12. RE-ANALYSIS — cluster ID preservation
# ===========================================================================

_FETCH_CLUSTER_IDS = (
    "SELECT fe.photo_id, fe.face_index, fe.cluster_id "
    "FROM face_embeddings fe ORDER BY fe.photo_id, fe.face_index"
)
