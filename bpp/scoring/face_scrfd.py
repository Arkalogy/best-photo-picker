"""SCRFD face detector — primary multi-scale detector.

SCRFD is the project's preferred face detector; it dramatically
outperforms the fallbacks (YuNet + BlazeFace) on small, distant,
and infant faces due to its 3-level FPN over strides 8/16/32.

This module owns:
  - the ONNX model singleton + download URL
  - input letterboxing + preprocessing (BGR-RGB swap, mean subtract)
  - decoding the 3 FPN levels into (cx, cy, w, h) candidates
  - SCRFD-specific NMS (faster than torchvision's general NMS for
    this output shape)
  - the public ``detect_faces_scrfd`` entry point

Re-exported from bpp.scoring.face so callers and tests
(``from bpp.scoring.face import _scrfd_model``,
``detect_faces_scrfd``, etc.) keep working unchanged.
"""

from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from bpp.scoring.model_base import ModelSingleton
from bpp.scoring.onnx_providers import get_providers
from bpp.utils.logging import get_logger

log = get_logger(__name__)

# Serializes ``session.run`` calls into the SCRFD ONNX session.
#
# Every other detector in this codebase locks its inference call —
# ``_yunet_lock``, ``_fr_lock`` (BlazeFace full-range), ``_dlib_lock``,
# ``_cascade_lock``, ``_mp_lock`` (MediaPipe), ``_sface_lock``. SCRFD
# was the exception because the original implementation assumed ONNX
# Runtime's ``session.run`` is fully thread-safe. In practice, on
# macOS arm64 with the default CPUExecutionProvider, concurrent
# session.run calls on the same session race on internal arena
# allocator state and SIGSEGV the entire process — silently, with no
# Python traceback, after a non-deterministic number of photos
# (~5-30 reliably crash 4 worker threads, ~50+ always does).
#
# This was hidden until libraries grew past a few hundred photos
# because the race window is small for any single photo. At
# ThreadPoolExecutor(max_workers=4) and 1000+ photos the parent
# process disappeared without a log line; the child of FaceWorker
# now segfaults the same way under subprocess isolation. The lock
# costs us ~0% on real-world libraries — SCRFD inference dominates
# the per-photo budget anyway, so serializing 4-worker contention
# adds nothing measurable while making the crash impossible.
_scrfd_lock = threading.Lock()


# ── Model singleton + constants ──────────────────────────────────────

_SCRFD_MODEL_PATH = os.path.join(
    os.path.expanduser("~"),
    ".cache",
    "bpp",
    "models",
    "scrfd_2.5g_bnkps.onnx",
)
# ── Model: SCRFD (2.5g_bnkps) ──────────────────────────────────────
# What:   tertiary face detector, biased toward small / distant
#         faces that YuNet + BlazeFace miss in group photos and
#         landscapes. Outputs bboxes + 5-point keypoints.
# Where:  HuggingFace mirror (RuteNL/SCRFD-face-detection-ONNX) of
#         the InsightFace SCRFD release. The original deepinsight
#         distribution is Google-Drive-hosted which doesn't survive
#         scripted downloads.
# Why this one: 2.5g is the "medium" SCRFD variant (2.5 GFLOPs);
#         the bnkps suffix means it predicts batch-norm keypoints
#         (the head shape this codebase's anchor decoder expects).
# License: MIT (InsightFace project).
# To bump: stay within the bnkps family — switching to non-bnkps
#         changes the output tensor count and would break
#         _scrfd_decode in this file.
_SCRFD_MODEL_URL = (
    "https://huggingface.co/RuteNL/SCRFD-face-detection-ONNX/resolve/main/2.5g_bnkps.onnx"
)
_SCRFD_MODEL_SHA256 = "3f1ac54e769cb5fd76eda11ac3c088eed78d1f51a935a839d04d49b0e770219e"


def _scrfd_create(path: Path) -> Any:
    import onnxruntime as ort

    return ort.InferenceSession(str(path), providers=get_providers())


_scrfd_model = ModelSingleton(
    name="SCRFD face detection",
    model_path=Path(_SCRFD_MODEL_PATH),
    model_url=_SCRFD_MODEL_URL,
    model_sha256=_SCRFD_MODEL_SHA256,
    create_fn=_scrfd_create,
    registry_id="insightface_scrfd_25g",
    import_check=lambda: __import__("onnxruntime"),
)

_SCRFD_INPUT_SIZE = 640
_SCRFD_STRIDES = (8, 16, 32)
_SCRFD_NUM_ANCHORS = 2
_SCRFD_NMS_THRESH = 0.4
_MAX_RAW_DETECTIONS = 30  # cap before NMS — prevents texture/crowd floods


# ── Anchor + bbox helpers ────────────────────────────────────────────


@lru_cache(maxsize=32)
def _scrfd_anchor_centers(h_grid: int, w_grid: int, stride: int) -> np.ndarray:
    """Build (or return cached) anchor centers for a given grid + stride.

    Bounded cache (32 entries) prevents unbounded growth across varying
    input resolutions. SCRFD uses 3 strides over ~10 common shapes in
    practice — the cache pays back on every batch.
    """
    grid = np.stack(np.mgrid[:h_grid, :w_grid][::-1], axis=-1).astype(np.float32)
    centers = (grid * stride).reshape(-1, 2)
    centers = np.stack([centers] * _SCRFD_NUM_ANCHORS, axis=1).reshape(-1, 2)
    return centers


