"""Regression: extract_and_cluster_faces pre-loads cached embeddings
in batched IN-clauses, not one SELECT per photo.

Why this guard exists: the pre-batching version had a per-photo
`SELECT face_index, embedding FROM face_embeddings WHERE photo_id=?`
inside the with_faces loop. On large libraries that's 10k+ single-row
reads from the same table — the kernel can't batch them, the connection
hops on every call, and clustering walltime was dominated by the I/O.

After the fix, the cached-embedding read should be O(ceil(N/500))
queries regardless of how many photos are in `with_faces`.

Project rule: no DB queries in loops. Never
`for x in items: conn.execute(... x ...)` — batch with `IN` clauses,
`executemany()`, or pre-load into dicts for O(1) lookup. This test
locks the policy so a future contributor can't silently regress it.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest


@pytest.fixture()
def faces_db(tmp_path):
    """Real on-disk SQLite DB with the schema needed by face_worker."""
    from bpp.db.connection import init_db

    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    return db_path


def _setup_photos_and_embeddings(db_path: str, n_photos: int) -> dict[str, int]:
    """Insert n_photos rows, each with one cached face embedding.

    Returns the photo_map (filepath → photo_id) the worker expects."""
    from bpp.constants import CLUSTER_UNASSIGNED

    photo_map: dict[str, int] = {}
    conn = sqlite3.connect(db_path)
    try:
        # Minimal photos rows. The worker only needs photo_map to resolve
        # ids; the photo row schema doesn't matter for this test.
        for i in range(n_photos):
            fp = f"/lib/photos/test/img_{i:06d}.jpg"
            cur = conn.execute(
                "INSERT INTO photos "
                "(filepath, original_filename, file_size, file_mtime, sha256) "
                "VALUES (?, ?, ?, ?, ?)",
                (fp, f"img_{i:06d}.jpg", 1024, 0.0, f"hash_{i:06d}"),
            )
            pid = cur.lastrowid
            photo_map[fp] = pid

            # One cached embedding per photo. Random vector, normalized.
            rng = np.random.default_rng(seed=i)
            emb = rng.standard_normal(128).astype(np.float32)
            emb = emb / np.linalg.norm(emb)
            conn.execute(
                "INSERT INTO face_embeddings "
                "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
                " embedding, quality, cluster_id) "
                "VALUES (?, 0, 0, 0, 100, 100, ?, 0.9, ?)",
                (pid, emb.tobytes(), CLUSTER_UNASSIGNED),
            )
        conn.commit()
    finally:
        conn.close()
    return photo_map


class _CountingConn:
    """sqlite3.Connection wrapper that counts every execute() that
    matches a substring. Doesn't intercept executemany or
    SELECTs that don't match the pattern."""

    def __init__(self, real_conn: sqlite3.Connection, match_substr: str):
        self._conn = real_conn
        self._match = match_substr
        self.matched_calls = 0
        self.matched_sqls: list[str] = []

    def execute(self, sql, params=None):
        if self._match in sql:
            self.matched_calls += 1
            self.matched_sqls.append(sql)
        if params is None:
            return self._conn.execute(sql)
        return self._conn.execute(sql, params)

    def executemany(self, sql, seq):
        return self._conn.executemany(sql, seq)

    def commit(self):
        return self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_cached_embedding_load_batches_in_500s(faces_db):
    """1500 photos with cached embeddings → 3 batched SELECTs, not 1500."""
    from bpp.web.face_worker import extract_and_cluster_faces

    n = 1500
    photo_map = _setup_photos_and_embeddings(faces_db, n)
    with_faces = [{"filepath": fp, "faces": [{"bbox": [0, 0, 100, 100]}]} for fp in photo_map]

    real_conn = sqlite3.connect(faces_db)
    real_conn.row_factory = sqlite3.Row
    counter = _CountingConn(real_conn, match_substr="FROM face_embeddings WHERE photo_id IN")
    try:
        extract_and_cluster_faces(
            counter,  # type: ignore[arg-type]
            with_faces=with_faces,
            photo_map=photo_map,
            max_long_side=512,
            face_confidence=0.3,
            config={"face_cluster_threshold": 0.6},
        )
    finally:
        real_conn.close()

    # ceil(1500 / 500) = 3 — that's the read for cached embeddings.
    # Other IN-clause queries (stale cleanup, dismissed-slot snapshot)
    # only fire when there's something to clean up; with all-cached data
    # and no need_extract, only the read path executes.
    cached_load_calls = [s for s in counter.matched_sqls if "cluster_id !=" in s]
    assert len(cached_load_calls) == 3, (
        f"Expected 3 batched cached-embedding reads (1500/500), got "
        f"{len(cached_load_calls)}:\n" + "\n".join(cached_load_calls)
    )


def test_cached_embedding_load_does_not_query_per_photo(faces_db):
    """Sanity inverse: with 100 photos, the per-photo equality form
    (`WHERE photo_id=?`) must NOT be used for the cached read. If a
    contributor reverts to the old loop, this catches it."""
    from bpp.web.face_worker import extract_and_cluster_faces

    n = 100
    photo_map = _setup_photos_and_embeddings(faces_db, n)
    with_faces = [{"filepath": fp, "faces": []} for fp in photo_map]

    real_conn = sqlite3.connect(faces_db)
    real_conn.row_factory = sqlite3.Row
    counter = _CountingConn(
        real_conn,
        match_substr="FROM face_embeddings WHERE photo_id=?",
    )
    try:
        extract_and_cluster_faces(
            counter,  # type: ignore[arg-type]
            with_faces=with_faces,
            photo_map=photo_map,
            max_long_side=512,
            face_confidence=0.3,
            config={"face_cluster_threshold": 0.6},
        )
    finally:
        real_conn.close()

    assert counter.matched_calls == 0, (
        f"Found {counter.matched_calls} per-photo equality SELECTs — the "
        "batching regressed. Use IN-clauses instead."
    )


def test_no_cached_embeddings_still_works(faces_db):
    """Edge case: 0 candidate photo_ids → skip the IN-clause entirely
    (an empty IN list is a SQL syntax error in sqlite). The function
    must not raise."""
    from bpp.web.face_worker import extract_and_cluster_faces

    real_conn = sqlite3.connect(faces_db)
    real_conn.row_factory = sqlite3.Row
    try:
        # No with_faces at all — the only loop never runs.
        faces_found, n_clusters = extract_and_cluster_faces(
            real_conn,
            with_faces=[],
            photo_map={},
            max_long_side=512,
            face_confidence=0.3,
            config={"face_cluster_threshold": 0.6},
        )
    finally:
        real_conn.close()

    assert faces_found == 0
    assert n_clusters == 0
