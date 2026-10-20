"""Shared fixtures and helpers of the test_face_review_flow.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import numpy as np
import pytest

from bpp.db.connection import get_db, init_db

# ────────────────────────── fixtures ──────────────────────────


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test_face_review.db")
    init_db(db_path)
    return get_db(db_path)


@pytest.fixture
def client(tmp_path):
    from bpp.web.app import create_app

    d = str(tmp_path.resolve())
    # library_path=None keeps serve_mode=False, which skips
    # _start_file_health_checks. Otherwise its background _startup_scan
    # thread can race the seed: if it queries `photos` after the test
    # inserts fake-path rows, check_missing() flags them all
    # missing=1 → load_face_clusters filters them out → the handler
    # silently drops the pair (bp_faces_manage.py:849). Reproduces only
    # under the slower thread scheduling on Linux CI runners.
    app = create_app(workdir=d, library_path=None)
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _make_photo(conn, filepath: str) -> int:
    cur = conn.execute(
        "INSERT INTO photos (filepath, original_filename, file_size, file_mtime)"
        " VALUES (?, ?, 1000, 1000000)",
        (filepath, filepath.rsplit("/", 1)[-1]),
    )
    conn.commit()
    return cur.lastrowid


def _add_cluster(
    conn,
    cluster_id: int,
    embedding: np.ndarray,
    *,
    n_faces: int = 1,
    photo_prefix: str = "/tmp/",
) -> list[int]:
    """Create N face_embeddings rows for a cluster, all centered on `embedding`.

    Returns the list of face_embedding IDs.
    """
    ids = []
    for i in range(n_faces):
        pid = _make_photo(conn, f"{photo_prefix}c{cluster_id}_f{i}.jpg")
        cur = conn.execute(
            "INSERT INTO face_embeddings"
            " (photo_id, face_index, embedding, cluster_id,"
            "  bbox_x, bbox_y, bbox_w, bbox_h)"
            " VALUES (?, 0, ?, ?, 10, 20, 50, 60)",
            (pid, embedding.astype(np.float32).tobytes(), cluster_id),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def _unit_vec(dim: int = 128, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


# ────────── /api/faces/review-pairs/next + /verdict (integration) ──────────


def _seed_library(ctx, *, with_pair: bool = True):
    """Seed a library with clusters 1 and 2 under the ambiguity ceiling (0.75)."""
    conn = ctx.get_conn()
    a = _unit_vec(seed=1)
    if with_pair:
        b = a + 0.55 * _unit_vec(seed=2)
        _add_cluster(conn, 1, a, n_faces=3)
        _add_cluster(conn, 2, b, n_faces=2)
    else:
        _add_cluster(conn, 1, a)
