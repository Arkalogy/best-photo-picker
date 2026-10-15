"""Fallback face detectors — dlib HOG and OpenCV Haar cascade.

Extracted from :mod:`bpp.scoring.face` as part of the 500-LOC cap split.
These are the last-resort detectors in the orchestrator's priority chain:

- **dlib HOG**: used as a secondary detector when the fast detectors
  (YuNet + BlazeFace SR + SCRFD) miss faces. Requires the optional
  ``bppicker[faces]`` extra (face_recognition). Confidence is synthetic
  (``DLIB_DEFAULT_CONFIDENCE``) since face_recognition doesn't expose it.
- **Haar cascade**: shipped with OpenCV; the absolute last resort if every
  modern detector returns nothing. Confidence is also synthetic
  (``HAAR_DEFAULT_CONFIDENCE``).

The cascade is lazily loaded behind a lock so concurrent FaceWorker
threads share a single classifier instance.

Both are license-gated like every other model: the Haar cascade by its
own registry entry here, the HOG detector by the dlib entry inside
:func:`~bpp.scoring.face_recognition_loader.load_face_recognition`.
Until accepted, each returns no faces.
"""

from __future__ import annotations

import threading

import cv2
import numpy as np

from bpp.constants import (
    DLIB_DEFAULT_CONFIDENCE,
    HAAR_DEFAULT_CONFIDENCE,
    HAAR_MIN_FACE_SIZE,
    HAAR_MIN_NEIGHBORS,
    HAAR_SCALE_FACTOR,
)
from bpp.scoring.model_load_gate import MemoizedLoadGate

_haar_gate = MemoizedLoadGate("opencv_haar_frontalface")


def _license_allows(gate: MemoizedLoadGate) -> bool:
    from bpp.registry.policy import ModelLoadBlockedError

    try:
        gate.enforce()
    except ModelLoadBlockedError:
        return False
    return True


# ── dlib HOG (secondary — good for embeddings, adults) ──


def _has_face_recognition() -> bool:
    # The loader caches the import and applies the dlib license gate
    # (uncached, so a later acceptance takes effect).
    from bpp.scoring.face_recognition_loader import load_face_recognition

    return load_face_recognition() is not None


def _dlib_detect(image: np.ndarray) -> list[tuple[int, int, int, int, float]]:
    """Detect faces using dlib HOG via face_recognition. Returns (x, y, w, h, confidence).

    face_recognition doesn't expose dlib confidence scores, so we assign
    a fixed synthetic confidence (DLIB_DEFAULT_CONFIDENCE).  This ranks
    dlib detections below high-confidence MediaPipe hits during NMS.
    """
    from bpp.scoring.face_recognition_loader import load_face_recognition

    face_recognition = load_face_recognition()
    if face_recognition is None:
        return []

    rgb = np.ascontiguousarray(
        image[:, :, ::-1] if len(image.shape) == 3 and image.shape[2] == 3 else image
    )
    locations = face_recognition.face_locations(rgb, model="hog")
    if not locations:
        return []
    return [
        (left, top, right - left, bottom - top, DLIB_DEFAULT_CONFIDENCE)
        for (top, right, bottom, left) in locations
    ]


# ── Haar cascade (last resort) ──

_CASCADE: cv2.CascadeClassifier | None = None
_cascade_lock = threading.Lock()


def _get_cascade() -> cv2.CascadeClassifier:
    global _CASCADE
    if _CASCADE is not None:
        return _CASCADE
    with _cascade_lock:
        if _CASCADE is not None:
            return _CASCADE
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        _CASCADE = cv2.CascadeClassifier(cascade_path)
        if _CASCADE.empty():
            raise RuntimeError(f"Failed to load Haar cascade from {cascade_path}")
    return _CASCADE


def _haar_detect(image: np.ndarray) -> list[tuple[int, int, int, int, float]]:
    """Haar cascade fallback detection. Returns (x, y, w, h, confidence)."""
    if not _license_allows(_haar_gate):
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    cascade = _get_cascade()
    faces = cascade.detectMultiScale(
        gray,
        scaleFactor=HAAR_SCALE_FACTOR,
        minNeighbors=HAAR_MIN_NEIGHBORS,
        minSize=HAAR_MIN_FACE_SIZE,
    )
    if isinstance(faces, np.ndarray) and len(faces) > 0:
        return [(*tuple(f), HAAR_DEFAULT_CONFIDENCE) for f in faces.tolist()]
    return []
