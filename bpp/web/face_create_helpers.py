"""Helpers backing the ``POST /api/v1/faces/create`` handler.

Extracted from :mod:`bpp.web.bp_faces_bbox` after the post-review
decomposition pushed the file over the 500-LOC cap. The handler in
``bp_faces_bbox.py`` is the orchestrator; each named step lives here
and is independently unit-testable.

Public names (no leading underscore) so the helpers can be unit-
tested directly without reaching into ``bp_faces_bbox``'s private
surface. The handler aliases them with the leading-underscore names
to match its previous import shape.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from bpp.db.face_embedding_safety import decode_embedding
from bpp.errors import ConflictError, ValidationError

# Duplicate-guard IoU threshold — see :func:`detect_duplicate_face`
# for the both-signals-must-agree rationale. Pulled out as a module
# constant so a future tuning request lands in one place.
IOU_DUP_THRESH = 0.5


def parse_bbox_pct(bbox_pct: dict) -> tuple[float, float, float, float]:
    """Coerce + range-check the ``bbox_pct`` payload from the request.

    Raises :class:`ValidationError` on missing / non-numeric / out-of-
    range values. Returned tuple is ``(x, y, w, h)`` in percent units.
    """
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
    return px, py, pw, ph


def parse_create_inputs(
    data: dict,
) -> tuple[str, int | None, str | None, tuple[float, float, float, float]]:
    """Validate the request body and return ``(path_hash, cluster_id,
    new_person_name, bbox_pct_tuple)``.

    Exactly one of ``cluster_id`` / ``new_person_name`` must be set.
    """
    path_hash = data.get("path_hash")
    cluster_id_raw = data.get("cluster_id")
    new_person_name_raw = data.get("new_person_name")
    bbox_pct = data.get("bbox_pct") or {}

    if not path_hash:
        raise ValidationError("path_hash required", field="path_hash")

    new_person_name: str | None = None
    if isinstance(new_person_name_raw, str) and new_person_name_raw.strip():
        new_person_name = new_person_name_raw.strip()

    cluster_id: int | None = None
    if new_person_name is None:
        if not isinstance(cluster_id_raw, int) or cluster_id_raw < 0:
            raise ValidationError(
                "cluster_id (non-negative int) or new_person_name required",
            )
        cluster_id = cluster_id_raw
    elif cluster_id_raw is not None:
        raise ValidationError(
            "Pass cluster_id OR new_person_name, not both",
        )

    return path_hash, cluster_id, new_person_name, parse_bbox_pct(bbox_pct)


def extract_embedding_at_region(
    image: Any, bbox_pct_tuple: tuple[float, float, float, float]
) -> dict:
    """Run the YuNet-confirm + embedding pipeline at the user-supplied
    percent-bbox. Returns the extractor result dict.

    Raises :class:`ValidationError` with HTTP 422 when YuNet doesn't
    find a face in the region — the same gate as ``update-bbox``.
    """
    from bpp.scoring.face_embed import extract_embedding_for_region

    det_h, det_w = image.shape[:2]
    px, py, pw, ph = bbox_pct_tuple
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
    return result


def detect_duplicate_face(
    conn: Any, photo_id: int, new_bbox: Any, new_emb: Any, emb_thresh: float
) -> None:
    """Refuse if the user-drawn box matches an existing face_embeddings
    row by BOTH IoU and embedding distance.

    Either signal alone produces false positives — high IoU happens for
    two adjacent faces (kids leaning into each other), and a close
    embedding without spatial overlap is just the same person elsewhere
    in the photo. Requiring both means we only reject the true duplicate.

    Raises :class:`ConflictError` with a user-friendly message and the
    existing face_id in ``context`` so the UI can pivot to the existing
    detection.
    """
    from bpp.scoring.face_embed import _bbox_iou

    existing = conn.execute(
        "SELECT id, bbox_x, bbox_y, bbox_w, bbox_h, embedding, cluster_id"
        " FROM face_embeddings WHERE photo_id=?",
        (photo_id,),
    ).fetchall()
    for r in existing:
        ex_bbox = (r["bbox_x"], r["bbox_y"], r["bbox_w"], r["bbox_h"])
        iou = _bbox_iou(ex_bbox, tuple(int(v) for v in new_bbox[:4]))
        if iou < IOU_DUP_THRESH:
            continue
        # Protection A: a corrupt existing row can't tell us anything
        # useful about duplicates — skip it (safer than crashing the
        # user's drag-to-create flow on a single bad neighbor).
        ex_emb = decode_embedding(
            r["embedding"],
            where="face_create_helpers.dup_guard",
        )
        if ex_emb is None:
            continue
        if float(np.linalg.norm(new_emb - ex_emb)) >= emb_thresh:
            continue

        # Both signals agree — refuse, with the existing person's name
        # if any.
        ex_cid = int(r["cluster_id"])
        existing_label = None
        if ex_cid >= 0:
            label_row = conn.execute(
                "SELECT name FROM albums WHERE album_type='smart_person' "
                "AND smart_person_cluster_id = ?",
                (ex_cid,),
            ).fetchone()
            existing_label = label_row["name"] if label_row else None
        if existing_label:
            msg = (
                f"This face is already tagged as “{existing_label}”."
                f" Use Reassign if it's the wrong person."
            )
        elif ex_cid >= 0:
            msg = (
                "This face is already tagged for an unnamed person."
                " Use Label/Reassign on the existing detection instead."
            )
        else:
            msg = (
                "This face is already detected (unassigned)."
                " Label the existing detection instead of adding a new one."
            )
        raise ConflictError(msg, duplicate_face_id=r["id"])


def insert_face_and_optional_album(
    conn: Any,
    *,
    photo_id: int,
    new_bbox: Any,
    new_emb: Any,
    new_quality: float,
    cluster_id: int | None,
    new_person_name: str | None,
    extraction_max_long_side: int | None = None,
) -> tuple[int, int, int]:
    """Allocate face_index, INSERT the face_embeddings row, and (when
    minting a new person) create the smart_person album. Returns
    ``(face_id, next_idx, resolved_cluster_id)``.

    Caller is responsible for the final ``conn.commit()`` so this
    helper can be wrapped in a SAVEPOINT by future call sites without
    forcing a behaviour change here.

    ``extraction_max_long_side`` is the detector-input size that
    produced ``new_bbox`` (v40 / Bug #9 hardening). Should always be
    passed by new callers; default None preserves the contract for
    pre-v40 test fixtures.
    """
    from bpp.db.smart_albums import ALBUM_PERSON, _ensure_smart_album

    # New-person branch: allocate the next cluster_id from the
    # high-water mark. with_face_lock serializes writers, so this
    # is race-safe.
    if new_person_name is not None:
        max_cid_row = conn.execute(
            "SELECT MAX(cluster_id) AS m FROM face_embeddings WHERE cluster_id >= 0"
        ).fetchone()
        cluster_id = (max_cid_row["m"] + 1) if max_cid_row and max_cid_row["m"] is not None else 0
    assert cluster_id is not None  # invariant: branch above OR parse fills it

    # Allocate the next face_index for this photo.
    max_row = conn.execute(
        "SELECT MAX(face_index) AS m FROM face_embeddings WHERE photo_id=?",
        (photo_id,),
    ).fetchone()
    next_idx = (max_row["m"] + 1) if max_row and max_row["m"] is not None else 0

    cursor = conn.execute(
        "INSERT INTO face_embeddings "
        "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
        " embedding, quality, cluster_id, extraction_max_long_side) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            photo_id,
            next_idx,
            int(new_bbox[0]),
            int(new_bbox[1]),
            int(new_bbox[2]),
            int(new_bbox[3]),
            new_emb.tobytes(),
            new_quality,
            cluster_id,
            extraction_max_long_side,
        ),
    )
    face_id = cursor.lastrowid

    # New-person branch: create the smart_person album BEFORE commit
    # so the face row + named album land atomically.
    if new_person_name is not None:
        _ensure_smart_album(
            conn,
            name=new_person_name,
            album_type=ALBUM_PERSON,
            rule={"cluster_id": cluster_id},
            photo_ids=[photo_id],
        )

    return face_id, next_idx, cluster_id
