"""Selfie segmentation for subject-aware composition scoring.

Uses MediaPipe ImageSegmenter to identify the main subject (person)
in a photo, enabling composition scoring even when no face is detected
(e.g., full-body shots, back-turned subjects).
"""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

from bpp.scoring.model_base import ModelSingleton
from bpp.utils.logging import get_logger
from bpp.utils.paths import models_dir as _models_dir

log = get_logger(__name__)

_MODEL_DIR = str(_models_dir())
# ── Model: MediaPipe Selfie Segmenter ──────────────────────────────
# What:   binary mask of "subject (person) vs background". bpp uses
#         the mask to weight composition / sharpness scoring more
#         heavily on the subject than the background, so a sharp
#         subject in front of motion-blurred background scores
#         higher than the inverse.
# Where:  Google's official MediaPipe model storage bucket.
# Why this one: float16 quantised "latest" — small (~1MB), CPU
#         realtime. Despite the name, it works on any human-subject
#         photo, not just selfies.
# License: Apache 2.0 (MediaPipe).
# To bump: same `/latest/` pattern as other MediaPipe models —
#         refresh SHA when Google rotates the artifact.
_SEGMENTER_PATH = os.path.join(_MODEL_DIR, "selfie_segmenter.tflite")
_SEGMENTER_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "image_segmenter/selfie_segmenter/float16/latest/"
    "selfie_segmenter.tflite"
)
_SEGMENTER_SHA256 = "191ac9529ae506ee0beefa6b2c945a172dab9d07d1e802a290a4e4038226658b"


def _create_segmenter(path: Path | None):
    import mediapipe as mp

    options = mp.tasks.vision.ImageSegmenterOptions(
        base_options=mp.tasks.BaseOptions(
            model_asset_path=str(path),
        ),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        output_confidence_masks=True,
    )
    return mp.tasks.vision.ImageSegmenter.create_from_options(options)


_segmenter = ModelSingleton(
    name="Selfie segmenter",
    model_path=Path(_SEGMENTER_PATH),
    model_url=_SEGMENTER_URL,
    model_sha256=_SEGMENTER_SHA256,
    create_fn=_create_segmenter,
    registry_id=None,  # ancillary mediapipe segmenter, no licensing concern
    import_check=lambda: __import__("mediapipe"),
)


def _get_segmenter():
    """Get or create the ImageSegmenter singleton (thread-safe)."""
    return _segmenter.get()


def ensure_segmenter_model() -> list[str]:
    """Pre-download segmenter model. Returns list of warnings."""
    warnings = []
    path = _segmenter.ensure_model()
    if path is None:
        warnings.append("Selfie segmenter unavailable — subject composition scoring disabled")
    return warnings


def segment_subject(image: np.ndarray) -> np.ndarray | None:
    """Segment the main subject (person) from background.

    Returns a confidence mask (float32, 0-1) where 1 = subject, 0 = background.
    Returns None if segmenter is unavailable or no subject found.
    """
    import mediapipe as mp

    segmenter = _get_segmenter()
    if segmenter is None:
        return None

    if len(image.shape) == 2:
        rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    try:
        result = segmenter.segment(mp_image)
    except Exception:
        return None

    if not result.confidence_masks:
        return None

    # First confidence mask is the person mask
    mask = result.confidence_masks[0].numpy_view().copy()
    # Squeeze if needed (may be HxWx1)
    if mask.ndim == 3:
        mask = mask[:, :, 0]

    return mask.astype(np.float32)


def score_subject_composition(image: np.ndarray) -> float | None:
    """Score composition using subject segmentation.

    Returns a composition score in [0, 1] based on:
      - Subject coverage: ideal ~15-60% of frame
      - Subject position: centered or on rule-of-thirds lines
      - Subject compactness: connected subject better than fragmented

    Returns None if segmenter unavailable or no subject found.
    """
    mask = segment_subject(image)
    if mask is None:
        return None

    h, w = mask.shape[:2]
    total_pixels = h * w

    # Subject area ratio
    subject_pixels = float(np.sum(mask > 0.5))
    area_ratio = subject_pixels / total_pixels

    # No meaningful subject found
    if area_ratio < 0.02:
        return None

    # Area score: peaks around 20-50% of frame, drops for tiny or full-frame
    if area_ratio < 0.15:
        area_score = area_ratio / 0.15  # ramp up
    elif area_ratio <= 0.60:
        area_score = 1.0  # sweet spot
    else:
        area_score = max(0.3, 1.0 - (area_ratio - 0.60) / 0.40)  # taper off

    # Subject center position (weighted by confidence)
    ys, xs = np.mgrid[0:h, 0:w]
    weight = mask.clip(0, 1)
    total_w = weight.sum()
    if total_w < 1:
        return None
    cx = float((xs * weight).sum() / total_w) / w
    cy = float((ys * weight).sum() / total_w) / h

    # Rule of thirds positioning
    thirds_x = [1 / 3, 1 / 2, 2 / 3]
    thirds_y = [1 / 3, 2 / 5, 1 / 2]
    min_dx = min(abs(cx - t) for t in thirds_x)
    min_dy = min(abs(cy - t) for t in thirds_y)
    position_score = 1.0 - min(1.0, (min_dx + min_dy) * 3.0)

    return float(max(0.0, min(1.0, 0.5 * area_score + 0.5 * position_score)))


# ── Registry ───────────────────────────────────────────────────────
from bpp.scoring.model_base import ModelEntry, ModelRegistry  # noqa: E402

ModelRegistry.register(
    ModelEntry(
        name="Selfie segmenter",
        path=_SEGMENTER_PATH,
        url=_SEGMENTER_URL,
        sha256=_SEGMENTER_SHA256,
        reset=_segmenter.reset,
    )
)
