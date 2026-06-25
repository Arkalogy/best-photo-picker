"""Landmark validation + quality scoring for face embeddings.

Extracted from :mod:`bpp.scoring.face_embed` as part of the 500-LOC
cap enforcement. Two related concerns live here:

* Geometric validation of detected landmarks (dlib 4-point style
  and YuNet 5-point style) — used to reject false-positive
  detections (blankets, torsos, etc.) before they corrupt
  clustering.
* Per-face quality scoring (symmetry / size / blur / detector
  confidence) — used to weight embeddings during clustering and
  filter low-quality detections.

face_embed re-exports every name defined here so existing callers
continue to import via the original module path.
"""

from __future__ import annotations

import cv2
import numpy as np

from bpp.scoring.face_embed import (
    MAX_FACE_ASPECT,
    MIN_FACE_ASPECT,
    MIN_FACE_PX,
    MIN_LANDMARK_SPREAD,
)
from bpp.utils.logging import get_logger

log = get_logger(__name__)


# ── Landmark validation (used by dlib fallback) ──


def _validate_face_landmarks(
    landmarks: dict,
    bbox: tuple[int, int, int, int],
) -> bool:
    """Validate that detected landmarks are geometrically consistent with a face.

    Uses the 'small' model landmarks: nose_tip (1 point), left_eye (2 points),
    right_eye (2 points).  Checks:
    1. Landmark spread — points must span a reasonable fraction of the bbox
    2. Eyes above nose — the average eye Y must be above (less than) the nose Y
    3. Aspect ratio — bbox must be roughly square
    """
    _x, _y, w, h = bbox

    # Aspect ratio check
    aspect = w / h if h > 0 else 0
    if aspect < MIN_FACE_ASPECT or aspect > MAX_FACE_ASPECT:
        return False

    nose_pts = landmarks.get("nose_tip", [])
    left_eye_pts = landmarks.get("left_eye", [])
    right_eye_pts = landmarks.get("right_eye", [])

    if not nose_pts or not left_eye_pts or not right_eye_pts:
        return False

    all_pts = nose_pts + left_eye_pts + right_eye_pts
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]

    # Spread check: landmarks must span a meaningful portion of the bbox
    diag = (w**2 + h**2) ** 0.5
    spread = ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5
    if diag > 0 and spread / diag < MIN_LANDMARK_SPREAD:
        return False

    # Eyes-above-nose check (Y increases downward in images)
    eye_y = (sum(p[1] for p in left_eye_pts) + sum(p[1] for p in right_eye_pts)) / (
        len(left_eye_pts) + len(right_eye_pts)
    )
    nose_y = sum(p[1] for p in nose_pts) / len(nose_pts)

    # Allow some tolerance — rotated/tilted faces may have small differences.
    # Require nose to be at least slightly below eyes (tolerance: 5% of bbox height).
    return not (nose_y < eye_y - h * 0.05)


# ── YuNet landmark validation ──


