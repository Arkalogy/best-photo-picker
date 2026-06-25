"""Tests for Moment clustering (bpp/db/moments.py)."""

from __future__ import annotations

import os
import tempfile

import numpy as np

from bpp.db.clip import CLIP_EMBEDDING_DIM, upsert_clip_embedding
from bpp.db.connection import init_db
from bpp.db.moments import assign_moment_clusters, get_moment_groups


def _unit(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(CLIP_EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _similar(base: np.ndarray, jitter: float = 0.02, seed: int = 99) -> np.ndarray:
    """A vector with cosine ~0.99 to base (same scene, slight variation)."""
    rng = np.random.default_rng(seed)
    v = base + jitter * rng.standard_normal(CLIP_EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _photo(conn, filepath: str, date: str) -> int:
    cur = conn.execute(
        "INSERT INTO photos (filepath, original_filename, file_size, file_mtime, date) "
        "VALUES (?,?,?,?,?)",
        (filepath, os.path.basename(filepath), 1, 1, date),
    )
    conn.commit()
    return int(cur.lastrowid)


def _fresh_db():
    d = tempfile.mkdtemp()
    return init_db(os.path.join(d, "t.db"))


def test_similar_shots_near_in_time_form_one_moment():
    conn = _fresh_db()
    base = _unit(1)
    p1 = _photo(conn, "/a/1.jpg", "2024-07-24T10:00:00")
    p2 = _photo(conn, "/a/2.jpg", "2024-07-24T10:00:30")  # +30s, within 90s
    p3 = _photo(conn, "/a/3.jpg", "2024-07-24T10:00:10")  # different scene
    upsert_clip_embedding(conn, p1, base)
    upsert_clip_embedding(conn, p2, _similar(base))
    upsert_clip_embedding(conn, p3, _unit(777))  # orthogonal-ish → cosine < 0.90

    multi = assign_moment_clusters(conn, threshold=0.90, time_window_seconds=90)
    assert multi == 2, "the two similar shots should be the only ones grouped"

    rows = {
        r[0]: (r[1], r[2])
        for r in conn.execute("SELECT id, moment_cluster_id, moment_size FROM photos")
    }
    assert rows[p1][0] == rows[p2][0] != 0, "p1 and p2 share a non-zero moment id"
    assert rows[p1][1] == 2 and rows[p2][1] == 2
    assert rows[p3] == (0, 1), "the different-scene shot is a singleton"

    groups = get_moment_groups(conn)
    assert len(groups) == 1
    assert groups[0]["size"] == 2
    assert sorted(groups[0]["photo_ids"]) == sorted([p1, p2])


def test_time_window_gates_visually_identical_shots():
    """Same scene but >window apart must NOT merge (different moment)."""
    conn = _fresh_db()
    base = _unit(2)
    p1 = _photo(conn, "/a/1.jpg", "2024-07-24T10:00:00")
    p2 = _photo(conn, "/a/2.jpg", "2024-07-24T10:05:00")  # +300s, outside 90s
    upsert_clip_embedding(conn, p1, base)
    upsert_clip_embedding(conn, p2, _similar(base))

    multi = assign_moment_clusters(conn, threshold=0.90, time_window_seconds=90)
    assert multi == 0, "identical-looking shots 5 min apart are separate moments"
    assert get_moment_groups(conn) == []


def test_photo_without_embedding_is_singleton():
    conn = _fresh_db()
    base = _unit(3)
    p1 = _photo(conn, "/a/1.jpg", "2024-07-24T10:00:00")
    p2 = _photo(conn, "/a/2.jpg", "2024-07-24T10:00:20")
    upsert_clip_embedding(conn, p1, base)
    # p2 has no CLIP embedding → can't match anything
    upsert_clip_embedding(conn, p2, _similar(base))
    p3 = _photo(conn, "/a/3.jpg", "2024-07-24T10:00:10")  # no embedding at all

    assign_moment_clusters(conn, threshold=0.90, time_window_seconds=90)
    row3 = conn.execute(
        "SELECT moment_cluster_id, moment_size FROM photos WHERE id=?", (p3,)
    ).fetchone()
    assert tuple(row3) == (0, 1)


def test_soft_deleted_photos_excluded_and_reset():
    conn = _fresh_db()
    base = _unit(4)
    p1 = _photo(conn, "/a/1.jpg", "2024-07-24T10:00:00")
    p2 = _photo(conn, "/a/2.jpg", "2024-07-24T10:00:30")
    upsert_clip_embedding(conn, p1, base)
    upsert_clip_embedding(conn, p2, _similar(base))
    # First run groups them.
    assert assign_moment_clusters(conn, threshold=0.90, time_window_seconds=90) == 2
    # Trash p2; re-run should leave p1 a singleton and clear p2's stale values.
    conn.execute("UPDATE photos SET deleted_at=datetime('now') WHERE id=?", (p2,))
    conn.commit()
    assert assign_moment_clusters(conn, threshold=0.90, time_window_seconds=90) == 0
    rows = {
        r[0]: (r[1], r[2])
        for r in conn.execute("SELECT id, moment_cluster_id, moment_size FROM photos")
    }
    assert rows[p1] == (0, 1)
    assert rows[p2] == (0, 1), "trashed photo's stale moment values are reset"


def test_idempotent_rerun():
    conn = _fresh_db()
    base = _unit(5)
    p1 = _photo(conn, "/a/1.jpg", "2024-07-24T10:00:00")
    p2 = _photo(conn, "/a/2.jpg", "2024-07-24T10:00:30")
    upsert_clip_embedding(conn, p1, base)
    upsert_clip_embedding(conn, p2, _similar(base))
    a = assign_moment_clusters(conn, threshold=0.90, time_window_seconds=90)
    b = assign_moment_clusters(conn, threshold=0.90, time_window_seconds=90)
    assert a == b == 2
    assert len(get_moment_groups(conn)) == 1


# ===========================================================================
# v42 migration (moments columns) — coverage the feature commit missed
# ===========================================================================


class TestSchemaV42Migration:
    """The v42 migration adds moment_cluster_id + moment_size + the
    partial index. Mirrors the v41 migration test pattern."""

    def test_schema_version_includes_v42(self) -> None:
        from bpp.db.schema import SCHEMA_VERSION

        assert SCHEMA_VERSION >= 42

    def test_canonical_schema_has_moment_columns(self) -> None:
        from bpp.db.schema import INDEXES_SQL, TABLES_SQL

        assert "moment_cluster_id" in TABLES_SQL
        assert "moment_size" in TABLES_SQL
        assert "idx_photos_moment" in INDEXES_SQL

    def test_v42_migration_is_idempotent(self) -> None:
        """Calling the migration twice must not raise or duplicate columns."""
        import sqlite3

        from bpp.db.migrations_latest import _migrate_v42

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY, filepath TEXT)")
        _migrate_v42(conn)
        _migrate_v42(conn)  # second call is a no-op
        cols = [row[1] for row in conn.execute("PRAGMA table_info(photos)").fetchall()]
        assert cols.count("moment_cluster_id") == 1
        assert cols.count("moment_size") == 1

    def test_v42_migration_skips_missing_table(self) -> None:
        import sqlite3

        from bpp.db.migrations_latest import _migrate_v42

        conn = sqlite3.connect(":memory:")
        _migrate_v42(conn)  # no photos table — must skip, not raise
