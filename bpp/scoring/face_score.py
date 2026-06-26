"""Face-quality scoring — area / center / edge / count / expression.

Pure quality scorer with no detector internals. Optionally invokes
``detect_faces`` when caller didn't pre-compute bounding boxes, and
``_score_expression`` from face_expression for the blink/smile/frontality
sub-score.

Extracted from ``bpp.scoring.face`` during the v0.1 cleanup.
Re-exported from ``bpp.scoring.face`` for back-compat.
"""

from __future__ import annotations

import numpy as np

from bpp.constants import (
    FACE_AREA_MULTIPLIER,
    FACE_COUNT_PENALTY,
    FACE_EDGE_MULTIPLIER,
    FACE_SCORE_AREA_W,
    FACE_SCORE_CENTER_W,
    FACE_SCORE_COUNT_W,
    FACE_SCORE_EDGE_W,
    FACE_SCORE_EXPRESSION_W,
    MIN_FACE_AREA_FRAC,
)
from bpp.scoring.face_expression import _score_expression
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def score_face(
    image: np.ndarray,
    *,
    min_confidence: float = 0.3,
    min_face_area_frac: float = MIN_FACE_AREA_FRAC,
    faces: list[tuple[int, int, int, int]] | None = None,
    model_toggles: dict[str, bool] | None = None,
) -> dict[str, float]:
    """Score face quality. Returns dict with sub-scores and overall face_score.

    If *faces* is provided, skip detection and use the given bounding boxes.
    *model_toggles* can disable expression scoring (``model_face_landmarker``).
    """
    mt = model_toggles or {}
    h, w = image.shape[:2]
    frame_area = h * w

    if faces is None:
        # Import lazily to avoid a circular import (face.py imports score_face).
        from bpp.scoring.face import detect_faces

        faces = detect_faces(
            image,
            min_confidence=min_confidence,
            min_face_area_frac=min_face_area_frac,
            model_toggles=mt,
        )

    if not faces:
        return {
            "face_score": 0.2,
            "face_count": 0,
            "largest_face_ratio": 0.0,
            "face_center_dist": 1.0,
            "face_edge_penalty": 0.0,
            "expression_score": 0.0,
        }

    # Largest face
    areas = [fw * fh for (_, _, fw, fh) in faces]
    largest_idx = int(np.argmax(areas))
    fx, fy, fw, fh = faces[largest_idx]

    # Face area ratio (larger face = better framing, up to a point)
    face_ratio = areas[largest_idx] / frame_area
    area_score = min(1.0, face_ratio * FACE_AREA_MULTIPLIER)

    # Face center distance from frame center
    face_cx = fx + fw / 2
    face_cy = fy + fh / 2
    frame_cx = w / 2
    frame_cy = h / 2
    max_dist = ((w / 2) ** 2 + (h / 2) ** 2) ** 0.5
    center_dist = ((face_cx - frame_cx) ** 2 + (face_cy - frame_cy) ** 2) ** 0.5
    center_score = 1.0 - min(1.0, center_dist / max_dist)

    # Edge penalty: faces too close to edges
    margin_x = min(fx, w - (fx + fw)) / w
    margin_y = min(fy, h - (fy + fh)) / h
    min_margin = min(margin_x, margin_y)
    edge_score = min(1.0, min_margin * FACE_EDGE_MULTIPLIER)

    # Count bonus: 1 face ideal, mild penalty for 0 or many
    count_score = (
        1.0 if len(faces) == 1 else max(0.5, 1.0 - FACE_COUNT_PENALTY * abs(len(faces) - 1))
    )

    # Expression quality: blink/smile/frontality from FaceLandmarker
    if mt.get("model_face_landmarker", True):
        expression_score = _score_expression(image)
    else:
        log.debug("FaceLandmarker skipped (disabled)")
        expression_score = 0.5

    overall = (
        FACE_SCORE_AREA_W * area_score
        + FACE_SCORE_CENTER_W * center_score
        + FACE_SCORE_EDGE_W * edge_score
        + FACE_SCORE_COUNT_W * count_score
        + FACE_SCORE_EXPRESSION_W * expression_score
    )

    return {
        "face_score": float(max(0.0, min(1.0, overall))),
        "face_count": len(faces),
        "largest_face_ratio": float(face_ratio),
        "face_center_dist": float(center_dist / max_dist),
        "face_edge_penalty": float(1.0 - edge_score),
        "expression_score": float(expression_score),
    }
