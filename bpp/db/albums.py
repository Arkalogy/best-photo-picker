"""CRUD operations for albums and album_photos."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from bpp.constants import ACTIVE_PHOTO_SQL, active_photo_sql, visible_photo_conditions
from bpp.db.photos import PHOTO_COLS_PREFIXED, PHOTO_COLS_SLIM_PREFIXED
from bpp.utils.json_utils import safe_json_loads
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_ALBUM_COLS = "id, name, album_type, rule_json, config_json, k, parent_id, created_at, modified_at"
_ALBUM_COLS_PREFIXED = ", ".join(f"a.{c.strip()}" for c in _ALBUM_COLS.split(","))


def create_album(
    conn: sqlite3.Connection,
    name: str,
    config: dict[str, Any] | None = None,
    album_type: str = "manual",
    rule: dict[str, Any] | None = None,
    k: int = 50,
    parent_id: int | None = None,
) -> int:
    """Create a new album and return its id.

    P5/v36: when ``album_type == 'smart_person'`` and ``rule['cluster_id']``
    is an int, the shadow column ``smart_person_cluster_id`` is populated
    in the same INSERT. Readers that previously joined via
    ``json_extract(rule_json, '$.cluster_id')`` can now probe the indexed
    column directly — see :func:`bpp.db.smart_album_lookup.find_person_album_by_cluster`.
    """
    smart_person_cluster_id = _extract_smart_person_cluster_id(album_type, rule)
    cur = conn.execute(
        "INSERT INTO albums (name, album_type, rule_json, config_json, k, parent_id, "
        "smart_person_cluster_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            name,
            album_type,
            json.dumps(rule) if rule else None,
            json.dumps(config) if config else None,
            k,
            parent_id,
            smart_person_cluster_id,
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_smart_person_cluster_name_map(
    conn: sqlite3.Connection,
) -> dict[int, str]:
    """Return ``{cluster_id: album_name}`` for every smart_person album.

    P5b — the canonical replacement for the ~8 "load all smart_person
    rows then parse rule_json" sites the audit flagged. Uses the v36
    ``smart_person_cluster_id`` shadow column directly:

    * No JSON parse per row (the prior pattern called ``safe_json_loads``
      on every row).
    * Indexed lookup via ``idx_albums_smart_person_cluster`` —
      O(log N) instead of O(N) on a 50k-photo library with many
      person clusters.

    Rows where ``smart_person_cluster_id`` is NULL (legacy / malformed
    rule_json that the v36 backfill couldn't parse) are skipped. The
    map is built once and returned; callers do O(1) name lookups.

    Positional row access (``r[0]`` / ``r[1]``) so callers without
    ``row_factory=sqlite3.Row`` set on the conn (most production paths
    use the connection pool's default, but test fixtures often skip
    it) still work.
    """
    rows = conn.execute(
        "SELECT smart_person_cluster_id, name FROM albums "
        "WHERE album_type = 'smart_person' "
        "AND smart_person_cluster_id IS NOT NULL"
    ).fetchall()
    return {int(r[0]): r[1] for r in rows}


def find_smart_person_album_by_cluster(
    conn: sqlite3.Connection, cluster_id: int
) -> dict[str, Any] | None:
    """Return ``{id, name}`` for the smart_person album bound to
    *cluster_id*, or ``None`` when no such album exists.

    Single-row variant of :func:`get_smart_person_cluster_name_map`
    for endpoints that only need one lookup. Same indexed path; same
    NULL-skip semantics.
    """
    row = conn.execute(
        "SELECT id, name FROM albums "
        "WHERE album_type = 'smart_person' "
        "AND smart_person_cluster_id = ?",
        (cluster_id,),
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1]}


def _extract_smart_person_cluster_id(album_type: str, rule: dict[str, Any] | None) -> int | None:
    """Pull ``cluster_id`` from a smart_person album's rule dict.

    Returns ``None`` for non-smart_person rows or when the rule is
    missing / malformed. Used by writers (``create_album`` +
    ``smart_album_people._refresh_person_albums``) to populate the
    v36 shadow column atomically with rule_json. Centralized so the
    "smart_person rule shape" is one place to update.
    """
    if album_type != "smart_person" or not isinstance(rule, dict):
        return None
    cid = rule.get("cluster_id")
    return cid if isinstance(cid, int) else None


def list_albums(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all albums with photo + selection counts.

    Counts exclude deleted/hidden photos. The returned dicts include
    parsed `config` and `rule` (decoded from their *_json columns) for
    convenience — callers don't need to reparse.
    """
    rows = conn.execute(
        f"SELECT {_ALBUM_COLS_PREFIXED}, "
        "COALESCE(cnt.photo_count, 0) AS photo_count, "
        "COALESCE(cnt.selected_count, 0) AS selected_count "
        "FROM albums a "
        "LEFT JOIN ("
        "  SELECT ap.album_id, COUNT(*) AS photo_count, "
        "  SUM(CASE WHEN ap.selected = 1 THEN 1 ELSE 0 END) AS selected_count "
        "  FROM album_photos ap "
        "  JOIN photos p ON p.id=ap.photo_id "
        f"  WHERE {active_photo_sql('p')} "
        "  GROUP BY ap.album_id"
        ") cnt ON cnt.album_id=a.id "
        "ORDER BY a.album_type, a.name"
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["config"] = safe_json_loads(d["config_json"], context="album config")
        d["rule"] = safe_json_loads(d["rule_json"], context="album rule")
        del d["config_json"]
        del d["rule_json"]
        result.append(d)
    return result


def get_album(conn: sqlite3.Connection, album_id: int) -> dict[str, Any] | None:
    """Return one album by id, or None if it doesn't exist.

    Photo count excludes deleted/hidden photos. Same `config` / `rule`
    parsing as `list_albums`.
    """
    row = conn.execute(
        f"SELECT {_ALBUM_COLS_PREFIXED}, "
        "(SELECT COUNT(*) FROM album_photos ap "
        "JOIN photos p ON p.id=ap.photo_id "
        f"WHERE ap.album_id=a.id AND {active_photo_sql('p')}) AS photo_count, "
        "(SELECT COALESCE(SUM(ap.selected), 0) FROM album_photos ap "
        "JOIN photos p ON p.id=ap.photo_id "
        f"WHERE ap.album_id=a.id AND {active_photo_sql('p')}) AS selected_count "
        "FROM albums a WHERE a.id=?",
        (album_id,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["config"] = safe_json_loads(d["config_json"], context="album config")
    d["rule"] = safe_json_loads(d["rule_json"], context="album rule")
    del d["config_json"]
    del d["rule_json"]
    return d


def update_album(conn: sqlite3.Connection, album_id: int, **kwargs: Any) -> None:
    """Update album fields. Accepts name, config, k, rule, parent_id."""
    updates = {}
    if "name" in kwargs:
        updates["name"] = kwargs["name"]
    if "config" in kwargs:
        updates["config_json"] = json.dumps(kwargs["config"]) if kwargs["config"] else None
    if "k" in kwargs:
        updates["k"] = kwargs["k"]
    if "rule" in kwargs:
        updates["rule_json"] = json.dumps(kwargs["rule"]) if kwargs["rule"] else None
    if "parent_id" in kwargs:
        pid = kwargs["parent_id"]
        # Prevent self-parenting
        updates["parent_id"] = None if pid == album_id else pid
    if not updates:
        return
    updates["modified_at"] = "datetime('now')"
    set_parts = []
    values = []
    for k, v in updates.items():
        if k == "modified_at":
            set_parts.append(f"{k}=datetime('now')")
        else:
            set_parts.append(f"{k}=?")
            values.append(v)
    values.append(album_id)
    conn.execute(f"UPDATE albums SET {', '.join(set_parts)} WHERE id=?", values)
    conn.commit()


def delete_album(conn: sqlite3.Connection, album_id: int) -> None:
    """Delete an album and detach its children.

    Sub-albums get re-parented to top level (parent_id=NULL) rather
    than cascade-deleted — losing a parent shouldn't lose its
    children's contents. The album_photos rows for this album cascade
    via foreign key.
    """
    # Move children to top level before deleting
    conn.execute("UPDATE albums SET parent_id=NULL WHERE parent_id=?", (album_id,))
    conn.execute("DELETE FROM albums WHERE id=?", (album_id,))
    conn.commit()
    log.info("Deleted album %d", album_id)


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
    placeholders = ", ".join(["?"] * len(photo_ids))
    conn.execute(
        f"DELETE FROM album_photos WHERE album_id=? AND photo_id IN ({placeholders})",
        [album_id, *photo_ids],
    )
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
    placeholders = ", ".join(["?"] * len(photo_ids))
    cur = conn.execute(
        f"UPDATE album_photos SET override=? WHERE album_id=? AND photo_id IN ({placeholders})",
        [override, album_id, *photo_ids],
    )
    conn.commit()
    return cur.rowcount


def set_favorites_bulk(
    conn: sqlite3.Connection,
    album_id: int,
    photo_ids: list[int],
    favorite: bool,
) -> int:
    """Set favorite state for multiple photos in an album. Returns count updated."""
    if not photo_ids:
        return 0
    placeholders = ", ".join(["?"] * len(photo_ids))
    cur = conn.execute(
        f"UPDATE album_photos SET favorite=? WHERE album_id=? AND photo_id IN ({placeholders})",
        [1 if favorite else 0, album_id, *photo_ids],
    )
    conn.commit()
    return cur.rowcount


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


# Columns needed by build_photo_dict (everything except exif_json).
# Imported from photos.py as PHOTO_COLS_SLIM_PREFIXED.


def get_album_photos(
    conn: sqlite3.Connection,
    album_id: int,
    include_missing: bool = False,
    include_deleted: bool = False,
    include_hidden: bool = False,
    limit: int | None = None,
    offset: int = 0,
    slim: bool = False,
) -> list[dict[str, Any]]:
    """Get photos in an album with their selection/override/favorite state.

    When *slim* is True, exif_json is excluded from the result to reduce
    payload size for background page loads.
    """
    # Conditions come from the shared builder (constants.py) — the single
    # place the visible-photo rule is assembled, sidecar exclusion included.
    cond_sql = " AND ".join(
        visible_photo_conditions(
            alias="p",
            include_missing=include_missing,
            include_deleted=include_deleted,
            include_hidden=include_hidden,
        )
    )
    select = PHOTO_COLS_SLIM_PREFIXED if slim else PHOTO_COLS_PREFIXED
    pagination = ""
    params: list[Any] = [album_id]
    if limit is not None:
        pagination = " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    rows = conn.execute(
        f"""
        SELECT {select}, ap.selected, ap.override, ap.favorite
        FROM photos p
        JOIN album_photos ap ON p.id = ap.photo_id
        WHERE ap.album_id=? AND {cond_sql}
        ORDER BY p.date, p.id{pagination}
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_album_overrides_and_favorites(
    conn: sqlite3.Connection,
    album_id: int,
) -> tuple[dict[str, str], list[str]]:
    """Get overrides and favorites for an album without fetching all photo data.

    Returns (overrides_dict, favorites_list) where overrides maps filepath to mode.
    """
    rows = conn.execute(
        "SELECT p.filepath, ap.override, ap.favorite FROM album_photos ap "
        "JOIN photos p ON p.id = ap.photo_id "
        "WHERE ap.album_id=? AND (ap.override IS NOT NULL OR ap.favorite = 1) "
        f"AND {active_photo_sql('p')}",
        (album_id,),
    ).fetchall()
    overrides = {r[0]: r[1] for r in rows if r[1] is not None}
    favorites = [r[0] for r in rows if r[2]]

    return overrides, favorites


def ensure_all_photos_album(conn: sqlite3.Connection) -> int:
    """Ensure the 'All Photos' album exists, create if not. Returns its id."""
    row = conn.execute("SELECT id FROM albums WHERE album_type='all' LIMIT 1").fetchone()
    if row:
        return row[0]
    return create_album(conn, "All Photos", album_type="all")


def sync_all_photos_album(conn: sqlite3.Connection) -> None:
    """Ensure every non-missing, non-deleted, non-sidecar photo is in the 'All Photos' album.

    Also removes photos that no longer satisfy ACTIVE_PHOTO_SQL (e.g. photos that
    were marked as Live Photo sidecars after the album was last synced).
    """
    album_id = ensure_all_photos_album(conn)
    # Remove photos that are no longer active (deleted, missing, hidden, or sidecar)
    conn.execute(
        f"""
        DELETE FROM album_photos
        WHERE album_id = ?
          AND photo_id IN (
              SELECT id FROM photos WHERE NOT ({ACTIVE_PHOTO_SQL})
          )
        """,
        (album_id,),
    )
    # Add any active photos not yet in the album
    conn.execute(
        f"""
        INSERT OR IGNORE INTO album_photos (album_id, photo_id)
        SELECT ?, id FROM photos WHERE {ACTIVE_PHOTO_SQL}
        """,
        (album_id,),
    )
    conn.commit()
