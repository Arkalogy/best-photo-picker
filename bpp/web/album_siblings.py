"""The Duplicates album's sibling lists: for each photo on a page, the
other photos of its near-duplicate cluster (or, before clustering has
run, of its exact perceptual hash)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from bpp.constants import ACTIVE_PHOTO_SQL

_COLUMNS = (
    "filepath, {key}, aggregate_score, blur_score, exposure_score, face_score,"
    " composition_score, date_day, original_filename"
)


def attach_cluster_siblings(
    conn: Any,
    thumbs: Any,
    photos: list[dict[str, Any]],
    raw_data: list[dict[str, Any]],
) -> None:
    """Set ``similar_photos`` on each entry of ``photos`` (built from the
    matching ``raw_data`` row) that has cluster siblings."""
    from bpp.web.photo_dict import build_similar_entry

    use_clusters = (
        conn.execute("SELECT 1 FROM photos WHERE dup_cluster_id > 0 LIMIT 1").fetchone() is not None
    )
    group_key = "dup_cluster_id" if use_clusters else "phash"
    keys = {raw.get(group_key) for raw in raw_data if raw.get(group_key)}
    if not keys:
        return
    placeholders = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"SELECT {_COLUMNS.format(key=group_key)} FROM photos"
        f" WHERE {group_key} IN ({placeholders}) AND {ACTIVE_PHOTO_SQL}",
        list(keys),
    ).fetchall()
    groups: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row[1]].append(
            {
                "filepath": row[0],
                "aggregate_score": row[2],
                "blur_score": row[3],
                "exposure_score": row[4],
                "face_score": row[5],
                "composition_score": row[6],
                "date_day": row[7],
                "original_filename": row[8],
            }
        )

    for i, raw in enumerate(raw_data):
        key = raw.get(group_key)
        if not key or key not in groups:
            continue
        my_fp = raw.get("filepath", "")
        # A hamming-distance cluster is not a scored match: no similarity.
        siblings = [
            build_similar_entry(item, thumbs, similarity=None)
            for item in groups[key]
            if item["filepath"] != my_fp
        ]
        if siblings:
            photos[i]["similar_photos"] = siblings
