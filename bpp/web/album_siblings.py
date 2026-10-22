"""The Duplicates album's sibling lists: for each photo on a page, the
other photos of its near-duplicate cluster (or, before clustering has
run, of its exact perceptual hash)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from bpp.constants import ACTIVE_PHOTO_SQL

#: Cluster members attached to one page row (the compare strip shows a
#: handful; the rest are reachable through the cluster id).
SIBLINGS_PER_ROW = 24

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
        f" WHERE {group_key} IN ({placeholders}) AND {ACTIVE_PHOTO_SQL}"
        " ORDER BY aggregate_score DESC, filepath",
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
        # A page row carries at most SIBLINGS_PER_ROW of its cluster (a
        # 5,000-frame cluster would otherwise repeat itself per row, a
        # gigabyte of JSON per page); ``cluster_size`` carries the count.
        members = [item for item in groups[key] if item["filepath"] != my_fp]
        siblings = [
            build_similar_entry(item, thumbs, similarity=None)
            for item in members[:SIBLINGS_PER_ROW]
        ]
        if siblings:
            photos[i]["similar_photos"] = siblings
            photos[i]["cluster_size"] = len(members) + 1


def _smart_duplicates_ui_metadata(conn: Any, ctx: Any, photos: Any, photos_data: Any) -> None:
    """The Duplicates album's response hook: attach each photo's cluster
    siblings. Registered here (the web layer shapes API responses)."""
    attach_cluster_siblings(conn, ctx.thumbs, photos, photos_data)


from bpp.db.smart_albums import SmartAlbumRegistry  # noqa: E402

SmartAlbumRegistry.set_ui_metadata_fn("smart_duplicates", _smart_duplicates_ui_metadata)
