"""Groups blueprint: detected groups of people who appear together.

Extracted from bp_faces_manage.py during the v0.1 cleanup — the
groups endpoint was a separate concern (co-occurrence detection over
person clusters) that didn't share state with the face-mutation
endpoints beyond the standard ctx + DB helpers, so isolating it
shrinks bp_faces_manage and makes future "groups" work touch one
file instead of editing a 1300-line module.
"""

from __future__ import annotations

import json

from flask import Blueprint, Response, jsonify, request

from bpp.constants import ACTIVE_PHOTO_SQL
from bpp.utils.logging import get_logger
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("groups", __name__)

_ACTIVE = ACTIVE_PHOTO_SQL


@bp.get("/api/v1/groups")
def api_groups() -> tuple[Response, int]:
    """Return detected groups of people who appear together."""
    ctx = get_ctx()
    conn = ctx.get_conn()

    from bpp.db.groups import detect_groups, group_min_photos

    # Explicit query param wins; otherwise the user's Settings value.
    min_photos = request.args.get("min_photos", type=int) or group_min_photos(conn)
    groups = detect_groups(conn, min_photos=min_photos)

    # Pre-load person album names: cluster_id -> name.
    # P5b: indexed lookup via the v36 shadow column.
    from bpp.db.albums import get_smart_person_cluster_name_map

    person_name_map = get_smart_person_cluster_name_map(conn)

    # Batch-fetch all face embeddings for all group members (1 query instead of N)
    all_cids = list({cid for g in groups for cid in g["members"]})
    cluster_faces_map: dict[int, list] = {cid: [] for cid in all_cids}
    if all_cids:
        from bpp.constants import SQL_BATCH_SIZE

        for i in range(0, len(all_cids), SQL_BATCH_SIZE):
            batch = all_cids[i : i + SQL_BATCH_SIZE]
            ph = ",".join("?" * len(batch))
            rows = conn.execute(
                "SELECT fe.cluster_id, fe.face_index, p.filepath, fe.embedding, fe.quality "
                "FROM face_embeddings fe "
                "JOIN photos p ON p.id = fe.photo_id "
                f"WHERE fe.cluster_id IN ({ph}) AND p.{_ACTIVE}",
                batch,
            ).fetchall()
            for r in rows:
                cluster_faces_map[r[0]].append(r)

    # Batch-fetch all group albums (1 query instead of N)
    group_album_map: dict[str, tuple[int, str]] = {}
    album_rows = conn.execute(
        "SELECT rule_json, id, name FROM albums WHERE album_type='smart_group'"
    ).fetchall()
    for ar in album_rows:
        group_album_map[ar[0]] = (ar[1], ar[2])

    # Enrich with member info (names, representative crops)
    for group in groups:
        member_info = []
        for cid in group["members"]:
            name = person_name_map.get(cid, f"Person {cid + 1}")

            # Get representative face for avatar (closest to centroid)
            thumb_hash = ""
            face_index = 0
            reps = cluster_faces_map.get(cid, [])
            if reps:
                # Best-QUALITY crop for the avatar, not the centroid-closest
                # one: the centroid pick surfaced junk crops (hands / blurry
                # profiles / food misdetected as faces). fe.quality is r[4].
                rep = max(reps, key=lambda r: r[4] if r[4] is not None else 0.0)
                face_index = rep[1]
                if ctx.thumbs:
                    thumb_hash = ctx.thumbs.get_hash(rep[2]) or ""

            member_info.append(
                {
                    "cluster_id": cid,
                    "name": name,
                    "thumb_hash": thumb_hash,
                    "face_index": face_index,
                }
            )
        group["member_info"] = member_info

        # Get group album ID if it exists
        rule_json = json.dumps({"group_members": group["members"]}, sort_keys=True)
        album_match = group_album_map.get(rule_json)
        group["album_id"] = album_match[0] if album_match else None
        group["album_name"] = album_match[1] if album_match else None

    return jsonify({"groups": groups}), 200
