"""MediaPipe BlazeFace (short-range) face detector.

Secondary detector run alongside YuNet. Tuned for selfie / front-camera
distances (under 2m) where it tends to catch profiles YuNet misses.

Extracted from ``bpp.scoring.face`` during the v0.1 cleanup.
Re-exported from ``bpp.scoring.face`` for back-compat.
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

_BUNDLED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scoring", "models")
_MODEL_DIR = str(_models_dir())
# ── Model: MediaPipe BlazeFace (short-range) ───────────────────────
# What:   secondary face detector, run alongside YuNet. Tuned for
#         selfie / front-camera distances (under 2m) where it tends
#         to catch profiles YuNet misses. Outputs bounding boxes +
#         keypoints in MediaPipe's .task format.
# Where:  Google's official MediaPipe model storage bucket.
# Why this one: Float16-quantised short-range model is the smallest
#         BlazeFace variant Google ships; balances accuracy vs the
#         per-photo CPU budget when paired with YuNet.
# License: Apache 2.0 (MediaPipe).
# Pinned:  via /float16/latest/ — Google guarantees this URL
#         resolves to whatever the current "latest" build is. SHA
#         pin still detects when Google rotates the artifact.
# Full-range counterpart: the long-distance BlazeFace variant lives
#         in bpp/scoring/face_blazeface_fr.py — Google ships it from
#         a different bucket (`mediapipe-assets/`) under the
#         `_sparse` filename, not the `_detector/blaze_face_full_range/`
#         path that historically 404'd.
_MODEL_PATH = os.path.join(_MODEL_DIR, "blaze_face_short_range.tflite")
_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_detector/blaze_face_short_range/float16/latest/"
    "blaze_face_short_range.tflite"
)
_MODEL_SHA256 = "b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f"


def _create_mediapipe_detector(path: Path | None):
    import mediapipe as mp

    options = mp.tasks.vision.FaceDetectorOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(path)),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        min_detection_confidence=0.1,
    )
    return mp.tasks.vision.FaceDetector.create_from_options(options)


_mp_blazeface = ModelSingleton(
    name="BlazeFace short-range",
    model_path=Path(_MODEL_PATH),
    model_url=_MODEL_URL,
    model_sha256=_MODEL_SHA256,
    create_fn=_create_mediapipe_detector,
    registry_id=None,  # ancillary mediapipe BlazeFace, no licensing concern
    import_check=lambda: __import__("mediapipe"),
    bundled_path=os.path.join(_BUNDLED_DIR, "blaze_face_short_range.tflite"),
)
# Inference lock: MediaPipe detectors are NOT thread-safe — serialize .detect() calls.
_mp_lock = threading.Lock()


def _get_mediapipe_detector():
    return _mp_blazeface.get()


def ensure_mediapipe_models() -> list[str]:
    """Pre-download BlazeFace models. Returns list of warnings for unavailable models."""
    warnings = []
    path = _mp_blazeface.ensure_model()
    if path is None:
        warnings.append("BlazeFace short-range model unavailable — face detection degraded")
    return warnings


def _mediapipe_detect(
    image: np.ndarray,
    min_confidence: float = 0.2,
) -> list[tuple[int, int, int, int, float]]:
    """Detect faces using MediaPipe BlazeFace. Returns (x, y, w, h, confidence)."""
    import mediapipe as mp

    detector = _get_mediapipe_detector()
    if detector is None:
        return []
    # MediaPipe expects 3-channel RGB
    if len(image.shape) == 2:
        rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    # MediaPipe detectors are NOT thread-safe — serialize .detect() calls.
    with _mp_lock:
        results = detector.detect(mp_image)
    if not results.detections:
        return []
    faces = []
    for d in results.detections:
        score = d.categories[0].score if d.categories else 0.0
        if score < min_confidence:
            continue
        bb = d.bounding_box
        faces.append((bb.origin_x, bb.origin_y, bb.width, bb.height, score))
    return faces
