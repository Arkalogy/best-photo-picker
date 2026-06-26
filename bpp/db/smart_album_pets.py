"""Pet smart-album refresh.

Owns the pet-cluster → album sync, the legacy migration from
class-only rules (``pet_class: "cats"``) to cluster-based rules
(``cluster_id: 7, pet_class: "cat"``), and stale-album cleanup.

Re-exported from smart_albums.py so the registry registration
(``"smart_pet" → _refresh_pet_albums``) keeps resolving.
"""

from __future__ import annotations

import json
import sqlite3

from bpp.constants import ACTIVE_PHOTO_SQL, PET_DISPLAY_CONFIDENCE
from bpp.utils.json_utils import safe_json_loads
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_ACTIVE = ACTIVE_PHOTO_SQL


def _pet_default_name(pet_class: str, cluster_id: int) -> str:
    """Generate default display name for a pet cluster."""
    singular = pet_class.rstrip("s")
    return singular.capitalize() + "s"


def _refresh_pet_albums(conn: sqlite3.Connection) -> None:
    """Create smart albums per pet cluster (species or manual split)."""
    from bpp.db.pets import get_pet_clusters
    from bpp.db.smart_albums import _ensure_smart_album

    # Migrate legacy class-only rules to cluster-based rules
    for old_class in ("cats", "cat", "dogs", "dog"):
        old_rule = json.dumps({"pet_class": old_class}, sort_keys=True)
        row = conn.execute(
            "SELECT id FROM albums WHERE album_type='smart_pet' AND rule_json=?",
            (old_rule,),
        ).fetchone()
        if row:
            singular = old_class.rstrip("s")
            cid_row = conn.execute(
                "SELECT DISTINCT cluster_id FROM pet_detections WHERE class=?",
                (singular,),
            ).fetchone()
            if cid_row:
                new_rule = json.dumps(
                    {"cluster_id": cid_row[0], "pet_class": singular},
                    sort_keys=True,
                )
                conn.execute(
                    "UPDATE albums SET rule_json=? WHERE id=?",
                    (new_rule, row[0]),
                )
            else:
                # No detections for this class — remove stale album
                conn.execute("DELETE FROM album_photos WHERE album_id=?", (row[0],))
                conn.execute("DELETE FROM albums WHERE id=?", (row[0],))

    # Get active clusters
    clusters = get_pet_clusters(conn)

    cluster_pet_class: dict[int, str] = {}
    for c in clusters:
        cluster_pet_class[c["cluster_id"]] = c["pet_class"]
    active_cids = set(cluster_pet_class.keys())

    # Single query: all photo IDs grouped by cluster
    cluster_photos: dict[int, list[int]] = {}
    if active_cids:
        ph = ",".join("?" * len(active_cids))
        for r in conn.execute(
            "SELECT pd.cluster_id, p.id FROM photos p "
            "JOIN pet_detections pd ON pd.photo_id = p.id "
            f"WHERE pd.cluster_id IN ({ph}) AND p.{_ACTIVE} "
            f"AND pd.confidence >= {PET_DISPLAY_CONFIDENCE}",
            list(active_cids),
        ).fetchall():
            cluster_photos.setdefault(r[0], []).append(r[1])

    for cid, pet_class in cluster_pet_class.items():
        photo_ids = cluster_photos.get(cid, [])
        if photo_ids:
            _ensure_smart_album(
                conn,
                name=_pet_default_name(pet_class, cid),
                album_type="smart_pet",
                rule={"cluster_id": cid, "pet_class": pet_class},
                photo_ids=photo_ids,
            )

    # Remove albums for clusters that no longer exist — batch deletes to
    # avoid N+1 queries when there are many stale pet clusters.
    existing = conn.execute(
        "SELECT id, rule_json FROM albums WHERE album_type='smart_pet'"
    ).fetchall()
    orphan_ids = [
        album_id
        for album_id, rule_json in existing
        if safe_json_loads(rule_json, {}, context="smart_pet album rule").get("cluster_id")
        not in active_cids
    ]
    if orphan_ids:
        ph = ",".join(["?"] * len(orphan_ids))
        conn.execute(f"DELETE FROM album_photos WHERE album_id IN ({ph})", orphan_ids)
        conn.execute(f"DELETE FROM albums WHERE id IN ({ph})", orphan_ids)

    conn.commit()
