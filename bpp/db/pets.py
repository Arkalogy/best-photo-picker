"""CRUD operations for pet detections."""

from __future__ import annotations

import sqlite3
from typing import Any

from bpp.constants import (
    CLUSTER_DISMISSED,
    CLUSTER_UNASSIGNED,
    PET_DISPLAY_CONFIDENCE,
    active_photo_sql,
)
from bpp.utils.retry import retry_io


def _iou(a: dict, b: dict) -> float:
    """Compute IoU between two bbox dicts with keys bbox_x/y/w/h."""
    ax1 = a.get("bbox_x")
    ay1 = a.get("bbox_y")
    aw = a.get("bbox_w")
    ah = a.get("bbox_h")
    bx1 = b.get("bbox_x")
    by1 = b.get("bbox_y")
    bw = b.get("bbox_w")
    bh = b.get("bbox_h")
    if any(v is None for v in (ax1, ay1, aw, ah, bx1, by1, bw, bh)):
        return 0.0
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    union = a["bbox_w"] * a["bbox_h"] + b["bbox_w"] * b["bbox_h"] - inter
    return inter / union if union > 0 else 0.0


def _match_cluster_by_iou(new_det: dict, old_dets: list[dict], min_iou: float = 0.3) -> int:
    """Find the best IoU match among old detections of the same class.

    Returns the cluster_id of the best match, or CLUSTER_UNASSIGNED.
    """
    best_iou, best_cid = 0.0, CLUSTER_UNASSIGNED
    for old in old_dets:
        if old["class"] != new_det["class"]:
            continue
        score = _iou(new_det, old)
        if score > best_iou:
            best_iou = score
            best_cid = old["cluster_id"]
    return best_cid if best_iou >= min_iou else CLUSTER_UNASSIGNED


_PET_COLS = (
    "id, photo_id, detection_index, class, confidence, bbox_x, bbox_y, bbox_w, bbox_h, cluster_id"
)


