"""Group smart-album refresh.

Owns the group-detection → album sync. Groups are sets of
cluster_ids that appear together in >= min_photos photos; each
set becomes a smart_group album named after its members
("Alex & Sam").

Re-exported from smart_albums.py so the registry registration
(``"smart_group" → _refresh_group_albums``) keeps resolving.

User-set group names are preserved across refreshes — only photo
membership and stale-album cleanup mutate during a refresh.
"""

from __future__ import annotations

import json
import re
import sqlite3

from bpp.db.albums import add_photos_to_album
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_AUTO_NAME_TOKEN = re.compile(r"^Person \d+$")


def is_stale_auto_group_name(name: str, member_names: list[str]) -> bool:
    """True when a group album's name is auto-generated text gone stale.

    Auto names are member names joined with " & ". After a member is
    renamed (or merged), the stored name no longer matches the current
    default, which the UI then mistakes for a user-given name. Heuristic:
    if EVERY " & "-token is either "Person N" or one of the current
    member names, the name carries no user input — safe to regenerate.
    """
    tokens = name.split(" & ")
    if len(tokens) < 2:
        return False  # single token can't be the auto join — assume user text
    return all(_AUTO_NAME_TOKEN.match(t) or t in member_names for t in tokens)


def _refresh_group_albums(conn: sqlite3.Connection) -> None:
    """Create smart albums for groups of people who appear together."""
    from bpp.db.groups import detect_groups, get_group_photo_ids, group_min_photos
    from bpp.db.smart_albums import _ensure_smart_album

    groups = detect_groups(conn, min_photos=group_min_photos(conn))
    active_rules: set[str] = set()

    # Pre-load all person album names for O(1) lookup.
    # P5b: indexed shadow-column lookup via the canonical helper.
    from bpp.db.albums import get_smart_person_cluster_name_map

    _person_names = get_smart_person_cluster_name_map(conn)

    for group in groups:
        members = group["members"]
        rule = {"group_members": members}
        rule_json = json.dumps(rule, sort_keys=True)
        active_rules.add(rule_json)

        # Default name from pre-loaded person album names
        names = [_person_names.get(cid, f"Person {cid + 1}") for cid in members]
        default_name = " & ".join(names)

        photo_ids = get_group_photo_ids(conn, members)
        if not photo_ids:
            continue

        # Skip if user dismissed this group album
        dismissed = conn.execute(
            "SELECT 1 FROM dismissed_smart_albums WHERE album_type='smart_group' AND rule_json=?",
            (rule_json,),
        ).fetchone()
        if dismissed:
            continue

        # Album exists → preserve user-set name, just sync photos
        existing = conn.execute(
            "SELECT id, name FROM albums WHERE album_type='smart_group' AND rule_json=?",
            (rule_json,),
        ).fetchone()

        if existing:
            album_id = existing[0]
            # Self-heal stale auto names ("Person 2 & Person 5" after the
            # members were renamed). User-given names are left alone.
            if existing[1] != default_name and is_stale_auto_group_name(existing[1], names):
                conn.execute("UPDATE albums SET name=? WHERE id=?", (default_name, album_id))
                log.info(
                    "Group album %d auto-name refreshed: %r -> %r",
                    album_id,
                    existing[1],
                    default_name,
                )
            add_photos_to_album(conn, album_id, photo_ids)
        else:
            _ensure_smart_album(
                conn,
                name=default_name,
                album_type="smart_group",
                rule=rule,
                photo_ids=photo_ids,
            )

    # Clean up stale group albums (batch delete)
    existing = conn.execute(
        "SELECT id, rule_json FROM albums WHERE album_type='smart_group'"
    ).fetchall()
    stale_ids = [album_id for album_id, rule_json in existing if rule_json not in active_rules]
    if stale_ids:
        ph = ",".join("?" * len(stale_ids))
        conn.execute(f"DELETE FROM album_photos WHERE album_id IN ({ph})", stale_ids)
        conn.execute(f"DELETE FROM albums WHERE id IN ({ph})", stale_ids)
    conn.commit()
