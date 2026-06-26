"""YuNet face detector (OpenCV DNN, Apache 2.0).

Primary face detector — runs on CPU at >100 FPS for 320x320 input.
Each thread gets its own detector instance via thread-local storage
so ThreadPoolExecutor workers don't share mutable DNN state.

Extracted from ``bpp.scoring.face`` during the v0.1 cleanup.
Re-exported from ``bpp.scoring.face`` for back-compat.

──────────────────────────────────────────────────────────────────────
ARCHITECTURE INVARIANT — DO NOT "MODERNIZE" TO ModelSingleton
──────────────────────────────────────────────────────────────────────
This module deliberately does NOT use ``bpp.scoring.model_base.ModelSingleton``
even though the project conventions say new models must.

WHY: OpenCV's ``cv2.FaceDetectorYN`` is NOT thread-safe — its ``.detect()``
call mutates internal DNN buffer state. ThreadPoolExecutor workers running
the analyzer in parallel would corrupt each other's detections. The
per-thread ``threading.local()`` pattern (``_yunet_tls`` below) gives each
worker its own detector instance — fixing the thread safety at the cost of
the cache-key sharing ModelSingleton provides.

The negative-cache flag (``_YUNET_AVAILABLE``) + ``_yunet_lock`` exist
because the model download is a once-per-process action that thread-local
storage can't coordinate.

If a future contributor migrates this to ModelSingleton, the analyzer will
SIGSEGV under workers >= 2 on a large library — silently, with no Python
traceback. The project conventions codify the exemption ("Existing YuNet/SFace/dlib
are exempt — thread-local or no-download semantics"). Test that enforces
this: tests/test_face_thread_safety.py.

Same pattern in: bpp/scoring/face_embed.py (SFace), bpp/scoring/face.py
(dlib via _dlib_lock).
──────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import threading

import cv2
import numpy as np

from bpp.scoring.model_load_gate import MemoizedLoadGate
from bpp.utils.logging import get_logger
from bpp.utils.paths import models_dir as _yunet_models_dir

log = get_logger(__name__)

_yunet_tls = threading.local()  # per-thread detector instances
_YUNET_AVAILABLE: bool | None = None
_yunet_lock = threading.Lock()

# Load-time license gate for YuNet (``opencv_yunet``). ``download_file``
# already gates YuNet at *download* time, but ``_ensure_yunet_model``
# returns early on a cache hit BEFORE reaching that path, and YuNet has
# no other load-time check — so a revoked/absent acceptance (or weights
# that arrived by any non-download path: backup restore, copied machine,
# manual drop) would otherwise load unchecked. This gate is the
# load-time enforcement point. Memoized per process via the shared
# MemoizedLoadGate (workers are per-run subprocesses, so a between-run
# accept/revoke re-evaluates).
_yunet_gate = MemoizedLoadGate("opencv_yunet")
#: Raises ModelLoadBlockedError when YuNet isn't accepted; no-op once passed.
_enforce_yunet_policy = _yunet_gate.enforce


_YUNET_MODEL_PATH = str(_yunet_models_dir() / "face_detection_yunet_2023mar.onnx")
# ── Model: YuNet face detector (March 2023 release) ────────────────
# What:   primary face detection model. Outputs bounding boxes +
#         5-point landmarks (eyes / nose / mouth corners). ~340KB
#         ONNX, runs on CPU at >100 FPS for 320x320 input.
# Where:  opencv_zoo (the OpenCV team's official model zoo). Hosted
#         via Git LFS on raw.githubusercontent through media.GH so
#         the binary download isn't rate-limited the same way as
#         the API path.
# Why this one: YuNet is the modern OpenCV-recommended detector,
#         meaningfully better than the legacy Haar cascades for
#         tilted / partially-occluded faces. The 2023mar release is
#         the long-term-stable build; later experimental releases
#         exist but haven't been promoted to opencv_zoo's main.
# License: Apache 2.0 (opencv_zoo).
# To bump: replace 2023mar with a newer dated release; YuNet's
#         output tensor shape is stable across the 2023 series so
#         no caller-side changes are needed.
_YUNET_MODEL_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
    "models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
_YUNET_MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


def _ensure_yunet_model() -> bool:
    """Download YuNet ONNX model if needed. Returns True if ready.

    cached files are SHA-verified before reuse. Without this,
    a tampered cache (compromised dir, cloud-sync overwrite, malicious
    bind mount) would silently load unverified ONNX bytes into the
    DNN inference engine. ModelIntegrityError propagates so callers
    see a loud failure, not a "model unavailable" downgrade.
    """
    from bpp.registry import ModelLoadBlockedError
    from bpp.scoring.model_base import ModelIntegrityError
    from bpp.utils.download import download_file, verify_existing

    # Load-time license gate — MUST run before the cache-hit early return
    # below, which would otherwise bypass the download-time gate for
    # already-present weights. Fail-closed: a block downgrades to
    # "unavailable" + fallback (same as a missing model), not a crash.
    try:
        _enforce_yunet_policy()
    except ModelLoadBlockedError as exc:
        log.warning("YuNet model unavailable: %s", exc)
        return False

    if os.path.exists(_YUNET_MODEL_PATH):
        # Verify cached bytes — propagate ModelIntegrityError
        verify_existing(_YUNET_MODEL_PATH, sha256=_YUNET_MODEL_SHA256)
        return True
    try:
        os.makedirs(os.path.dirname(_YUNET_MODEL_PATH), exist_ok=True)
        tmp = _YUNET_MODEL_PATH + ".tmp"
        download_file(
            _YUNET_MODEL_URL,
            tmp,
            registry_id="opencv_yunet",
            sha256=_YUNET_MODEL_SHA256,
        )
        os.replace(tmp, _YUNET_MODEL_PATH)
        log.info("Downloaded YuNet model to %s", _YUNET_MODEL_PATH)
        return True
    except ModelIntegrityError:
        # Loud failure — propagate so the caller sees the integrity
        # mismatch instead of treating it as a benign network error.
        tmp = _YUNET_MODEL_PATH + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        log.error("YuNet model integrity failure", exc_info=True)
        raise
    except Exception as exc:
        tmp = _YUNET_MODEL_PATH + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        log.warning("YuNet model unavailable: %s", exc)
        return False


def _get_yunet_detector(
    width: int,
    height: int,
) -> cv2.FaceDetectorYN | None:
    """Get or create YuNet detector sized for the given image dimensions.

    Each thread gets its own detector instance via thread-local storage
    so that ThreadPoolExecutor workers don't share mutable DNN state.
    """
    global _YUNET_AVAILABLE
    if _YUNET_AVAILABLE is False:
        return None

    # Check thread-local cache
    det = getattr(_yunet_tls, "detector", None)
    size = getattr(_yunet_tls, "size", (0, 0))
    if det is not None and (width, height) == size:
        return det

    with _yunet_lock:
        if _YUNET_AVAILABLE is False:
            return None
        if not _ensure_yunet_model():
            _YUNET_AVAILABLE = False
            return None
    # Create per-thread detector outside the lock
    try:
        det = cv2.FaceDetectorYN.create(
            _YUNET_MODEL_PATH,
            "",
            (width, height),
            score_threshold=0.5,
            nms_threshold=0.3,
            top_k=5000,
        )
        _yunet_tls.detector = det
        _yunet_tls.size = (width, height)
        _YUNET_AVAILABLE = True
        return det
    except Exception as exc:
        log.warning("YuNet detector creation failed: %s", exc)
        _YUNET_AVAILABLE = False
        return None


def _yunet_detect(
    image: np.ndarray,
    min_confidence: float = 0.5,
) -> list[tuple[int, int, int, int, float]]:
    """Detect faces using OpenCV YuNet. Returns (x, y, w, h, confidence)."""
    # YuNet requires 3-channel input
    if len(image.shape) == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    h, w = image.shape[:2]
    detector = _get_yunet_detector(w, h)
    if detector is None:
        return []
    _, faces = detector.detect(image)
    if faces is None:
        return []
    results = []
    for face in faces:
        conf = float(face[-1])
        if conf < min_confidence:
            continue
        fx, fy, fw, fh = int(face[0]), int(face[1]), int(face[2]), int(face[3])
        results.append((fx, fy, fw, fh, conf))
    return results


def _yunet_detect_raw(
    image: np.ndarray,
    min_confidence: float = 0.5,
) -> np.ndarray | None:
    """Detect faces using YuNet, returning raw face arrays for SFace alignment.

    Each row: [x, y, w, h, right_eye_x, right_eye_y, left_eye_x, left_eye_y,
               nose_x, nose_y, mouth_right_x, mouth_right_y,
               mouth_left_x, mouth_left_y, confidence]
    """
    if len(image.shape) == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    h, w = image.shape[:2]
    detector = _get_yunet_detector(w, h)
    if detector is None:
        return None
    _, faces = detector.detect(image)
    if faces is None:
        return None
    # Filter by confidence
    mask = faces[:, -1] >= min_confidence
    filtered = faces[mask]
    return filtered if len(filtered) > 0 else None


def reset_yunet_cache() -> None:
    """YuNet uses a thread-local + module-global negative cache
    instead of ModelSingleton — clear the negative-cache flag so
    the next detection retries init.

    Also re-arms the license gate so a reset re-evaluates acceptance
    (e.g. after a revoke); otherwise a passed gate would let the next
    load skip the check."""
    global _YUNET_AVAILABLE
    _YUNET_AVAILABLE = None
    _yunet_gate.reset()
