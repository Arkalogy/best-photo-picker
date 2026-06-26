"""Pets blueprint: crop, clusters, naming, split/merge."""

from __future__ import annotations

import hashlib
import os

from flask import Blueprint, Response, jsonify, request, send_file

from bpp.constants import ACTIVE_PHOTO_SQL, JPEG_QUALITY_CROP, PET_CROP_PADDING, PET_CROP_SIZE
from bpp.db.albums import list_albums as db_list_albums
from bpp.db.pets import (
    dismiss_pet_cluster,
    get_pet_clusters,
    has_pet_data,
    merge_pet_clusters,
    split_pet_cluster,
)
from bpp.db.photos import get_photo_id_by_path
from bpp.db.smart_albums import get_affected_album_types, refresh_smart_albums
from bpp.errors import BppError, NotFoundError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

_ACTIVE = ACTIVE_PHOTO_SQL

log = get_logger(__name__)

bp = Blueprint("pets", __name__)


def _generate_pet_crop(
    filepath: str,
    bbox: tuple[int, int, int, int],
    crop_dir: str,
    path_hash: str,
    detection_index: int,
    max_long_side: int = 1024,
) -> str | None:
    """Generate a cropped pet thumbnail, returns path or None."""
    # Include bbox hash so re-analysis with a different bbox gets a fresh crop.
    bbox_tag = hashlib.md5(f"{bbox}".encode()).hexdigest()[:8]
    crop_path = os.path.join(crop_dir, f"pet_{path_hash}_{detection_index}_{bbox_tag}.jpg")
    if os.path.exists(crop_path):
        return crop_path

    try:
        from PIL import Image, ImageOps

        from bpp.utils.retry import retry_io

        # Context manager closes the file handle — on-demand crops leak an
        # FD each otherwise (same fix as face_crop.py).
        with retry_io(Image.open, filepath, label="pet_crop") as src:
            img = ImageOps.exif_transpose(src)
            orig_w, orig_h = img.size
            long_side = max(orig_w, orig_h)
            scale = long_side / max_long_side if long_side > max_long_side else 1.0

            bx, by, bw, bh = bbox
            sx = int(bx * scale)
            sy = int(by * scale)
            sw = int(bw * scale)
            sh = int(bh * scale)

            pad_x = int(sw * PET_CROP_PADDING)
            pad_y = int(sh * PET_CROP_PADDING)
            x1 = max(0, sx - pad_x)
            y1 = max(0, sy - pad_y)
            x2 = min(orig_w, sx + sw + pad_x)
            y2 = min(orig_h, sy + sh + pad_y)

            if x1 >= x2 or y1 >= y2:
                log.warning("Invalid crop region for %s pet %d", filepath, detection_index)
                return None

            crop = img.crop((x1, y1, x2, y2))
        crop.thumbnail((PET_CROP_SIZE, PET_CROP_SIZE), Image.LANCZOS)
        crop.convert("RGB").save(crop_path, "JPEG", quality=JPEG_QUALITY_CROP)
        return crop_path
    except Exception:
        log.exception("Failed to generate pet crop for %s", filepath)
        return None