def upsert_pet_detections(
    conn: sqlite3.Connection, photo_id: int, detections: list[dict[str, Any]]
) -> None:
    """Insert or replace pet detections for a photo.

    Preserves existing cluster_id assignments for re-analyzed photos.
    """
    # Snapshot existing detections (with bbox + cluster) for IoU matching
    old_dets = []
    for row in conn.execute(
        "SELECT detection_index, class, cluster_id, bbox_x, bbox_y, bbox_w, bbox_h "
        "FROM pet_detections WHERE photo_id=?",
        (photo_id,),
    ).fetchall():
        old_dets.append(
            {
                "class": row[1],
                "cluster_id": row[2],
                "bbox_x": row[3],
                "bbox_y": row[4],
                "bbox_w": row[5],
                "bbox_h": row[6],
            }
        )

    # Build index-based fallback for detections without bboxes
    idx_fallback = {(row["class"], i): row["cluster_id"] for i, row in enumerate(old_dets)}

    # NAS-jitter resilience: same pattern as bulk_upsert_photos —
    # transient I/O errors during SMB/NFS writes get one retry instead
    # of silently dropping the detections for this photo. Local-disk
    # callers see no overhead.
    def _do_delete() -> None:
        conn.execute("DELETE FROM pet_detections WHERE photo_id=?", (photo_id,))

    retry_io(_do_delete, label="upsert_pet_detections.delete")
    if not detections:
        conn.commit()
        return
    rows = []
    for idx, d in enumerate(detections):
        prev_cid = CLUSTER_UNASSIGNED
        if old_dets:
            prev_cid = _match_cluster_by_iou(d, old_dets)
            if prev_cid == CLUSTER_UNASSIGNED:
                # Fallback to index+class matching (for detections without bboxes)
                prev_cid = idx_fallback.get((d["class"], idx), CLUSTER_UNASSIGNED)
        rows.append(
            (
                photo_id,
                idx,
                d["class"],
                d.get("confidence"),
                d.get("bbox_x"),
                d.get("bbox_y"),
                d.get("bbox_w"),
                d.get("bbox_h"),
                prev_cid,
            )
        )

    def _do_insert() -> None:
        conn.executemany(
            "INSERT INTO pet_detections"
            " (photo_id, detection_index, class, confidence,"
            " bbox_x, bbox_y, bbox_w, bbox_h, cluster_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()

    retry_io(_do_insert, label="upsert_pet_detections.insert")


def bulk_upsert_pet_detections(
    conn: sqlite3.Connection,
    items: list[tuple[int, list[dict[str, Any]]]],
) -> int:
    """Batch-upsert pet detections for multiple photos in one transaction.

    Preserves existing cluster_id assignments for re-analyzed photos
    by matching on (photo_id, class, detection_index).

    Args:
        items: list of (photo_id, detections) pairs.

    Returns:
        Total number of detections inserted.
    """
    if not items:
        return 0
    photo_ids = [pid for pid, _ in items]
    placeholders = ",".join(["?"] * len(photo_ids))

    # Snapshot existing detections per photo for IoU matching
    old_by_photo: dict[int, list[dict]] = {}
    for row in conn.execute(
        "SELECT photo_id, class, cluster_id, bbox_x, bbox_y, bbox_w, bbox_h"
        f" FROM pet_detections WHERE photo_id IN ({placeholders})",
        photo_ids,
    ).fetchall():
        old_by_photo.setdefault(row[0], []).append(
            {
                "class": row[1],
                "cluster_id": row[2],
                "bbox_x": row[3],
                "bbox_y": row[4],
                "bbox_w": row[5],
                "bbox_h": row[6],
            }
        )

    conn.execute(
        f"DELETE FROM pet_detections WHERE photo_id IN ({placeholders})",
        photo_ids,
    )
    all_rows: list[tuple] = []
    for photo_id, detections in items:
        old_dets = old_by_photo.get(photo_id, [])
        idx_fb = {(r["class"], i): r["cluster_id"] for i, r in enumerate(old_dets)}
        for idx, d in enumerate(detections):
            prev_cid = CLUSTER_UNASSIGNED
            if old_dets:
                prev_cid = _match_cluster_by_iou(d, old_dets)
                if prev_cid == CLUSTER_UNASSIGNED:
                    prev_cid = idx_fb.get((d["class"], idx), CLUSTER_UNASSIGNED)
            all_rows.append(
                (
                    photo_id,
                    idx,
                    d["class"],
                    d.get("confidence"),
                    d.get("bbox_x"),
                    d.get("bbox_y"),
                    d.get("bbox_w"),
                    d.get("bbox_h"),
                    prev_cid,
                )
            )

    # NAS-jitter resilience: wrap the delete+insert+commit as a single
    # retried unit so a transient blip mid-batch doesn't leave the DB
    # in a half-deleted / half-rewritten state. executemany is one
    # transaction; the commit is the boundary; the retry replays from
    # the start so any partial work rolls back cleanly.
    def _do_write() -> None:
        if all_rows:
            conn.executemany(
                "INSERT INTO pet_detections"
                " (photo_id, detection_index, class, confidence,"
                " bbox_x, bbox_y, bbox_w, bbox_h, cluster_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                all_rows,
            )
        conn.commit()

    retry_io(_do_write, label="bulk_upsert_pet_detections")
    return len(all_rows)


def get_pet_detections(conn: sqlite3.Connection, photo_id: int) -> list[dict[str, Any]]:
    """Get all pet detections for a photo."""
    rows = conn.execute(
        f"SELECT {_PET_COLS} FROM pet_detections "
        # Dismissed detections never surface as chips — dismissing a junk
        # cluster (e.g. a plush toy repeatedly detected as "dog") must
        # silence it everywhere. Unassigned/not-yet-clustered detections
        # DO show (clustering may simply not have run yet).
        "WHERE photo_id=? AND confidence >= ? "
        "AND (cluster_id IS NULL OR cluster_id != ?) "
        "ORDER BY detection_index",
        (photo_id, PET_DISPLAY_CONFIDENCE, CLUSTER_DISMISSED),
    ).fetchall()
    return [dict(r) for r in rows]


def get_pet_clusters(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Get pet clusters with their photo counts and representative detections.

    Returns a list like face clusters: each has cluster_id, pet_class, photo_count,
    representative (best confidence detection), and filepaths.
    """
    # Fetch all visible detections with filepaths in one query, ranked by confidence
    rows = conn.execute(
        "SELECT pd.id, pd.photo_id, pd.detection_index, pd.class, "
        "pd.confidence, pd.bbox_x, pd.bbox_y, pd.bbox_w, pd.bbox_h, "
        "pd.cluster_id, p.filepath, "
        "ROW_NUMBER() OVER (PARTITION BY pd.cluster_id ORDER BY pd.confidence DESC) AS rn "
        "FROM pet_detections pd "
        "JOIN photos p ON p.id = pd.photo_id "
        f"WHERE pd.cluster_id >= 0 AND pd.confidence >= {PET_DISPLAY_CONFIDENCE} "
        f"AND {active_photo_sql('p')}"
    ).fetchall()

    # Group by cluster_id
    cluster_map: dict[int, dict[str, Any]] = {}
    for row in rows:
        cid = row["cluster_id"]
        if cid not in cluster_map:
            cluster_map[cid] = {
                "cluster_id": cid,
                "pet_class": row["class"],
                "representative": None,
                "filepaths_set": set(),
            }
        entry = cluster_map[cid]
        entry["filepaths_set"].add(row["filepath"])
        if row["rn"] == 1:
            rep = dict(row)
            del rep["rn"]
            entry["representative"] = rep

    clusters = []
    for entry in cluster_map.values():
        photo_ids = entry["filepaths_set"]
        entry["photo_count"] = len(photo_ids)
        entry["filepaths"] = sorted(photo_ids)
        del entry["filepaths_set"]
        clusters.append(entry)

    clusters.sort(key=lambda c: c["photo_count"], reverse=True)
    return clusters


def assign_pet_clusters(conn: sqlite3.Connection) -> None:
    """Assign cluster_id to pet detections based on class.

    Default clustering: all cats = cluster 0, all dogs = cluster 1.
    Only assigns detections that are unassigned (cluster_id = -1),
    preserving any manual splits/renames.
    """
    conn.execute(
        "UPDATE pet_detections SET cluster_id=0 WHERE class='cat' AND cluster_id=?",
        (CLUSTER_UNASSIGNED,),
    )
    conn.execute(
        "UPDATE pet_detections SET cluster_id=1 WHERE class='dog' AND cluster_id=?",
        (CLUSTER_UNASSIGNED,),
    )
    conn.commit()


def _next_cluster_id(conn: sqlite3.Connection) -> int:
    """Return the next available pet cluster_id.

    Excludes sentinel values (CLUSTER_UNASSIGNED=-1, CLUSTER_DISMISSED=-2)
    and defaults to 2 to avoid colliding with hardcoded cat=0 / dog=1.
    """
    row = conn.execute(
        "SELECT MAX(cluster_id) FROM pet_detections WHERE cluster_id >= 0"
    ).fetchone()
    return max((row[0] or -1) + 1, 2)


def split_pet_cluster(conn: sqlite3.Connection, detection_ids: list[int]) -> int | None:
    """Move selected detections into a new cluster.

    Returns the new cluster_id, or None if no rows matched.
    """
    if not detection_ids:
        return None
    new_cid = _next_cluster_id(conn)
    placeholders = ",".join(["?"] * len(detection_ids))
    cursor = conn.execute(
        f"UPDATE pet_detections SET cluster_id=? WHERE id IN ({placeholders})",
        [new_cid, *detection_ids],
    )
    if cursor.rowcount == 0:
        return None
    conn.commit()
    return new_cid


def merge_pet_clusters(
    conn: sqlite3.Connection,
    primary_cluster_id: int,
    merge_cluster_ids: list[int],
) -> int:
    """Merge pet clusters into the primary. Returns count of moved detections."""
    if not merge_cluster_ids:
        return 0
    placeholders = ",".join(["?"] * len(merge_cluster_ids))
    cursor = conn.execute(
        f"UPDATE pet_detections SET cluster_id=? WHERE cluster_id IN ({placeholders})",
        [primary_cluster_id, *merge_cluster_ids],
    )
    conn.commit()
    return cursor.rowcount


def dismiss_pet_cluster(conn: sqlite3.Connection, cluster_id: int) -> int:
    """Mark every detection in a cluster as not-a-pet (false detection).

    Sets cluster_id to the CLUSTER_DISMISSED sentinel, which removes the
    cluster from the Pets view, photo chips, and pet smart albums (all
    readers filter on the sentinel). Returns count of dismissed detections.
    """
    if cluster_id < 0:
        return 0  # never "dismiss" a sentinel bucket
    cursor = conn.execute(
        "UPDATE pet_detections SET cluster_id=? WHERE cluster_id=?",
        (CLUSTER_DISMISSED, cluster_id),
    )
    conn.commit()
    return cursor.rowcount


def has_pet_data(conn: sqlite3.Connection) -> bool:
    """Check if there are any pet detections in the DB."""
    row = conn.execute("SELECT 1 FROM pet_detections LIMIT 1").fetchone()
    return row is not None
