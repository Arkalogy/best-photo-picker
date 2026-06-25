"""Blur / sharpness scoring using variance of Laplacian.

Face-adaptive weighting: the blend between face-region and global
sharpness is proportional to how much of the frame the faces occupy.
Close-up portrait (face=15%) → 75% face weight.
Group shot (face=3%) → 15% face weight.
No faces → 100% global.
"""

from __future__ import annotations

import cv2
import numpy as np

# Scale factor: face_weight = clamp(total_face_coverage * K, 0, 1).
# K=5 means faces covering ≥20% of the frame → 100% face weight.
_FACE_COVERAGE_SCALE = 5.0
# Pad face bbox by this fraction to include surrounding context.
_FACE_PAD = 0.25


def compute_laplacian_variance(image: np.ndarray) -> float:
    """Compute variance of Laplacian on a grayscale image.

    Higher value = sharper image.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _face_region_laplacian(
    image: np.ndarray,
    faces: list[tuple[int, int, int, int]],
) -> float:
    """Compute max Laplacian variance across face crops (padded).

    Uses the sharpest face — if the main subject is in focus, that's
    what matters even if secondary faces are soft.
    """
    h, w = image.shape[:2]
    best = 0.0
    for fx, fy, fw, fh in faces:
        pad_x = int(fw * _FACE_PAD)
        pad_y = int(fh * _FACE_PAD)
        x1 = max(0, fx - pad_x)
        y1 = max(0, fy - pad_y)
        x2 = min(w, fx + fw + pad_x)
        y2 = min(h, fy + fh + pad_y)
        if x2 - x1 < 10 or y2 - y1 < 10:
            continue
        crop = image[y1:y2, x1:x2]
        val = compute_laplacian_variance(crop)
        best = max(best, val)
    return best


def _face_coverage(
    image: np.ndarray,
    faces: list[tuple[int, int, int, int]],
) -> float:
    """Total face area as fraction of image area."""
    img_area = image.shape[0] * image.shape[1]
    if img_area == 0:
        return 0.0
    face_area = sum(fw * fh for _, _, fw, fh in faces)
    return face_area / img_area


def score_blur_raw(
    image: np.ndarray,
    faces: list[tuple[int, int, int, int]] | None = None,
) -> float:
    """Return raw Laplacian variance, face-weighted when faces are present.

    The blend weight adapts to how much of the frame the faces occupy:
    face_weight = clamp(face_coverage * K, 0, 1). A close-up portrait
    with 15% face coverage gets 75% weight on face sharpness; a group
    shot with 3% coverage gets only 15%. This naturally matches
    photographer intent — large faces mean shallow DoF is likely
    intentional.
    """
    global_val = compute_laplacian_variance(image)
    if faces:
        face_val = _face_region_laplacian(image, faces)
        if face_val > 0:
            coverage = _face_coverage(image, faces)
            face_w = min(1.0, coverage * _FACE_COVERAGE_SCALE)
            return face_w * face_val + (1.0 - face_w) * global_val
    return global_val