@bp.get("/api/v1/pets/crop/<path_hash>/<int:detection_index>")
def api_pet_crop(path_hash: str, detection_index: int) -> Response | tuple[Response, int]:
    """Serve a square JPEG crop of a single pet detection within a
    photo. Generates the crop from the bbox in pet_detections on
    first request and caches it under ``cache/pet_crops/`` keyed by
    path_hash + detection_index + bbox digest."""
    ctx = get_ctx()
    if ctx.thumbs is None:
        raise NotFoundError("No thumbnails")
    filepath = ctx.thumbs.get_filepath(path_hash)
    if not filepath:
        raise NotFoundError("Unknown image", path_hash=path_hash)

    if not ctx.dirs:
        raise NotFoundError("No library loaded")

    conn = ctx.get_conn()
    row = conn.execute(
        "SELECT pd.bbox_x, pd.bbox_y, pd.bbox_w, pd.bbox_h "
        "FROM pet_detections pd "
        "JOIN photos p ON p.id = pd.photo_id "
        "WHERE p.filepath=? AND pd.detection_index=?",
        (filepath, detection_index),
    ).fetchone()
    if not row:
        raise NotFoundError(
            "Pet detection not found",
            path_hash=path_hash,
            detection_index=detection_index,
        )

    crop_dir = ctx.dirs["pet_crops"]
    os.makedirs(crop_dir, exist_ok=True)
    max_long_side = ctx.config.get("max_long_side", 1024)

    crop_path = _generate_pet_crop(
        filepath, row, crop_dir, path_hash, detection_index, max_long_side
    )
    if crop_path is None:
        raise BppError(
            "Failed to generate crop",
            user_message="Failed to generate crop",
            diagnostic_message=(
                f"_generate_pet_crop returned None for {filepath} index={detection_index}"
            ),
            path_hash=path_hash,
            detection_index=detection_index,
        )

    return send_file(crop_path, mimetype="image/jpeg")


@bp.get("/api/v1/pets/clusters")
def api_pet_clusters() -> tuple[Response, int]:
    """Return pet clusters with representative crops and photo counts."""
    ctx = get_ctx()
    conn = ctx.get_conn()

    if not has_pet_data(conn):
        return jsonify({"clusters": []}), 200

    clusters = get_pet_clusters(conn)

    # Add thumb_hash to representatives
    result = []
    for c in clusters:
        rep = c.get("representative")
        if rep and ctx.thumbs:
            fp = rep.get("filepath", "")
            rep["thumb_hash"] = ctx.thumbs.get_hash(fp)
        result.append(
            {
                "cluster_id": c["cluster_id"],
                "pet_class": c["pet_class"],
                "photo_count": c["photo_count"],
                "representative": rep,
                "filepaths": c["filepaths"],
            }
        )

    return jsonify({"clusters": result}), 200


@bp.get("/api/v1/pets/detections/<path_hash>")
def api_pet_detections_for_photo(path_hash: str) -> tuple[Response, int]:
    """Return pet detections for a specific photo."""
    ctx = get_ctx()
    if ctx.thumbs is None:
        return jsonify({"detections": []}), 200
    filepath = ctx.thumbs.get_filepath(path_hash)
    if not filepath:
        return jsonify({"detections": []}), 200

    conn = ctx.get_conn()
    photo_id = get_photo_id_by_path(conn, filepath)
    if photo_id is None:
        return jsonify({"detections": []}), 200

    from bpp.db.pets import get_pet_detections

    detections = get_pet_detections(conn, photo_id)
    return jsonify({"detections": detections}), 200


@bp.get("/api/v1/pets/cluster/<int:cluster_id>")
def api_pet_cluster_detail(cluster_id: int) -> tuple[Response, int]:
    """Return pet detections for a cluster (for identify picker). Sampled if large."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    limit = request.args.get("limit", 80, type=int)

    rows = conn.execute(
        "SELECT pd.id, pd.detection_index, p.filepath, pd.confidence "
        "FROM pet_detections pd "
        "JOIN photos p ON p.id = pd.photo_id "
        f"WHERE pd.cluster_id = ? AND p.{_ACTIVE} "
        "ORDER BY COALESCE(pd.confidence, 0) DESC, p.filepath, pd.detection_index",
        (cluster_id,),
    ).fetchall()

    detections = []
    seen: set[tuple[str, int]] = set()
    for r in rows:
        fp, di = r["filepath"], r["detection_index"]
        key = (fp, di)
        if key in seen:
            continue
        seen.add(key)
        detections.append(
            {
                "detection_id": r["id"],
                "detection_index": di,
                "filepath": fp,
                "confidence": round(r["confidence"], 3) if r["confidence"] else None,
            }
        )

    total = len(detections)
    if limit > 0 and total > limit:
        step = total / limit
        detections = [detections[int(i * step)] for i in range(limit)]

    for d in detections:
        d["thumb_hash"] = ctx.thumbs.get_hash(d["filepath"]) if ctx.thumbs else ""

    return jsonify({"detections": detections, "total": total}), 200


@bp.post("/api/v1/pets/split")
@requires_local_app
def api_pet_split() -> tuple[Response, int]:
    """Move selected detections into a new pet cluster."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    detection_ids = data.get("detection_ids")
    if not detection_ids or not isinstance(detection_ids, list):
        raise ValidationError("detection_ids required", field="detection_ids")
    if not all(isinstance(d, int) for d in detection_ids):
        raise ValidationError(
            "detection_ids must be integers",
            field="detection_ids",
        )

    conn = ctx.get_conn()
    log.info("Pet split: %d detection(s) %s", len(detection_ids), detection_ids)
    new_cid = split_pet_cluster(conn, detection_ids)
    if new_cid is None:
        raise NotFoundError(
            "No matching detections",
            detection_ids=detection_ids,
        )

    log.info("Pet split created cluster %d", new_cid)
    refresh_smart_albums(conn, kinds=get_affected_album_types("pet_detect"))
    albums = db_list_albums(conn)
    return jsonify({"status": "ok", "cluster_id": new_cid, "albums": albums}), 200


