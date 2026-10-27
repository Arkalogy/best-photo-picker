"""The one rule for allocating a new face cluster id: above every id in
use, in faces, manual tags or person albums (a person known only by tags
has no face rows, and must not have a new cluster land on their id)."""

from __future__ import annotations

import sqlite3


def highest_cluster_id(conn: sqlite3.Connection) -> int:
    """The highest cluster id in use anywhere: faces, manual tags alone, or
    a person album alone (a tag-only person). -1 when none."""
    floor = -1
    for sql in (
        "SELECT MAX(cluster_id) FROM face_embeddings",
        "SELECT MAX(cluster_id) FROM photo_person_tags",
        "SELECT MAX(smart_person_cluster_id) FROM albums WHERE album_type='smart_person'",
    ):
        try:
            row = conn.execute(sql).fetchone()
        except sqlite3.OperationalError:
            continue  # a library (or a test schema) without that table
        if row and row[0] is not None:
            floor = max(floor, int(row[0]))
    return floor


def next_cluster_id(conn: sqlite3.Connection) -> int:
    """A cluster id nothing uses yet."""
    return highest_cluster_id(conn) + 1
