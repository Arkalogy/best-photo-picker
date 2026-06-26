"""Per-photo face surface: faces-on-this-photo + cropped face JPEG.

Extracted from bp_faces.py during the v0.1 cleanup. Both endpoints
take a path_hash and return data about the faces detected on that
specific photo:

* ``/api/v1/faces/photo/<path_hash>`` — full face dicts (bbox,
  cluster_id, identity, person tags) for every face on the photo.
* ``/api/v1/faces/crop/<path_hash>/<face_index>`` — cached square
  JPEG crop of one face on disk, generated lazily on first request
  from the bbox in face_embeddings.

Both read from the face_embeddings + photos join and don't mutate;
splitting them out keeps bp_faces focused on cluster-level reads.
"""

from __future__ import annotations

import os

from flask import Blueprint, Response, jsonify, send_file

from bpp.db.photos import get_photo_id_by_path
from bpp.errors import BppError, NotFoundError
from bpp.utils.logging import get_logger
from bpp.web.face_worker import generate_face_crop
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("faces_photo", __name__)


def _oriented_dims(filepath: str) -> tuple[int, int]:
    """Original image dimensions in EXIF-transposed (display) space.

    Face bbox coords are stored relative to the exif-transposed image, so
    the overlay math needs the transposed dims. We read them from the
    header + the orientation tag WITHOUT a full pixel decode — the old
    ``ImageOps.exif_transpose()`` forced a decode on every lightbox open
    (slow on HEIC). Neither PIL nor pillow_heif applies orientation to
    ``.size``, so for the 90deg/270deg orientations (5-8) the transposed dims
    are simply the raw dims swapped — matching ``exif_transpose().size``
    (parity pinned in tests/test_faces_photo_bbox_dims.py). Returns (0, 0)
    when the file can't be read.
    """
    try:
        from PIL import Image

        from bpp.utils.retry import retry_io

        try:
            from pillow_heif import register_heif_opener

            register_heif_opener()
        except ImportError:
            pass

        with retry_io(Image.open, filepath, label="bbox_pct") as img:
            raw_w, raw_h = img.size
            try:
                orientation = img.getexif().get(0x0112, 1)  # 0x0112 = Orientation
            except Exception:
                orientation = 1
        return (raw_h, raw_w) if orientation in (5, 6, 7, 8) else (raw_w, raw_h)
    except (FileNotFoundError, OSError) as e:
        log.warning("Could not read image for bbox percentages: %s — %s", filepath, e)
        return 0, 0