@bp.post("/api/v1/pets/merge")
@requires_local_app
def api_pet_merge() -> tuple[Response, int]:
    """Merge pet clusters into a primary cluster."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    primary_id = data.get("primary_cluster_id")
    merge_ids = data.get("merge_cluster_ids")
    if primary_id is None or not merge_ids:
        raise ValidationError(
            "primary_cluster_id and merge_cluster_ids required",
        )
    if not isinstance(primary_id, int) or not isinstance(merge_ids, list):
        raise ValidationError(
            "invalid parameter types",
            primary_cluster_id_type=type(primary_id).__name__,
            merge_cluster_ids_type=type(merge_ids).__name__,
        )
    if not all(isinstance(m, int) for m in merge_ids):
        raise ValidationError(
            "merge_cluster_ids must be integers",
            field="merge_cluster_ids",
        )

    conn = ctx.get_conn()
    log.info("Pet merge: primary=%d, merge_ids=%s", primary_id, merge_ids)
    count = merge_pet_clusters(conn, primary_id, merge_ids)
    log.info("Pet merge moved %d detection(s) into cluster %d", count, primary_id)
    refresh_smart_albums(conn, kinds=get_affected_album_types("pet_detect"))
    albums = db_list_albums(conn)
    return jsonify({"status": "ok", "count": count, "albums": albums}), 200


@bp.post("/api/v1/pets/dismiss")
@requires_local_app
def api_pet_dismiss() -> tuple[Response, int]:
    """Mark a pet cluster as not-a-pet (false detection)."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    cluster_id = data.get("cluster_id")
    if not isinstance(cluster_id, int) or cluster_id < 0:
        raise ValidationError("cluster_id must be a non-negative integer", field="cluster_id")

    conn = ctx.get_conn()
    count = dismiss_pet_cluster(conn, cluster_id)
    if count == 0:
        raise NotFoundError("No detections in that cluster", cluster_id=cluster_id)

    log.info("Pet dismiss: cluster %d, %d detection(s) marked not-a-pet", cluster_id, count)
    # The dismiss itself is committed; a refresh failure must not turn a
    # successful dismiss into a 500. Report success with a warning so the
    # user knows the album list may be stale until the next refresh.
    warning = None
    try:
        refresh_smart_albums(conn, kinds=get_affected_album_types("pet_detect"))
    except Exception:
        log.error(
            "Pet dismiss succeeded but album refresh failed (cluster %d)",
            cluster_id,
            exc_info=True,
        )
        warning = "Dismissed, but album refresh failed — albums may be stale until the next refresh"
    albums = db_list_albums(conn)
    result = {"status": "ok", "count": count, "albums": albums}
    if warning:
        result["warning"] = warning
    return jsonify(result), 200
