"""Facial-expression quality scoring via MediaPipe FaceLandmarker.

Owns the FaceLandmarker model singleton, the blendshape-name
constants, and the scoring function that turns blendshapes into a
[0, 1] expression-quality score (blink penalty + smile bonus +
frontality bonus).

Re-exported from bpp.scoring.face so existing imports
(``from bpp.scoring.face import _score_expression`` and friends)
continue to work.
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
# ── Model: MediaPipe Face Landmarker ───────────────────────────────
# What:   per-face 478 3D mesh points + 52 ARKit blendshape scores
#         (smile, eyes_blink, brow_raise, etc.). bpp uses the
#         blendshape outputs to score expression quality — a photo
#         with everyone smiling and eyes open scores higher than the
#         same composition with one person mid-blink.
# Where:  Google's official MediaPipe model storage bucket.
# Why this one: float16 "latest" — Google ships this as the standard
#         general-purpose face mesh model, no smaller variant.
# License: Apache 2.0 (MediaPipe).
# To bump: same `/latest/` URL pattern — refresh SHA when rotated.
#         Blendshape index ordering is stable across releases so the
#         caller-side `_BLENDSHAPE_*` constants stay valid.
_LANDMARKER_PATH = os.path.join(_MODEL_DIR, "face_landmarker.task")
_LANDMARKER_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "face_landmarker/face_landmarker/float16/latest/"
    "face_landmarker.task"
)
_LANDMARKER_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"


def _create_landmarker(path: Path | None):
    import mediapipe as mp

    options = mp.tasks.vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(
            model_asset_path=str(path),
        ),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        output_face_blendshapes=True,
        num_faces=10,
        min_face_detection_confidence=0.1,
        min_face_presence_confidence=0.1,
    )
    return mp.tasks.vision.FaceLandmarker.create_from_options(options)


_face_landmarker = ModelSingleton(
    name="FaceLandmarker",
    model_path=Path(_LANDMARKER_PATH),
    model_url=_LANDMARKER_URL,
    model_sha256=_LANDMARKER_SHA256,
    create_fn=_create_landmarker,
    registry_id=None,  # ancillary mediapipe model, no licensing concern
    import_check=lambda: __import__("mediapipe"),
)
# Inference lock: MediaPipe detectors are NOT thread-safe — serialize .detect() calls.
_landmarker_lock = threading.Lock()


def _get_landmarker():
    """Get or create the FaceLandmarker singleton (thread-safe)."""
    return _face_landmarker.get()


# ── Blendshape names + thresholds ────────────────────────────────────

_BLINK_LEFT = "eyeBlinkLeft"
_BLINK_RIGHT = "eyeBlinkRight"
_SMILE_LEFT = "mouthSmileLeft"
_SMILE_RIGHT = "mouthSmileRight"
# Frontality proxy: jaw asymmetry indicates head rotation off-axis.
_JAW_LEFT = "jawLeft"
_JAW_RIGHT = "jawRight"

_BLINK_THRESH = 0.50  # above this = eyes considered closed
_SMILE_FULL = 0.60  # above this = full smile (max boost)


# ── Scoring ──────────────────────────────────────────────────────────


def _score_expression(image: np.ndarray) -> float:
    """Score facial expression quality using FaceLandmarker blendshapes.

    Returns a value in ``[0.0, 1.0]``:
      - 0.0 = severe blink, bad expression
      - 0.5 = neutral (no landmarker available, or neutral expression)
      - 1.0 = smiling, eyes open, facing camera

    Falls back to 0.5 (neutral) when FaceLandmarker is unavailable.
    """
    landmarker = _get_landmarker()
    if landmarker is None:
        return 0.5

    import mediapipe as mp

    # FaceLandmarker expects RGB
    if len(image.shape) == 2:
        rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    try:
        # MediaPipe detectors are NOT thread-safe — serialize .detect() calls.
        with _landmarker_lock:
            result = landmarker.detect(mp_image)
    except Exception:
        return 0.5

    if not result.face_blendshapes:
        return 0.5

    # Score each face individually, return score for the best face
    best_score = 0.0
    for face_shapes in result.face_blendshapes:
        # Build lookup: name → score
        bs = {s.category_name: s.score for s in face_shapes}

        # Blink penalty: average of both eyes, higher = more closed
        blink_l = bs.get(_BLINK_LEFT, 0.0)
        blink_r = bs.get(_BLINK_RIGHT, 0.0)
        blink_avg = (blink_l + blink_r) / 2.0
        # 1.0 when eyes fully open, 0.0 when both fully closed
        if blink_avg >= _BLINK_THRESH:
            blink_score = max(0.0, 1.0 - (blink_avg - _BLINK_THRESH) / (1.0 - _BLINK_THRESH))
        else:
            blink_score = 1.0

        # Smile boost: average of both mouth corners
        smile_l = bs.get(_SMILE_LEFT, 0.0)
        smile_r = bs.get(_SMILE_RIGHT, 0.0)
        smile_avg = (smile_l + smile_r) / 2.0
        # 0.5 neutral, ramps up to 1.0 at _SMILE_FULL
        smile_score = 0.5 + 0.5 * min(1.0, smile_avg / _SMILE_FULL)

        # Frontality: low jaw asymmetry = facing camera
        jaw_l = bs.get(_JAW_LEFT, 0.0)
        jaw_r = bs.get(_JAW_RIGHT, 0.0)
        jaw_asym = abs(jaw_l - jaw_r)
        # 1.0 when perfectly frontal, drops with asymmetry
        frontal_score = max(0.0, 1.0 - jaw_asym * 2.0)

        # Combine: blink is most important (a bad blink ruins the photo)
        face_expr = 0.50 * blink_score + 0.30 * smile_score + 0.20 * frontal_score
        best_score = max(best_score, face_expr)

    return best_score


# ── Registry ───────────────────────────────────────────────────────
from bpp.scoring.model_base import ModelEntry, ModelRegistry  # noqa: E402

ModelRegistry.register(
    ModelEntry(
        name="FaceLandmarker",
        path=_LANDMARKER_PATH,
        url=_LANDMARKER_URL,
        sha256=_LANDMARKER_SHA256,
        reset=_face_landmarker.reset,
    )
)
