"""Create / update / remove primitives for smart albums.

Extracted from :mod:`bpp.db.smart_album_refreshers` when the LOC gate
caught it crossing the 500-line cap (2026-06-12). These two helpers are
the write-side primitives every ``_refresh_*_album`` routine builds on;
the refresh routines themselves stay in smart_album_refreshers.

Both names are re-exported from smart_album_refreshers AND
smart_albums, so the historical import paths keep working.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from bpp.db.albums import add_photos_to_album, create_album


def _remove_smart_album_if_exists(conn: sqlite3.Connection, album_type: str, rule: dict) -> None:
    """Remove a smart album if it exists and has no matching photos."""
    import json

    rule_json = json.dumps(rule, sort_keys=True)
    row = conn.execute(
        "SELECT id FROM albums WHERE album_type=? AND rule_json=?",
        (album_type, rule_json),
    ).fetchone()
    if row:
        conn.execute("DELETE FROM album_photos WHERE album_id=?", (row[0],))
        conn.execute("DELETE FROM albums WHERE id=?", (row[0],))
        conn.commit()


def _ensure_smart_album(
    conn: sqlite3.Connection,
    name: str,
    album_type: str,
    rule: dict[str, Any],
    photo_query: tuple[str, tuple] | None = None,
    photo_ids: list[int] | None = None,
) -> int | None:
    """Create or update a smart album. Returns album ID, or None if dismissed."""
    import json

    rule_json = json.dumps(rule, sort_keys=True)

    # Skip if user dismissed this smart album
    dismissed = conn.execute(
        "SELECT 1 FROM dismissed_smart_albums WHERE album_type=? AND rule_json=?",
        (album_type, rule_json),
    ).fetchone()
    if dismissed:
        return None

    # Check if album already exists by type + rule
    row = conn.execute(
        "SELECT id FROM albums WHERE album_type=? AND rule_json=?",
        (album_type, rule_json),
    ).fetchone()

    if row:
        album_id = row[0]
        # Keep display name in sync with code — but not for user-renameable
        # types (registry flag, so plugin album types can opt in too).
        from bpp.db.smart_albums import SmartAlbumRegistry

        if not SmartAlbumRegistry.is_user_renameable(album_type):
            conn.execute("UPDATE albums SET name=? WHERE id=?", (name, album_id))
    else:
        album_id = create_album(conn, name, album_type=album_type, rule=rule)

    # Resolve photo IDs from query if needed
    if photo_ids is None and photo_query:
        query, params = photo_query
        rows = conn.execute(query, params).fetchall()
        photo_ids = [r[0] for r in rows]

    if photo_ids:
        # For existing smart albums, clear stale entries first so the
        # album_photos table stays in sync with the current source data
        # (e.g. after reclustering, photos may have moved to other clusters).
        if row:
            conn.execute("DELETE FROM album_photos WHERE album_id=?", (album_id,))
        add_photos_to_album(conn, album_id, photo_ids)

    return album_id
