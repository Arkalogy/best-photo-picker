"""Per-photo column writers: scores and perceptual hashes.

Split out of :mod:`bpp.db.photos` (500-LOC cap); the names are
re-exported there so callers keep their import path.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from bpp.scoring.registry import get_score_db_columns

_SCORE_COLUMNS = get_score_db_columns()


def update_scores(conn: sqlite3.Connection, filepath: str, scores: dict[str, Any]) -> None:
    """Update score columns for a photo."""
    allowed = {*_SCORE_COLUMNS, "cluster_size"}
    updates = {k: v for k, v in scores.items() if k in allowed}
    if not updates:
        return
    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(
        f"UPDATE photos SET {set_clause} WHERE filepath=?",
        [*updates.values(), filepath],
    )
    conn.commit()


def update_hashes_many(
    conn: sqlite3.Connection, rows: list[tuple[str, int | None, int | None]]
) -> None:
    """Persist perceptual hashes for many photos in one transaction:
    ``rows`` are ``(filepath, phash, ahash)``."""
    if not rows:
        return
    conn.executemany(
        "UPDATE photos SET phash=?, ahash=? WHERE filepath=?",
        [(phash, ahash, fp) for fp, phash, ahash in rows],
    )
    conn.commit()


def update_hashes(
    conn: sqlite3.Connection, filepath: str, phash: int | None, ahash: int | None
) -> None:
    """Persist perceptual hashes (phash, ahash) for a photo by filepath.

    Used by the background dedup pass to fill in hashes lazily after
    initial analyze. Pass None for either if computation failed.
    """
    conn.execute(
        "UPDATE photos SET phash=?, ahash=? WHERE filepath=?",
        (phash, ahash, filepath),
    )
    conn.commit()
