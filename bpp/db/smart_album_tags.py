"""Tag smart-album refresh.

Owns the tag → album sync — one ``smart_tag`` album per active tag
with at least one photo. Stale albums (deleted tags or tags with
zero photos) are removed.

Re-exported from smart_albums.py so the registry registration
(``"smart_tag" → _refresh_tag_albums``) keeps resolving.
"""

from __future__ import annotations

import sqlite3

from bpp.constants import ACTIVE_PHOTO_SQL
from bpp.utils.json_utils import safe_json_loads
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_ACTIVE = ACTIVE_PHOTO_SQL


def _refresh_tag_albums(conn: sqlite3.Connection) -> None:
    """Create smart albums per tag."""
    from bpp.db.smart_albums import _ensure_smart_album
    from bpp.db.tags import list_tags_with_counts

    tags = list_tags_with_counts(conn)
    active_tag_ids: set[int] = {t["id"] for t in tags}
    tag_ids_with_photos = [t["id"] for t in tags if t["count"] > 0]

    # Single grouped query instead of N per-tag SELECTs. On a library
    # with M tags the planner reads photo_tags once and groups in C.
    tag_photos: dict[int, list[int]] = {}
    if tag_ids_with_photos:
        placeholders = ",".join("?" * len(tag_ids_with_photos))
        rows = conn.execute(
            f"SELECT pt.tag_id, p.id FROM photos p "
            f"JOIN photo_tags pt ON pt.photo_id = p.id "
            f"WHERE pt.tag_id IN ({placeholders}) AND p.{_ACTIVE}",
            tag_ids_with_photos,
        ).fetchall()
        for tid, pid in rows:
            tag_photos.setdefault(tid, []).append(pid)
        # Dedup while preserving the DISTINCT semantic of the original query.
        tag_photos = {tid: list(dict.fromkeys(ids)) for tid, ids in tag_photos.items()}

    for tag in tags:
        tid = tag["id"]
        photo_ids = tag_photos.get(tid, [])
        if photo_ids:
            _ensure_smart_album(
                conn,
                name=tag["name"],
                album_type="smart_tag",
                rule={"tag_id": tid},
                photo_ids=photo_ids,
            )

    # Remove albums for deleted tags or tags with no photos.
    # Batch the two DELETEs so a library with hundreds of stale tag
    # albums collapses 2N statements into 2.
    existing = conn.execute(
        "SELECT id, rule_json FROM albums WHERE album_type='smart_tag'"
    ).fetchall()
    stale_album_ids: list[int] = []
    for album_id, rule_json in existing:
        rule = safe_json_loads(rule_json, {}, context="smart_tag album rule")
        tid = rule.get("tag_id")
        if tid is not None and (tid not in active_tag_ids or not tag_photos.get(tid)):
            stale_album_ids.append(album_id)
    if stale_album_ids:
        ph = ",".join("?" * len(stale_album_ids))
        conn.execute(f"DELETE FROM album_photos WHERE album_id IN ({ph})", stale_album_ids)
        conn.execute(f"DELETE FROM albums WHERE id IN ({ph})", stale_album_ids)

    conn.commit()