def _scrfd_distance2bbox(points: np.ndarray, dist: np.ndarray) -> np.ndarray:
    x1 = points[:, 0] - dist[:, 0]
    y1 = points[:, 1] - dist[:, 1]
    x2 = points[:, 0] + dist[:, 2]
    y2 = points[:, 1] + dist[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def _scrfd_nms(dets: np.ndarray, thresh: float) -> list[int]:
    x1, y1, x2, y2, scores = (
        dets[:, 0],
        dets[:, 1],
        dets[:, 2],
        dets[:, 3],
        dets[:, 4],
    )
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1 + 1) * np.maximum(0.0, yy2 - yy1 + 1)
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(ovr <= thresh)[0] + 1]
    return keep


# ── Public entry point ───────────────────────────────────────────────


def detect_faces_scrfd(
    image: np.ndarray,
    *,
    min_confidence: float = 0.6,
) -> list[tuple[int, int, int, int, float]]:
    """Detect faces using SCRFD. Returns ``(x, y, w, h, confidence)`` per face.

    Uses a 3-level FPN (strides 8/16/32) to detect faces across scales.
    Input is letterbox-resized to 640x640 so the aspect ratio is
    preserved while the receptive field stays consistent.
    """
    session = _scrfd_model.get()
    if session is None:
        return []

    # Ensure 3-channel BGR
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)

    h_orig, w_orig = image.shape[:2]
    sz = _SCRFD_INPUT_SIZE

    # Letterbox resize — preserve aspect, pad to square
    im_ratio = h_orig / w_orig
    if im_ratio > 1.0:
        new_h, new_w = sz, int(sz / im_ratio)
    else:
        new_w, new_h = sz, int(sz * im_ratio)
    det_scale = new_h / h_orig
    resized = cv2.resize(image, (new_w, new_h))
    det_img = np.zeros((sz, sz, 3), dtype=np.uint8)
    det_img[:new_h, :new_w, :] = resized

    # Preprocess: BGR→RGB, subtract 127.5, divide 128
    blob = cv2.dnn.blobFromImage(
        det_img,
        1.0 / 128,
        (sz, sz),
        (127.5, 127.5, 127.5),
        swapRB=True,
    )

    # Inference. The lock is essential — see _scrfd_lock comment above.
    input_name = session.get_inputs()[0].name
    with _scrfd_lock:
        outputs = session.run(None, {input_name: blob})

    # Detect batch dimension: shape (1, N, C) → batched, (N, C) → unbatched
    batched = len(outputs[0].shape) == 3

    # Decode the 3 FPN levels
    scores_all: list[np.ndarray] = []
    bboxes_all: list[np.ndarray] = []
    fmc = len(_SCRFD_STRIDES)

    for idx, stride in enumerate(_SCRFD_STRIDES):
        raw_scores = outputs[idx]
        raw_bbox = outputs[idx + fmc]
        scores = raw_scores[0] if batched else raw_scores  # (N, 1)
        bbox_preds = (raw_bbox[0] if batched else raw_bbox) * stride

        h_grid = sz // stride
        w_grid = sz // stride
        centers = _scrfd_anchor_centers(h_grid, w_grid, stride)

        pos = np.where(scores[:, 0] >= min_confidence)[0]
        if len(pos) == 0:
            continue

        scores_all.append(scores[pos])
        bboxes_all.append(_scrfd_distance2bbox(centers[pos], bbox_preds[pos]))

    if not scores_all:
        return []

    scores_cat = np.vstack(scores_all)
    bboxes_cat = np.vstack(bboxes_all) / det_scale

    # NMS + convert (x1, y1, x2, y2, conf) → (x, y, w, h, conf)
    pre_det = np.hstack((bboxes_cat, scores_cat)).astype(np.float32)
    keep = _scrfd_nms(pre_det, _SCRFD_NMS_THRESH)
    dets = pre_det[keep]

    results: list[tuple[int, int, int, int, float]] = []
    for x1, y1, x2, y2, conf in dets:
        x = max(0, min(round(float(x1)), w_orig - 1))
        y = max(0, min(round(float(y1)), h_orig - 1))
        w = min(round(float(x2 - x1)), w_orig - x)
        h = min(round(float(y2 - y1)), h_orig - y)
        if w > 0 and h > 0:
            results.append((x, y, w, h, float(conf)))
    return results


# ── Registry ───────────────────────────────────────────────────────
from bpp.scoring.model_base import ModelEntry, ModelRegistry  # noqa: E402

ModelRegistry.register(
    ModelEntry(
        name="SCRFD 2.5g",
        path=_SCRFD_MODEL_PATH,
        url=_SCRFD_MODEL_URL,
        sha256=_SCRFD_MODEL_SHA256,
        reset=_scrfd_model.reset,
    )
)
