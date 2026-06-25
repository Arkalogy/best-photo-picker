"""Face bbox endpoints: user-drawn bounding box operations.

Extracted from bp_faces_manage.py during the v0.1 cleanup. These two
endpoints share a common shape — the user has drawn or dragged a
bounding box on a photo, and the server re-runs face detection +
embedding extraction on that region. They sit between the bulk
cluster-mutation endpoints (merge, dismiss, restore) and the per-face
ops (reassign), with the distinguishing feature that **the bbox
itself is user input**.

The two endpoints:

* ``POST /api/v1/faces/create`` — user drew a box where the detector
  missed a face. Mint a new face_embeddings row (existing person or
  new-person + smart_person album, atomic).

* ``POST /api/v1/faces/update-bbox`` — user dragged an existing
  detection's box onto the right face. Re-extract embedding in place,
  preserve cluster identity (no auto-rematch — that was unreliable).

Both refuse 422 when YuNet can't confirm a face in the user's region.
Without that gate, a user-drawn box over a busy pattern produces a
hallucinated embedding that pollutes downstream clustering.
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from bpp.constants import FACE_CLUSTER_THRESHOLD_FALLBACK
from bpp.db.face_cluster_ops import sync_face_count as _sync_face_count
from bpp.db.photos import get_photo_id_by_path
from bpp.db.smart_albums import get_affected_album_types, refresh_smart_albums
from bpp.errors import BppError, NotFoundError, ValidationError
from bpp.utils.logging import get_logger

# Five named helpers extracted to face_create_helpers.py to keep this
# file under the 500-LOC cap. Aliased to the previous private names so
# the handler reads with the same shape and any test source-scan that
# expects the helpers reachable from this module path keeps working.
from bpp.web.face_create_helpers import (
    detect_duplicate_face as _detect_duplicate_face,
)
from bpp.web.face_create_helpers import (
    extract_embedding_at_region as _extract_embedding_at_region,
)
from bpp.web.face_create_helpers import (
    insert_face_and_optional_album as _insert_face_and_optional_album,
)
from bpp.web.face_create_helpers import (
    parse_create_inputs as _parse_create_inputs,
)
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx, with_face_lock

log = get_logger(__name__)

bp = Blueprint("faces_bbox", __name__)


@bp.post("/api/v1/faces/create")
@requires_local_app
@with_face_lock
def api_faces_create() -> tuple[Response, int]:
    """Create a new face_embeddings row from a user-drawn bbox.

    Used when YuNet missed a face entirely (e.g. a face the user can see
    that the detector skipped). The user draws a box on the photo and
    picks a person; we extract the embedding from that region (with the
    same YuNet-must-confirm-a-face rule as update-bbox) and insert a
    new row tied to the chosen cluster.

    Two modes:

    - **Existing person**: caller passes ``cluster_id`` (non-negative int).
      The cluster must already have at least one face_embeddings row.
    - **New person**: caller passes ``new_person_name`` (non-empty string).
      Server allocates the next cluster_id, inserts the face row, and
      creates a smart_person album with that name — all in one
      transaction so partial state is impossible.

    Refuses (422) if YuNet doesn't find a face in the region — same
    rule as update-bbox, for the same reason: trusting a user-drawn
    box without detector confirmation produces hallucinated embeddings
    that pollute clustering.

    Decomposed into four named helpers (review followup 2026-05-31):
    parse → extract → duplicate-guard → insert. The handler body is
    the orchestration; per-step logic lives in :func:`_parse_create_inputs`,
    :func:`_extract_embedding_at_region`, :func:`_detect_duplicate_face`,
    and :func:`_insert_face_and_optional_album`.
    """
    from bpp.scoring.aggregate import load_and_downscale

    ctx = get_ctx()
    data = request.get_json(silent=True) or {}

    # 1. Parse + validate.
    path_hash, cluster_id, new_person_name, bbox_pct_tuple = _parse_create_inputs(data)

    if ctx.thumbs is None:
        raise NotFoundError("No thumbnails loaded")
    filepath = ctx.thumbs.get_filepath(path_hash)
    if not filepath:
        raise NotFoundError("Unknown image", path_hash=path_hash)

    conn = ctx.get_conn()
    photo_id = get_photo_id_by_path(conn, filepath)
    if photo_id is None:
        raise NotFoundError("Photo not in database", filepath=filepath)

    if new_person_name is None:
        # Existing person — cluster must already have at least one row.
        cluster_check = conn.execute(
            "SELECT 1 FROM face_embeddings WHERE cluster_id=? LIMIT 1",
            (cluster_id,),
        ).fetchone()
        if not cluster_check:
            raise ValidationError(
                f"Unknown cluster_id {cluster_id}",
                field="cluster_id",
                value=cluster_id,
            )

    # 2. Load image + extract embedding at the user's region.
    max_long_side = ctx.config.get("max_long_side", 1024) if ctx.config else 1024
    image = load_and_downscale(filepath, max_long_side)
    if image is None:
        raise BppError(
            "Failed to load source image",
            user_message="Failed to load source image",
            diagnostic_message=f"load_and_downscale returned None for {filepath}",
            filepath=filepath,
        )
    det_h, det_w = image.shape[:2]
    result = _extract_embedding_at_region(image, bbox_pct_tuple)
    new_bbox = result["bbox"]
    new_emb = result["embedding"]
    new_quality = float(result["quality"])

    # 3. Duplicate guard.
    emb_thresh = float(ctx.config.get("face_cluster_threshold", FACE_CLUSTER_THRESHOLD_FALLBACK))
    _detect_duplicate_face(conn, photo_id, new_bbox, new_emb, emb_thresh)

    # 4. Insert face row (+ optional smart_person album), commit.
    face_id, next_idx, cluster_id = _insert_face_and_optional_album(
        conn,
        photo_id=photo_id,
        new_bbox=new_bbox,
        new_emb=new_emb,
        new_quality=new_quality,
        cluster_id=cluster_id,
        new_person_name=new_person_name,
        extraction_max_long_side=max_long_side,
    )
    conn.commit()
    _sync_face_count(conn, face_id)
    refresh_smart_albums(conn, kinds=get_affected_album_types("face_tag"))

    # v36: indexed shadow column lookup.
    name_row = conn.execute(
        "SELECT name FROM albums WHERE album_type='smart_person' AND smart_person_cluster_id = ?",
        (cluster_id,),
    ).fetchone()
    person_name = name_row["name"] if name_row else None

    new_bbox_pct = {
        "x": round(new_bbox[0] / det_w * 100, 2),
        "y": round(new_bbox[1] / det_h * 100, 2),
        "w": round(new_bbox[2] / det_w * 100, 2),
        "h": round(new_bbox[3] / det_h * 100, 2),
    }

    return (
        jsonify(
            {
                "status": "created",
                "face_id": face_id,
                "face_index": next_idx,
                "cluster_id": cluster_id,
                "person_name": person_name,
                "method": result["method"],
                "quality": new_quality,
                "bbox_pct": new_bbox_pct,
            }
        ),
        200,
    )


@bp.post("/api/v1/faces/update-bbox")
@requires_local_app
@with_face_lock
def api_faces_update_bbox() -> tuple[Response, int]:
    """Update a face's bbox after the user drags it onto the correct face.

    Re-extracts the embedding from the new region and persists the new
    bbox + embedding + quality. The face's existing cluster_id is preserved
    — this endpoint never re-matches identity. Auto-matching from a
    user-drawn box was unreliable in practice: tiny crop shifts produced
    embeddings that landed marginally closer to other clusters than the
    face's own, so a legitimate resize could silently flip the person.
    Use the explicit "Reassign this face" or "Label" flows to change
    identity.
    """
    from bpp.scoring.aggregate import load_and_downscale
    from bpp.scoring.face_embed import extract_embedding_for_region

    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    face_id = data.get("face_id")
    bbox_pct = data.get("bbox_pct") or {}
    if not isinstance(face_id, int):
        raise ValidationError("face_id (int) required", field="face_id")
    try:
        px = float(bbox_pct.get("x"))
        py = float(bbox_pct.get("y"))
        pw = float(bbox_pct.get("w"))
        ph = float(bbox_pct.get("h"))
    except (TypeError, ValueError) as e:
        raise ValidationError(
            "bbox_pct.{x,y,w,h} must be numbers",
            field="bbox_pct",
        ) from e
    if pw <= 0 or ph <= 0 or px < 0 or py < 0 or px + pw > 100 or py + ph > 100:
        raise ValidationError(
            "bbox_pct out of [0,100] bounds",
            field="bbox_pct",
        )

    conn = ctx.get_conn()
    row = conn.execute(
        "SELECT fe.id, fe.photo_id, fe.cluster_id, p.filepath FROM face_embeddings fe "
        "JOIN photos p ON p.id = fe.photo_id WHERE fe.id=?",
        (face_id,),
    ).fetchone()
    if not row:
        raise NotFoundError("Face not found", face_id=face_id)
    filepath = row["filepath"]
    existing_cluster_id = int(row["cluster_id"])

    max_long_side = ctx.config.get("max_long_side", 1024) if ctx.config else 1024
    image = load_and_downscale(filepath, max_long_side)
    if image is None:
        raise BppError(
            "Failed to load source image",
            user_message="Failed to load source image",
            diagnostic_message=f"load_and_downscale returned None for {filepath}",
            filepath=filepath,
        )
    det_h, det_w = image.shape[:2]
    bx = round(px / 100.0 * det_w)
    by = round(py / 100.0 * det_h)
    bw = round(pw / 100.0 * det_w)
    bh = round(ph / 100.0 * det_h)

    result = extract_embedding_for_region(image, (bx, by, bw, bh))
    if result is None:
        exc = ValidationError(
            "No face detected in that region. Drag the outline so it covers "
            "the actual face, then drop.",
            field="bbox_pct",
        )
        exc.http_status = 422  # type: ignore[misc]
        raise exc

    new_bbox = result["bbox"]
    new_emb = result["embedding"]
    new_quality = float(result["quality"])
    method = result["method"]

    # Identity stickiness: never auto-rematch. Persist new bbox + embedding
    # under the face's existing cluster_id. Use Reassign / Label flows
    # to change identity.
    new_cluster_id = existing_cluster_id
    matched = existing_cluster_id >= 0

    conn.execute(
        "UPDATE face_embeddings "
        "SET bbox_x=?, bbox_y=?, bbox_w=?, bbox_h=?, "
        "    embedding=?, quality=?, extraction_max_long_side=? "
        "WHERE id=?",
        (
            int(new_bbox[0]),
            int(new_bbox[1]),
            int(new_bbox[2]),
            int(new_bbox[3]),
            new_emb.tobytes(),
            new_quality,
            max_long_side,
            face_id,
        ),
    )
    conn.commit()

    # Invalidate cached face crop so the UI re-renders from the new bbox.
    try:
        face_index_row = conn.execute(
            "SELECT face_index FROM face_embeddings WHERE id=?", (face_id,)
        ).fetchone()
        path_hash = ctx.thumbs.get_hash(filepath) if ctx.thumbs else None
        if face_index_row and path_hash and ctx.dirs.get("face_crops"):
            import os as _os

            crop_path = _os.path.join(
                ctx.dirs["face_crops"],
                f"{path_hash}_{face_index_row['face_index']}.jpg",
            )
            if _os.path.exists(crop_path):
                _os.remove(crop_path)
    except Exception:
        log.debug("Failed to invalidate face crop cache", exc_info=True)

    _sync_face_count(conn, face_id)
    refresh_smart_albums(conn, kinds=get_affected_album_types("face_tag"))

    # Resolve person display name for the (preserved) cluster.
    # v36: indexed shadow column lookup.
    person_name = None
    if matched:
        name_row = conn.execute(
            "SELECT name FROM albums WHERE album_type='smart_person' "
            "AND smart_person_cluster_id = ?",
            (new_cluster_id,),
        ).fetchone()
        if name_row:
            person_name = name_row["name"]

    # Compute new bbox_pct from the (possibly adjusted) detection-space bbox.
    new_bbox_pct = {
        "x": round(new_bbox[0] / det_w * 100, 2),
        "y": round(new_bbox[1] / det_h * 100, 2),
        "w": round(new_bbox[2] / det_w * 100, 2),
        "h": round(new_bbox[3] / det_h * 100, 2),
    }

    return (
        jsonify(
            {
                "status": "updated",
                "face_id": face_id,
                "cluster_id": new_cluster_id,
                "matched": matched,
                "person_name": person_name,
                "method": method,
                "quality": new_quality,
                "bbox_pct": new_bbox_pct,
            }
        ),
        200,
    )
