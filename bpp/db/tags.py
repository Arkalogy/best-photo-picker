"""CRUD operations for tags and photo-tag associations."""

from __future__ import annotations

import sqlite3
from typing import Any

from bpp.constants import active_photo_sql
from bpp.db.photos import PHOTO_COLS_SLIM_PREFIXED

_TAG_COLS = "id, name, created_at"


def create_tag(conn: sqlite3.Connection, name: str) -> int:
    """Create a tag (or return existing ID if name matches case-insensitively)."""
    name = name.strip().lower()
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    conn.execute("INSERT INTO tags (name) VALUES (?)", (name,))
    conn.commit()
    row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise RuntimeError(f"Tag creation failed - row not found for {name!r}")
    return row[0]


def get_tag(conn: sqlite3.Connection, tag_id: int) -> dict[str, Any] | None:
    """Get a single tag by ID."""
    row = conn.execute(f"SELECT {_TAG_COLS} FROM tags WHERE id = ?", (tag_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def list_tags(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """List all tags ordered by name."""
    rows = conn.execute(f"SELECT {_TAG_COLS} FROM tags ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def list_tags_with_counts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """List all tags with photo counts and a cover photo (top-scored
    active member) — feeds the Tags browse view's cards."""
    rows = conn.execute(
        "SELECT t.id, t.name, t.created_at, "
        "  COUNT(p.id) AS count, "
        # Top-scored member as the card cover. MAX() pairs the filepath
        # with its own score row via the tuple trick below.
        "  (SELECT p2.filepath FROM photo_tags pt2 "
        "     JOIN photos p2 ON p2.id = pt2.photo_id "
        f"    WHERE pt2.tag_id = t.id AND {active_photo_sql('p2')} "
        "     ORDER BY p2.aggregate_score DESC LIMIT 1) AS cover_filepath "
        "FROM tags t "
        "LEFT JOIN photo_tags pt ON pt.tag_id = t.id "
        f"LEFT JOIN photos p ON p.id = pt.photo_id AND {active_photo_sql('p')} "
        "GROUP BY t.id ORDER BY t.name"
    ).fetchall()
    return [dict(r) for r in rows]


def rename_tag(conn: sqlite3.Connection, tag_id: int, new_name: str) -> None:
    """Rename a tag."""
    new_name = new_name.strip().lower()
    conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new_name, tag_id))
    conn.commit()


def delete_tag(conn: sqlite3.Connection, tag_id: int) -> None:
    """Delete a tag and all its photo associations (CASCADE)."""
    conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
    conn.commit()


def merge_tags(conn: sqlite3.Connection, source_id: int, target_id: int) -> int:
    """Merge *source* into *target*: every photo tagged source becomes
    tagged target (duplicates collapse), then source is deleted.

    Returns the number of photo links moved (post-dedup).
    """
    if source_id == target_id:
        return 0
    moved = conn.execute(
        "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) "
        "SELECT photo_id, ? FROM photo_tags WHERE tag_id = ?",
        (target_id, source_id),
    ).rowcount
    conn.execute("DELETE FROM photo_tags WHERE tag_id = ?", (source_id,))
    conn.execute("DELETE FROM tags WHERE id = ?", (source_id,))
    conn.commit()
    return moved


def search_tags(conn: sqlite3.Connection, query: str) -> list[dict[str, Any]]:
    """Search tags by name prefix (case-insensitive)."""
    query = query.strip().lower()
    rows = conn.execute(
        f"SELECT {_TAG_COLS} FROM tags WHERE name LIKE ? ORDER BY name",
        (f"{query}%",),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Photo-tag associations ──


def add_tag_to_photo(conn: sqlite3.Connection, photo_id: int, tag_id: int) -> None:
    """Add a tag to a photo (idempotent)."""
    conn.execute(
        "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)",
        (photo_id, tag_id),
    )
    conn.commit()


def remove_tag_from_photo(conn: sqlite3.Connection, photo_id: int, tag_id: int) -> None:
    """Remove a tag from a photo."""
    conn.execute(
        "DELETE FROM photo_tags WHERE photo_id = ? AND tag_id = ?",
        (photo_id, tag_id),
    )
    conn.commit()


def get_photo_tags(conn: sqlite3.Connection, photo_id: int) -> list[dict[str, Any]]:
    """Get all tags for a photo."""
    rows = conn.execute(
        "SELECT t.id, t.name, t.created_at FROM tags t "
        "JOIN photo_tags pt ON pt.tag_id = t.id "
        "WHERE pt.photo_id = ? ORDER BY t.name",
        (photo_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_photos_by_tag(conn: sqlite3.Connection, tag_id: int) -> list[dict[str, Any]]:
    """Get all non-deleted photos with a given tag."""
    rows = conn.execute(
        f"SELECT {PHOTO_COLS_SLIM_PREFIXED} FROM photos p "
        "JOIN photo_tags pt ON pt.photo_id = p.id "
        f"WHERE pt.tag_id = ? AND {active_photo_sql('p')} "
        "ORDER BY p.date",
        (tag_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def bulk_tag_photos(conn: sqlite3.Connection, photo_ids: list[int], tag_id: int) -> int:
    """Add a tag to multiple photos. Returns count added."""
    conn.executemany(
        "INSERT OR IGNORE INTO photo_tags (photo_id, tag_id) VALUES (?, ?)",
        [(pid, tag_id) for pid in photo_ids],
    )
    conn.commit()
    return len(photo_ids)


def bulk_untag_photos(conn: sqlite3.Connection, photo_ids: list[int], tag_id: int) -> int:
    """Remove a tag from multiple photos. Returns count removed."""
    from bpp.constants import SQL_BATCH_SIZE

    if not photo_ids:
        return 0
    for i in range(0, len(photo_ids), SQL_BATCH_SIZE):
        batch = photo_ids[i : i + SQL_BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        conn.execute(
            f"DELETE FROM photo_tags WHERE tag_id = ? AND photo_id IN ({placeholders})",
            [tag_id, *batch],
        )
    conn.commit()
    return len(photo_ids)
