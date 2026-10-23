"""Shared fixtures and helpers of the test_face_name_preservation.py suite (split for the 500-LOC
cap)."""

from __future__ import annotations

import json
import sqlite3

import numpy as np

from bpp.db.connection import init_db
from bpp.db.photos import bulk_upsert_photos

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path, n_photos: int = 20) -> tuple[sqlite3.Connection, str]:
    """Create a test DB with photo records and return (conn, db_path)."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    photos = []
    for i in range(n_photos):
        photos.append(
            {
                "filepath": f"/photos/img_{i:04d}.jpg",
                "original_filename": f"img_{i:04d}.jpg",
                "batch_name": "test_batch",
                "import_timestamp": "2024-01-01T00:00:00",
                "file_hash": f"hash_{i:04d}",
            }
        )
    bulk_upsert_photos(conn, photos)
    # Mark all photos as not missing so they pass ACTIVE_PHOTO_SQL filter
    conn.execute("UPDATE photos SET missing=0")
    conn.commit()
    return conn, db_path


def _get_photo_ids(conn: sqlite3.Connection) -> list[int]:
    """Return all photo IDs sorted."""
    return [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id").fetchall()]


def _insert_embeddings(
    conn: sqlite3.Connection,
    cluster_assignments: dict[int, list[int]],
) -> None:
    """Insert face embeddings with given cluster_id → [photo_id, ...] mapping.

    Each photo gets face_index=0 for its first cluster appearance.
    Uses INSERT OR REPLACE to handle re-inserts cleanly.
    """
    # Track face_index per photo to avoid UNIQUE constraint violations
    photo_fi: dict[int, int] = {}
    for cluster_id, photo_ids in cluster_assignments.items():
        for pid in photo_ids:
            fi = photo_fi.get(pid, 0)
            photo_fi[pid] = fi + 1
            emb = np.random.RandomState(pid + cluster_id * 1000).randn(128)
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, cluster_id, embedding) "
                "VALUES (?, ?, ?, ?)",
                (pid, fi, cluster_id, emb.astype(np.float32).tobytes()),
            )
    conn.commit()


def _create_named_person_album(
    conn: sqlite3.Connection,
    name: str,
    cluster_id: int,
    photo_ids: list[int],
) -> int:
    """Create a named smart_person album and populate album_photos."""
    rule_json = json.dumps({"cluster_id": cluster_id}, sort_keys=True)
    conn.execute(
        "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
        (name, rule_json),
    )
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for pid in photo_ids:
        conn.execute(
            "INSERT OR IGNORE INTO album_photos (album_id, photo_id) VALUES (?, ?)",
            (album_id, pid),
        )
    conn.commit()
    return album_id


def _get_person_album_names(conn: sqlite3.Connection) -> dict[int, str]:
    """Return {cluster_id: album_name} for all smart_person albums."""
    rows = conn.execute(
        "SELECT name, rule_json FROM albums WHERE album_type='smart_person'"
    ).fetchall()
    result = {}
    for name, rule_json in rows:
        try:
            rule = json.loads(rule_json)
            result[rule["cluster_id"]] = name
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return result
