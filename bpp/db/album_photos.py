"""Photo membership edits on one album: add, remove, selection,
overrides and favorites. Split out of :mod:`bpp.db.albums` for the
500-LOC cap; ``albums`` re-exports these names."""

from __future__ import annotations

import sqlite3

from bpp.db.sql_batches import in_batches


def add_photos_to_album(conn: sqlite3.Connection, album_id: int, photo_ids: list[int]) -> int:
    """Add photos to an album. Returns count added."""
    conn.executemany(
        "INSERT OR IGNORE INTO album_photos (album_id, photo_id) VALUES (?, ?)",
        ((album_id, pid) for pid in photo_ids),
    )
    conn.commit()
    return len(photo_ids)


def remove_photos_from_album(conn: sqlite3.Connection, album_id: int, photo_ids: list[int]) -> None:
    """Remove photos from an album. No-op on empty list."""
    if not photo_ids:
        return
    try:
        for placeholders, batch in in_batches(list(dict.fromkeys(photo_ids))):
            conn.execute(
                f"DELETE FROM album_photos WHERE album_id=? AND photo_id IN ({placeholders})",
                [album_id, *batch],
            )
    except BaseException:
        conn.rollback()  # all batches or none
        raise
    conn.commit()


def set_album_selection(
    conn: sqlite3.Connection, album_id: int, selected_photo_ids: set[int]
) -> None:
    """Bulk update which photos are algorithmically selected in an album.

    collapsed into a single UPDATE … CASE so two concurrent
    callers (e.g. recompute fired from two browser tabs against the
    same album) can't interleave between the "clear all" and "set
    selected" statements. The previous two-statement sequence
    let tab B's clear+set run in between tab A's clear and set,
    so tab A's set then re-applied stale ids on top of B's commit
    and tab B's selection silently disappeared. SQLite's per-write
    transaction makes the single-UPDATE form atomic against any
    concurrent reader.
    """
    if selected_photo_ids:
        placeholders = ", ".join(["?"] * len(selected_photo_ids))
        conn.execute(
            f"UPDATE album_photos SET selected = CASE "
            f"WHEN photo_id IN ({placeholders}) THEN 1 ELSE 0 END "
            "WHERE album_id=?",
            [*selected_photo_ids, album_id],
        )
    else:
        conn.execute("UPDATE album_photos SET selected=0 WHERE album_id=?", (album_id,))
    conn.commit()


def set_override(
    conn: sqlite3.Connection, album_id: int, photo_id: int, override: str | None
) -> None:
    """Set override for a photo in an album. override is 'include', 'exclude', or None."""
    conn.execute(
        "UPDATE album_photos SET override=? WHERE album_id=? AND photo_id=?",
        (override, album_id, photo_id),
    )
    conn.commit()


def set_overrides_bulk(
    conn: sqlite3.Connection,
    album_id: int,
    photo_ids: list[int],
    override: str | None,
) -> int:
    """Set override for multiple photos in an album. Returns count updated."""
    if not photo_ids:
        return 0
    count = 0
    try:
        for placeholders, batch in in_batches(list(dict.fromkeys(photo_ids))):
            count += conn.execute(
                f"UPDATE album_photos SET override=? "
                f"WHERE album_id=? AND photo_id IN ({placeholders})",
                [override, album_id, *batch],
            ).rowcount
    except BaseException:
        conn.rollback()  # all batches or none
        raise
    conn.commit()
    return count


def set_favorites_bulk(
    conn: sqlite3.Connection,
    album_id: int,
    photo_ids: list[int],
    favorite: bool,
) -> int:
    """Set favorite state for multiple photos in an album. Returns count updated."""
    if not photo_ids:
        return 0
    count = 0
    try:
        for placeholders, batch in in_batches(list(dict.fromkeys(photo_ids))):
            count += conn.execute(
                f"UPDATE album_photos SET favorite=? "
                f"WHERE album_id=? AND photo_id IN ({placeholders})",
                [1 if favorite else 0, album_id, *batch],
            ).rowcount
    except BaseException:
        conn.rollback()  # all batches or none
        raise
    conn.commit()
    return count


def toggle_favorite(conn: sqlite3.Connection, album_id: int, photo_id: int) -> bool:
    """Toggle favorite status. Returns new favorite state."""
    row = conn.execute(
        "SELECT favorite FROM album_photos WHERE album_id=? AND photo_id=?",
        (album_id, photo_id),
    ).fetchone()
    if row is None:
        return False
    new_val = 0 if row[0] else 1
    conn.execute(
        "UPDATE album_photos SET favorite=? WHERE album_id=? AND photo_id=?",
        (new_val, album_id, photo_id),
    )
    conn.commit()
    return bool(new_val)
