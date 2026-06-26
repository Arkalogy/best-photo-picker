"""Hand-as-face false positive suppression.

The face detection pipeline runs MediaPipe HandLandmarker against
the same image and drops any face bbox that overlaps a detected
hand by more than ``_HAND_FACE_IOU_THRESH``. This catches the
common case where a hand obscures or is mistaken for a face by
SCRFD / YuNet / BlazeFace.

This module owns:
  - the HandLandmarker model singleton + download URL
  - hand bbox extraction from landmarks
  - the IoU helper
  - the face-list filter

Re-exported from bpp.scoring.face so existing call sites and tests
that import ``_iou`` / ``_suppress_hand_faces`` / ``_detect_hand_bboxes``
keep working unchanged.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import cv2
import numpy as np

from bpp.scoring.model_base import ModelSingleton
from bpp.utils.logging import get_logger
from bpp.utils.paths import models_dir as _models_dir

log = get_logger(__name__)


# ── Constants + singleton ────────────────────────────────────────────

_MODEL_DIR = str(_models_dir())
# ── Model: MediaPipe Hand Landmarker ───────────────────────────────
# What:   detects hands and 21 keypoints per hand. bpp doesn't track
#         hand poses — it uses the bbox to *suppress* face detections
#         when a hand is in front of the face (people gesturing, peace
#         signs covering chin, etc.) which would otherwise produce a
#         spurious face cluster.
# Where:  Google's official MediaPipe model storage bucket.
# Why this one: float16 quantised "latest" lite hand landmarker —
#         MediaPipe ships heavier models for AR/skeletal tracking
#         that bpp doesn't need.
# License: Apache 2.0 (MediaPipe).
# To bump: Google's `/latest/` pattern means the URL stays stable
#         across releases. SHA pin still detects when the artifact
#         is rotated.
_HAND_MODEL_PATH = os.path.join(_MODEL_DIR, "hand_landmarker.task")
_HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/latest/"
    "hand_landmarker.task"
)
_HAND_MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"

# IoU threshold above which a face bbox is suppressed by a hand bbox
_HAND_FACE_IOU_THRESH = 0.30


def _create_hand_detector(path: Path | None):
    import mediapipe as mp

    options = mp.tasks.vision.HandLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(
            model_asset_path=str(path),
        ),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        num_hands=4,
        min_hand_detection_confidence=0.3,
    )
    return mp.tasks.vision.HandLandmarker.create_from_options(options)


_hand_landmarker = ModelSingleton(
    name="HandLandmarker",
    model_path=Path(_HAND_MODEL_PATH),
    model_url=_HAND_MODEL_URL,
    model_sha256=_HAND_MODEL_SHA256,
    create_fn=_create_hand_detector,
    registry_id=None,  # ancillary mediapipe model, no licensing concern
    import_check=lambda: __import__("mediapipe"),
)
# Inference lock: MediaPipe detectors are NOT thread-safe — serialize .detect() calls.
_hand_lock = threading.Lock()


def _get_hand_detector():
    """Get or create the HandLandmarker singleton (thread-safe)."""
    return _hand_landmarker.get()


# ── Public-ish API (used from face.py and the test suite) ────────────


def _detect_hand_bboxes(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Detect hands and return bounding boxes as ``(x, y, w, h)``.

    Computes bboxes from the 21 hand landmarks. Returns an empty list
    if HandLandmarker is unavailable or no hands are found.
    """
    detector = _get_hand_detector()
    if detector is None:
        return []

    import mediapipe as mp

    if len(image.shape) == 2:
        rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    h, w = rgb.shape[:2]
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    try:
        # MediaPipe detectors are NOT thread-safe — serialize .detect() calls.
        with _hand_lock:
            result = detector.detect(mp_image)
    except Exception:
        return []

    if not result.hand_landmarks:
        return []

    bboxes = []
    for hand_lms in result.hand_landmarks:
        xs = [lm.x * w for lm in hand_lms]
        ys = [lm.y * h for lm in hand_lms]
        x_min, x_max = int(min(xs)), int(max(xs))
        y_min, y_max = int(min(ys)), int(max(ys))
        bboxes.append((x_min, y_min, x_max - x_min, y_max - y_min))

    return bboxes


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Compute Intersection-over-Union between two ``(x, y, w, h)`` boxes."""
    ax1, ay1 = a[0], a[1]
    ax2, ay2 = a[0] + a[2], a[1] + a[3]
    bx1, by1 = b[0], b[1]
    bx2, by2 = b[0] + b[2], b[1] + b[3]

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0

    inter = (ix2 - ix1) * (iy2 - iy1)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _suppress_hand_faces(
    faces: list[tuple[int, int, int, int, float]],
    image: np.ndarray,
) -> list[tuple[int, int, int, int, float]]:
    """Remove face detections that overlap significantly with detected hands.

    Only runs hand detection when there are face detections to filter.
    Returns the filtered face list (may be unchanged when no hands are
    found).
    """
    if not faces:
        return faces

    hand_bboxes = _detect_hand_bboxes(image)
    if not hand_bboxes:
        return faces

    kept = []
    for face in faces:
        face_box = (face[0], face[1], face[2], face[3])
        is_hand = any(_iou(face_box, hb) >= _HAND_FACE_IOU_THRESH for hb in hand_bboxes)
        if is_hand:
            log.debug(
                "Suppressed hand-as-face FP at (%d,%d,%d,%d) conf=%.2f",
                *face,
            )
        else:
            kept.append(face)
    return kept


# ── Registry ───────────────────────────────────────────────────────
from bpp.scoring.model_base import ModelEntry, ModelRegistry  # noqa: E402

ModelRegistry.register(
    ModelEntry(
        name="HandLandmarker",
        path=_HAND_MODEL_PATH,
        url=_HAND_MODEL_URL,
        sha256=_HAND_MODEL_SHA256,
        reset=_hand_landmarker.reset,
    )
)