@bp.get("/api/v1/faces/photo/<path_hash>")
def api_faces_for_photo(path_hash: str) -> tuple[Response, int]:
    """Return face info (index, cluster_id, cluster name) for all faces in a photo."""
    ctx = get_ctx()
    if ctx.thumbs is None:
        raise NotFoundError("No thumbnails loaded")
    filepath = ctx.thumbs.get_filepath(path_hash)
    if not filepath:
        raise NotFoundError("Unknown image", path_hash=path_hash)

    conn = ctx.get_conn()
    rows = conn.execute(
        "SELECT fe.id, fe.face_index, fe.cluster_id, "
        "fe.bbox_x, fe.bbox_y, fe.bbox_w, fe.bbox_h, "
        "fe.extraction_max_long_side "
        "FROM face_embeddings fe "
        "JOIN photos p ON p.id = fe.photo_id "
        "WHERE p.filepath=? ORDER BY fe.face_index",
        (filepath,),
    ).fetchall()

    # Build cluster name map
    cluster_ids = list(
        {r["cluster_id"] for r in rows if r["cluster_id"] is not None and r["cluster_id"] >= 0}
    )
    name_map: dict[int, str] = {}
    if cluster_ids:
        # v36: probe the indexed shadow column instead of the
        # json_extract-on-every-row anti-pattern. Same result, O(log N)
        # instead of full table scan.
        placeholders = ",".join(["?"] * len(cluster_ids))
        name_rows = conn.execute(
            f"SELECT smart_person_cluster_id AS cid, name FROM albums "
            f"WHERE album_type='smart_person' "
            f"AND smart_person_cluster_id IN ({placeholders})",
            cluster_ids,
        ).fetchall()
        for nr in name_rows:
            cid = nr["cid"]
            if cid is not None:
                name_map[cid] = nr["name"]

    # Compute detection image dimensions for percentage-based bbox.
    # Each face row records the detector input size it was extracted at
    # (v40 / Bug #9 hardening). The bbox values are in that coordinate
    # space, so we reconstruct dimensions per row using the row's own
    # extraction_max_long_side, NOT the live config — a config change
    # mid-library would otherwise silently shift every stored overlay.
    # Pre-v40 rows have extraction_max_long_side=NULL; we fall back to
    # the current config and log once per request so the user knows
    # to re-extract for correctness.
    current_max_long = ctx.config.get("max_long_side", 1024) if ctx.config else 1024
    orig_w, orig_h = _oriented_dims(filepath)

    def _detector_dims(row_max_long: int | None) -> tuple[int, int]:
        """Detector-space dimensions for a row whose extraction max_long
        was ``row_max_long`` (or NULL → use current config + log)."""
        if orig_w <= 0 or orig_h <= 0:
            return 0, 0
        max_long = row_max_long if row_max_long is not None else current_max_long
        long_side = max(orig_w, orig_h)
        scale = long_side / max_long if long_side > max_long else 1.0
        return round(orig_w / scale), round(orig_h / scale)

    pre_v40_logged = False
    faces = []
    for r in rows:
        cid = r["cluster_id"]
        face: dict = {
            "face_id": r["id"],
            "face_index": r["face_index"],
            "cluster_id": cid,
            "name": name_map.get(cid) if cid is not None and cid >= 0 else None,
            "bbox_w": r["bbox_w"],
            "bbox_h": r["bbox_h"],
        }
        row_max_long = r["extraction_max_long_side"]
        if row_max_long is None and not pre_v40_logged:
            log.warning(
                "Pre-v40 face row(s) for %s — falling back to current "
                "max_long_side=%d for overlay coords. If the setting changed "
                "since extraction, re-extract to restore correctness.",
                filepath,
                current_max_long,
            )
            pre_v40_logged = True
        det_w, det_h = _detector_dims(row_max_long)
        if det_w > 0 and det_h > 0 and r["bbox_x"] is not None:
            face["bbox_pct"] = {
                "x": round(r["bbox_x"] / det_w * 100, 2),
                "y": round(r["bbox_y"] / det_h * 100, 2),
                "w": round(r["bbox_w"] / det_w * 100, 2),
                "h": round(r["bbox_h"] / det_h * 100, 2),
            }
        faces.append(face)

    # Load manual person tags for this photo
    person_tags = []
    try:
        photo_id = get_photo_id_by_path(conn, filepath)
        if photo_id is not None:
            tag_rows = conn.execute(
                "SELECT cluster_id FROM photo_person_tags WHERE photo_id=?",
                (photo_id,),
            ).fetchall()
            for tr in tag_rows:
                cid = tr["cluster_id"]
                person_tags.append({"cluster_id": cid, "name": name_map.get(cid)})
    except Exception as e:
        log.warning("Failed to load person tags for photo: %s", e)

    return jsonify({"faces": faces, "person_tags": person_tags, "thumb_hash": path_hash}), 200


@bp.get("/api/v1/faces/crop/<path_hash>/<int:face_index>")
def api_face_crop(path_hash: str, face_index: int) -> Response | tuple[Response, int]:
    """Serve the cached square JPEG crop of a single face within a
    photo. Generates the crop on first request from the bbox stored
    in face_embeddings; subsequent requests serve the cached file."""
    ctx = get_ctx()
    if ctx.thumbs is None:
        raise NotFoundError("No thumbnails")
    filepath = ctx.thumbs.get_filepath(path_hash)
    if not filepath:
        raise NotFoundError("Unknown image", path_hash=path_hash)

    wd = ctx.workdir
    if not wd:
        raise NotFoundError("No workdir")

    conn = ctx.get_conn()
    row = conn.execute(
        "SELECT fe.bbox_x, fe.bbox_y, fe.bbox_w, fe.bbox_h, "
        "fe.extraction_max_long_side "
        "FROM face_embeddings fe "
        "JOIN photos p ON p.id = fe.photo_id "
        "WHERE p.filepath=? AND fe.face_index=?",
        (filepath, face_index),
    ).fetchone()
    if not row:
        raise NotFoundError(
            "Face not found",
            path_hash=path_hash,
            face_index=face_index,
        )

    crop_dir = ctx.dirs["face_crops"]
    os.makedirs(crop_dir, exist_ok=True)
    # Use the detector size the row was extracted at (v40 / Bug #9).
    # Pre-v40 rows have NULL → fall back to current config.
    max_long_side = row["extraction_max_long_side"]
    if max_long_side is None:
        max_long_side = ctx.config.get("max_long_side", 1024)

    # Extract just the bbox columns — generate_face_crop expects a 4-tuple.
    # The full row also carries extraction_max_long_side (added in v40),
    # which would crash the (bx, by, bw, bh) unpack inside the helper.
    bbox = (row["bbox_x"], row["bbox_y"], row["bbox_w"], row["bbox_h"])
    crop_path = generate_face_crop(filepath, bbox, crop_dir, path_hash, face_index, max_long_side)
    if crop_path is None:
        raise BppError(
            "Failed to generate crop",
            user_message="Failed to generate crop",
            diagnostic_message=(
                f"generate_face_crop returned None for {filepath} face_index={face_index}"
            ),
            path_hash=path_hash,
            face_index=face_index,
        )

    return send_file(crop_path, mimetype="image/jpeg")
