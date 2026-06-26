"""BlazeFace full-range detector (profiles, angled faces, 2-10m distance).

Uses ai-edge-litert (TFLite runtime) for direct inference. The
legacy MediaPipe BlazeFace full-range model has 2304 anchors which
is incompatible with the Tasks API (expects 896), so we run TFLite
ourselves and decode anchors manually.

face.py owns the orchestration + scoring; this module owns the
FR-specific anchor grid + decode + thread-safe inference.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import cv2
import numpy as np

from bpp.scoring.model_base import ModelSingleton
from bpp.utils.paths import models_dir as _models_dir

_MODEL_DIR = str(_models_dir())

# ── Model: MediaPipe BlazeFace (full-range, sparse) ────────────────
# What:   long-distance face detector — handles back-facing camera
#         shots (profiles, angled, 2-10m subjects) that the short-
#         range BlazeFace in face.py can't see. Pairs with that
#         module to cover the full subject-distance spectrum.
# Where:  Google's mediapipe-assets bucket (different bucket than
#         /mediapipe-models/ used for short-range — Google split
#         the experimental and stable distributions).
# Why this one: "_sparse" variant uses sparse weights for ~30%
#         smaller download and equivalent quality at a slight FPS
#         cost (irrelevant for offline batch scoring).
# License: Apache 2.0 (MediaPipe).
# To bump: short-range URL pattern uses `/face_detector/.../latest/`
#         which Google guarantees to keep stable; full-range uses
#         a flat asset path so a new release would land at a new
#         filename — update path + URL + SHA together.
_FR_MODEL_PATH = os.path.join(_MODEL_DIR, "blaze_face_full_range_sparse.tflite")
_FR_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-assets/face_detection_full_range_sparse.tflite"
)
_FR_MODEL_SHA256 = "2c3728e6da56f21e21a320433396fb06d40d9088f2247c05e5635a688d45dfe1"
_FR_INPUT_SIZE = 192
_FR_STRIDE = 4
_FR_GRID = _FR_INPUT_SIZE // _FR_STRIDE  # 48 → 2304 anchors

_FR_ANCHORS: np.ndarray | None = None


def _get_fr_anchors() -> np.ndarray:
    """Lazily compute and cache the 48x48 anchor grid."""
    global _FR_ANCHORS
    if _FR_ANCHORS is not None:
        return _FR_ANCHORS
    anchors = np.empty((_FR_GRID * _FR_GRID, 2), dtype=np.float32)
    idx = 0
    for row in range(_FR_GRID):
        for col in range(_FR_GRID):
            anchors[idx, 0] = (col + 0.5) * _FR_STRIDE  # cx
            anchors[idx, 1] = (row + 0.5) * _FR_STRIDE  # cy
            idx += 1
    _FR_ANCHORS = anchors
    return anchors


def _create_fr_interpreter(path: Path | None):
    from ai_edge_litert.interpreter import Interpreter

    interp = Interpreter(model_path=str(path))
    interp.allocate_tensors()
    return interp


_fr_detector = ModelSingleton(
    name="BlazeFace full-range",
    model_path=Path(_FR_MODEL_PATH),
    model_url=_FR_MODEL_URL,
    model_sha256=_FR_MODEL_SHA256,
    create_fn=_create_fr_interpreter,
    registry_id=None,  # ancillary BlazeFace, no licensing concern
    import_check=lambda: __import__("ai_edge_litert"),
)
# TFLite interpreters are NOT thread-safe — serialize inference calls.
_fr_lock = threading.Lock()


def _get_fr_interpreter():
    """Get or create the full-range TFLite interpreter (thread-safe)."""
    return _fr_detector.get()


def blazeface_fr_detect(
    image: np.ndarray,
    min_confidence: float = 0.5,
) -> list[tuple[int, int, int, int, float]]:
    """Detect faces with BlazeFace full-range via TFLite.

    Handles profiles, angled faces, and 2-10m distance.
    Returns (x, y, w, h, confidence) in original image coordinates.
    """
    interp = _get_fr_interpreter()
    if interp is None:
        return []

    oh, ow = image.shape[:2]
    # Prepare input: resize to 192x192, convert to RGB float [0, 1]
    if len(image.shape) == 2:
        rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (_FR_INPUT_SIZE, _FR_INPUT_SIZE))
    inp = resized.astype(np.float32) / 255.0
    inp = np.expand_dims(inp, 0)

    # TFLite interpreters are not thread-safe — serialize inference calls
    with _fr_lock:
        input_details = interp.get_input_details()
        output_details = interp.get_output_details()
        # Select outputs by name, not index — TFLite doesn't guarantee order
        # across model re-exports. Names: regressors (boxes), classificators (scores)
        by_name = {o["name"]: o["index"] for o in output_details}
        boxes_idx = by_name.get("regressors", output_details[0]["index"])
        scores_idx = by_name.get("classificators", output_details[1]["index"])
        interp.set_tensor(input_details[0]["index"], inp)
        interp.invoke()

        # boxes shape (1, N, 16); scores shape (1, N, 1). N=2304 for full-range model.
        boxes_raw = interp.get_tensor(boxes_idx)[0].copy()
        scores_raw = interp.get_tensor(scores_idx)[0].flatten().copy()
    scores = 1.0 / (1.0 + np.exp(-np.clip(scores_raw, -50, 50)))

    anchors = _get_fr_anchors()

    # Decode boxes: [y_offset, x_offset, h, w, ...] in input pixel space
    cy = anchors[:, 1] + boxes_raw[:, 0]
    cx = anchors[:, 0] + boxes_raw[:, 1]
    bh = boxes_raw[:, 2]
    bw = boxes_raw[:, 3]

    # Filter by confidence
    mask = scores >= min_confidence
    if not mask.any():
        return []

    # Scale to original image coordinates
    scale_x = ow / _FR_INPUT_SIZE
    scale_y = oh / _FR_INPUT_SIZE

    results = []
    for i in np.where(mask)[0]:
        x = int((cx[i] - bw[i] / 2) * scale_x)
        y = int((cy[i] - bh[i] / 2) * scale_y)
        w = int(bw[i] * scale_x)
        h = int(bh[i] * scale_y)
        # Clamp to image bounds
        x = max(0, x)
        y = max(0, y)
        w = min(w, ow - x)
        h = min(h, oh - y)
        if w > 0 and h > 0:
            results.append((x, y, w, h, float(scores[i])))

    return results


# ── Registry ───────────────────────────────────────────────────────
from bpp.scoring.model_base import ModelEntry, ModelRegistry  # noqa: E402

ModelRegistry.register(
    ModelEntry(
        name="BlazeFace full-range",
        path=_FR_MODEL_PATH,
        url=_FR_MODEL_URL,
        sha256=_FR_MODEL_SHA256,
        reset=_fr_detector.reset,
    )
)