def _validate_yunet_landmarks(face: np.ndarray) -> bool:
    """Validate that YuNet's 5-point landmarks are geometrically consistent.

    YuNet raw row layout: [x, y, w, h,
        right_eye_x(4), right_eye_y(5), left_eye_x(6), left_eye_y(7),
        nose_x(8), nose_y(9), mouth_right_x(10), mouth_right_y(11),
        mouth_left_x(12), mouth_left_y(13), confidence(14)]

    Checks:
    1. Aspect ratio — bbox width/height within plausible face range
    2. Landmark spread — points span a meaningful portion of the bbox
    3. Eyes above nose — average eye Y < nose Y (Y increases downward)
    4. Nose above mouth — nose Y < average mouth Y
    5. Inter-eye distance — 20-80% of face width (rejects hands, toys)
    """
    fw, fh = float(face[2]), float(face[3])
    if fw <= 0 or fh <= 0:
        return False

    # 1. Aspect ratio
    aspect = fw / fh
    if aspect < MIN_FACE_ASPECT or aspect > MAX_FACE_ASPECT:
        return False

    # 2. Min face size
    if fw < MIN_FACE_PX or fh < MIN_FACE_PX:
        return False

    # Extract landmarks
    r_eye = (float(face[4]), float(face[5]))
    l_eye = (float(face[6]), float(face[7]))
    nose = (float(face[8]), float(face[9]))
    r_mouth = (float(face[10]), float(face[11]))
    l_mouth = (float(face[12]), float(face[13]))

    all_pts = [r_eye, l_eye, nose, r_mouth, l_mouth]
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]

    # 3. Landmark spread
    diag = (fw**2 + fh**2) ** 0.5
    spread = ((max(xs) - min(xs)) ** 2 + (max(ys) - min(ys)) ** 2) ** 0.5
    if diag > 0 and spread / diag < MIN_LANDMARK_SPREAD:
        return False

    # 4. Eyes above nose (with 5% tolerance for tilted faces)
    eye_y = (r_eye[1] + l_eye[1]) / 2
    if nose[1] < eye_y - fh * 0.05:
        return False

    # 5. Nose above mouth (with tolerance)
    mouth_y = (r_mouth[1] + l_mouth[1]) / 2
    if mouth_y < nose[1] - fh * 0.05:
        return False

    # 6. Inter-eye distance sanity (0.10 accommodates babies' closer-set eyes)
    eye_dist = ((r_eye[0] - l_eye[0]) ** 2 + (r_eye[1] - l_eye[1]) ** 2) ** 0.5
    eye_ratio = eye_dist / fw
    return not (eye_ratio < 0.10 or eye_ratio > 0.85)


def _blur_score(image: np.ndarray, bbox: tuple[int, int, int, int]) -> float:
    """Score face crop sharpness 0.0-1.0 via Laplacian variance.

    Blurry faces produce noisy embeddings that degrade clustering.
    The Laplacian highlights edges; its variance is low on blurry crops.

    Returns 0.0 (very blurry) to 1.0 (sharp).  The sigmoid mapping
    is calibrated so typical phone-camera faces score 0.6-1.0 and
    motion-blurred / defocused faces score 0.1-0.4.
    """
    x, y, w, h = bbox
    ih, iw = image.shape[:2]
    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(iw, x + w)
    y2 = min(ih, y + h)
    if x2 <= x1 or y2 <= y1:
        return 0.5  # can't crop — neutral score

    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        return 0.5

    if len(crop.shape) == 3:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    lap_var = cv2.Laplacian(crop, cv2.CV_64F).var()

    # Sigmoid mapping: midpoint at 100, steepness 0.03
    # lap_var ~20 → 0.08, ~50 → 0.18, ~100 → 0.50, ~200 → 0.95
    import math

    return 1.0 / (1.0 + math.exp(-0.03 * (lap_var - 100)))


def _face_quality_from_landmarks(
    face: np.ndarray,
    image: np.ndarray | None = None,
) -> float:
    """Score face quality 0.0-1.0 from YuNet's 5-point landmarks + sharpness.

    Components:
    - Frontality (0.4 weight): symmetry of eye-to-nose distances
    - Size (0.25 weight): larger faces produce better embeddings
    - Confidence (0.15 weight): detector confidence
    - Sharpness (0.2 weight): Laplacian blur detection on the crop
    """
    fw, fh = float(face[2]), float(face[3])
    conf = float(face[-1])

    r_eye = np.array([float(face[4]), float(face[5])])
    l_eye = np.array([float(face[6]), float(face[7])])
    nose = np.array([float(face[8]), float(face[9])])

    # Frontality: symmetric eye-nose distances mean facing camera
    left_dist = float(np.linalg.norm(l_eye - nose))
    right_dist = float(np.linalg.norm(r_eye - nose))
    max_d = max(left_dist, right_dist, 1e-6)
    symmetry = 1.0 - abs(left_dist - right_dist) / max_d

    # Size: 112px is the SFace alignment target, faces near that size
    # produce the best embeddings
    face_size = max(fw, fh)
    size_score = min(face_size / 112.0, 1.0)

    fx, fy = int(face[0]), int(face[1])
    if image is not None:
        blur = _blur_score(image, (fx, fy, int(fw), int(fh)))
        return symmetry * 0.4 + size_score * 0.25 + conf * 0.15 + blur * 0.2

    return symmetry * 0.5 + size_score * 0.3 + conf * 0.2
