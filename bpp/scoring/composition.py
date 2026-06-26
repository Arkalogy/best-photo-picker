"""Composition scoring based on face placement and subject segmentation."""

from __future__ import annotations

import numpy as np

from bpp.utils.logging import get_logger

log = get_logger(__name__)


def score_composition(
    image: np.ndarray,
    face_boxes: list[tuple[int, int, int, int]],
    *,
    model_toggles: dict[str, bool] | None = None,
) -> float:
    """Score composition based on face placement relative to frame.

    Uses rule-of-thirds heuristic for face positioning.
    Falls back to selfie segmentation when no faces are detected
    (unless ``model_segmentation`` is disabled via *model_toggles*).
    Returns score 0..1.
    """
    mt = model_toggles or {}
    h, w = image.shape[:2]

    if not face_boxes:
        # Try subject-based composition via selfie segmentation
        if mt.get("model_segmentation", True):
            try:
                from bpp.scoring.segmentation import score_subject_composition

                subject_score = score_subject_composition(image)
                if subject_score is not None:
                    return subject_score
            except Exception as exc:
                log.debug("Segmentation fallback failed: %s", exc)
        else:
            log.debug("Segmentation skipped (disabled)")
        return 0.5

    # Use the largest face
    areas = [fw * fh for (_, _, fw, fh) in face_boxes]
    largest_idx = int(np.argmax(areas))
    fx, fy, fw, fh = face_boxes[largest_idx]

    face_cx = (fx + fw / 2) / w
    face_cy = (fy + fh / 2) / h

    # Rule of thirds: ideal positions are at 1/3 or 2/3
    thirds_x = [1 / 3, 1 / 2, 2 / 3]
    thirds_y = [1 / 3, 2 / 5, 1 / 2]  # Slightly higher is often better for faces

    min_dx = min(abs(face_cx - t) for t in thirds_x)
    min_dy = min(abs(face_cy - t) for t in thirds_y)

    # Score: closer to ideal thirds = better
    x_score = 1.0 - min(1.0, min_dx * 4.0)
    y_score = 1.0 - min(1.0, min_dy * 4.0)

    # Head room: top of face shouldn't be too close to top edge
    face_top = fy / h
    headroom_score = min(1.0, face_top * 10.0) if face_top < 0.1 else 1.0

    score = 0.4 * x_score + 0.4 * y_score + 0.2 * headroom_score
    return float(max(0.0, min(1.0, score)))
