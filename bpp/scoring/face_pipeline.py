"""Face detection orchestrator + NMS / filter helpers.

Extracted from :mod:`bpp.scoring.face` as part of the 500-LOC cap split.
This module owns the multi-detector pipeline:

- ``_DETECTORS`` registry dict bound at module load (single point of
  swap for plugin authors; public-API hook also via
  :func:`bpp.scoring.face_detector_registry.register_detector`).
- ``_nms_faces`` — confidence-weighted Non-Maximum Suppression
  via ``cv2.dnn.NMSBoxes``.
- ``_filter_small_faces`` / ``_filter_small_faces_conf`` — area-fraction
  threshold to drop tiny detections.
- ``_tiled_detect`` — overlapping-tile YuNet pass to catch small/distant
  faces the full-image detectors miss.
- ``_collect_detections`` — fast-detectors-first orchestrator with
  SCRFD early-exit and 180° rotation fallback.
- ``_iterative_collect`` — confidence-relaxation outer loop on top of
  ``_collect_detections``.

The public ``detect_faces`` / ``detect_faces_with_confidence`` entry
points stay in :mod:`bpp.scoring.face`; they call into this module.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from bpp.constants import (
    FACE_CONFIDENCE_FLOOR,
    FACE_GOOD_CONFIDENCE,
    FACE_NMS_IOU_THRESH,
    FACE_NMS_SCORE_THRESH,
    MIN_FACE_AREA_FRAC,
    MIN_FACE_IMAGE_PX,
)
from bpp.scoring.face_blazeface_fr import blazeface_fr_detect as _blazeface_fr_detect
from bpp.scoring.face_detector_registry import FaceDetector, register_detector
from bpp.scoring.face_fallback import _dlib_detect, _haar_detect, _has_face_recognition
from bpp.scoring.face_mediapipe import _mediapipe_detect
from bpp.scoring.face_scrfd import _MAX_RAW_DETECTIONS, detect_faces_scrfd
from bpp.scoring.face_yunet import _yunet_detect
from bpp.utils.logging import get_logger

log = get_logger(__name__)


# ── Detector registry ──
# Single point of binding for detector implementations. The orchestrator
# below references detectors by name via this dict instead of bare function
# symbols, so a swap is one line at module load.
#
# Adding a new detector:
#   1. Implement `def my_detect(image, min_confidence) -> list[(x,y,w,h,conf)]`
#      in its own module (extension pattern: see face_scrfd.py / face_blazeface_fr.py).
#   2. Add an entry: `_DETECTORS["my_kind"] = my_detect`.
#   3. Reference `_DETECTORS["my_kind"]` from _collect_detections() at the
#      appropriate priority (fast / slow / rotation).
_DETECTORS: dict[str, Any] = {}


def _scrfd_detect_wrapper(
    img: np.ndarray, min_confidence: float
) -> list[tuple[int, int, int, int, float]]:
    return detect_faces_scrfd(img, min_confidence=min_confidence)


def _dlib_detect_wrapper(
    img: np.ndarray, _min_confidence: float
) -> list[tuple[int, int, int, int, float]]:
    return _dlib_detect(img)


def _haar_detect_wrapper(
    img: np.ndarray, _min_confidence: float
) -> list[tuple[int, int, int, int, float]]:
    return _haar_detect(img)


_DETECTORS["yunet"] = _yunet_detect
_DETECTORS["mediapipe_sr"] = _mediapipe_detect
_DETECTORS["blazeface_fr"] = _blazeface_fr_detect
_DETECTORS["scrfd"] = _scrfd_detect_wrapper
_DETECTORS["dlib"] = _dlib_detect_wrapper
_DETECTORS["haar"] = _haar_detect_wrapper

register_detector(
    FaceDetector(
        name="yunet",
        detect=_yunet_detect,
        toggle_key="model_yunet",
        license_id="Apache-2.0",
        description="OpenCV YuNet — fast, complementary detector",
    )
)
register_detector(
    FaceDetector(
        name="mediapipe_sr",
        detect=_mediapipe_detect,
        toggle_key=None,  # bundled BlazeFace short-range; always available
        license_id="Apache-2.0",
        description="MediaPipe BlazeFace short-range — close-ups",
    )
)
register_detector(
    FaceDetector(
        name="blazeface_fr",
        detect=_blazeface_fr_detect,
        toggle_key="model_blazeface_fr",
        license_id="Apache-2.0",
        description="BlazeFace full-range — wide / group shots",
    )
)
register_detector(
    FaceDetector(
        name="scrfd",
        detect=_scrfd_detect_wrapper,
        toggle_key="model_scrfd",
        license_id="MIT",
        description="InsightFace SCRFD — best for babies / small faces",
    )
)
register_detector(
    FaceDetector(
        name="dlib",
        detect=_dlib_detect_wrapper,
        toggle_key=None,
        license_id="BSD-3-Clause",
        description="dlib HOG fallback — adults, requires bppicker[faces]",
    )
)
register_detector(
    FaceDetector(
        name="haar",
        detect=_haar_detect_wrapper,
        toggle_key=None,
        license_id="BSD-2-Clause",
        description="OpenCV Haar cascade — last-resort fallback",
    )
)


# ── Non-Maximum Suppression ──


def _nms_faces(
    faces_with_conf: list[tuple[int, int, int, int, float]],
    *,
    keep_confidence: bool = False,
) -> list[tuple[int, int, int, int]] | list[tuple[int, int, int, int, float]]:
    """Merge overlapping detections using confidence-weighted NMS.

    Uses ``cv2.dnn.NMSBoxes`` — the same industry-standard algorithm used by
    YOLO, RetinaFace, and this codebase's pet detector (``pets.py``).
    Higher-confidence detections win when boxes overlap.

    Returns (x, y, w, h) boxes, optionally with confidence if *keep_confidence*.
    """
    if not faces_with_conf:
        return []

    # NMSBoxes expects [x1, y1, x2, y2] format
    rects = [[x, y, x + w, y + h] for (x, y, w, h, _) in faces_with_conf]
    scores = [conf for (*_, conf) in faces_with_conf]
    indices = cv2.dnn.NMSBoxes(rects, scores, FACE_NMS_SCORE_THRESH, FACE_NMS_IOU_THRESH)

    if len(indices) == 0:
        return []

    if keep_confidence:
        return [
            (
                faces_with_conf[i][0],
                faces_with_conf[i][1],
                faces_with_conf[i][2],
                faces_with_conf[i][3],
                faces_with_conf[i][4],
            )
            for i in indices.flatten()
        ]
    return [
        (faces_with_conf[i][0], faces_with_conf[i][1], faces_with_conf[i][2], faces_with_conf[i][3])
        for i in indices.flatten()
    ]


# ── Size filter ──


def _filter_small_faces(
    faces: list[tuple[int, int, int, int]],
    image_area: int,
    min_area_frac: float = MIN_FACE_AREA_FRAC,
) -> list[tuple[int, int, int, int]]:
    """Remove faces smaller than *min_area_frac* of image area."""
    min_area = image_area * min_area_frac
    return [(x, y, fw, fh) for (x, y, fw, fh) in faces if fw * fh >= min_area]


def _filter_small_faces_conf(
    faces: list[tuple[int, int, int, int, float]],
    image_area: int,
    min_area_frac: float = MIN_FACE_AREA_FRAC,
) -> list[tuple[int, int, int, int, float]]:
    """Remove faces smaller than *min_area_frac* (confidence-preserving)."""
    min_area = image_area * min_area_frac
    return [(x, y, fw, fh, c) for (x, y, fw, fh, c) in faces if fw * fh >= min_area]


# ── Tiled detection (catches small/distant faces) ──

# Minimum image dimension to attempt tiled detection.
# Below this, tiling produces tiles too small for reliable detection.
_TILE_MIN_IMAGE_PX = 400
_TILE_SIZE = 640
_TILE_OVERLAP = 0.25


def _tiled_detect(
    image: np.ndarray,
    min_confidence: float,
) -> list[tuple[int, int, int, int, float]]:
    """Run YuNet on overlapping tiles to catch small/distant faces.

    Splits the image into overlapping _TILE_SIZE tiles, runs detection
    on each, and remaps coordinates back to the full image.

    Only call this as a fallback when full-image detection finds nothing.
    """
    h, w = image.shape[:2]
    if h < _TILE_MIN_IMAGE_PX or w < _TILE_MIN_IMAGE_PX:
        return []

    stride = int(_TILE_SIZE * (1 - _TILE_OVERLAP))
    all_faces: list[tuple[int, int, int, int, float]] = []

    for y0 in range(0, max(1, h - _TILE_SIZE // 2), stride):
        for x0 in range(0, max(1, w - _TILE_SIZE // 2), stride):
            y1 = min(y0 + _TILE_SIZE, h)
            x1 = min(x0 + _TILE_SIZE, w)
            tile = image[y0:y1, x0:x1]

            # Skip tiny tiles at edges
            th, tw = tile.shape[:2]
            if th < MIN_FACE_IMAGE_PX or tw < MIN_FACE_IMAGE_PX:
                continue

            faces = _yunet_detect(tile, min_confidence)
            for fx, fy, fw, fh, conf in faces:
                # Remap to full-image coordinates
                all_faces.append((fx + x0, fy + y0, fw, fh, conf))

    return all_faces


# ── Orchestrator ──


def _collect_detections(
    image: np.ndarray,
    min_confidence: float,
    model_toggles: dict[str, bool] | None = None,
) -> tuple[list[tuple[int, int, int, int, float]], bool]:
    """Run detectors with early-exit when confident faces are found.

    Strategy: run fast detectors first (YuNet, BlazeFace SR).  If they
    find high-confidence faces, skip the slower detectors (FR, dlib).
    Fall back to rotation and tiling only when nothing is found.

    *model_toggles* can disable BlazeFace full-range via ``model_blazeface_fr``.
    """
    mt = model_toggles or {}
    h, w = image.shape[:2]
    all_faces: list[tuple[int, int, int, int, float]] = []
    found_upright = False

    # 0. SCRFD (InsightFace, MIT) — best multi-scale detector, handles babies/small faces
    # When SCRFD finds confident faces, skip MediaPipe + BlazeFace FR + dlib
    # (the expensive paths). We still run YuNet because it's the cheap
    # complementary detector and the SCRFD early-exit otherwise drops
    # secondary faces in group photos that SCRFD misses but YuNet catches
    # (small background subjects, profiles, kids). BPP's per-person Pick
    # workflow depends on every face making it to clustering; speed wins
    # that lose a face are the wrong wins. Downstream NMS dedupes overlap.
    if mt.get("model_scrfd", True):
        scrfd_faces = detect_faces_scrfd(image, min_confidence=min_confidence)
        all_faces.extend(scrfd_faces)
        if scrfd_faces:
            found_upright = True
            max_scrfd_conf = max(c for *_, c in scrfd_faces)
            if max_scrfd_conf >= FACE_GOOD_CONFIDENCE:
                yunet_faces = _DETECTORS["yunet"](image, min_confidence)
                all_faces.extend(yunet_faces)
                return all_faces, found_upright
    else:
        log.debug("SCRFD skipped (disabled)")

    # 1. YuNet (OpenCV DNN, BSD) — fast, complementary detector
    yunet_faces = _DETECTORS["yunet"](image, min_confidence)
    all_faces.extend(yunet_faces)
    if yunet_faces:
        found_upright = True

    # 2. MediaPipe BlazeFace short-range — fast, complementary for close-ups
    mp_faces = _DETECTORS["mediapipe_sr"](image, min_confidence)
    all_faces.extend(mp_faces)
    if mp_faces:
        found_upright = True

    # Early exit: if fast detectors found confident faces, skip slow ones.
    # BlazeFace FR and dlib are only needed for hard cases (profiles,
    # distant faces, babies) that the fast detectors miss.
    has_confident = all_faces and max(c for *_, c in all_faces) >= FACE_GOOD_CONFIDENCE
    if not has_confident:
        # 3. BlazeFace full-range — profiles, angled, 2-10m distance
        if mt.get("model_blazeface_fr", True):
            fr_faces = _DETECTORS["blazeface_fr"](image, min_confidence)
            all_faces.extend(fr_faces)
            if fr_faces:
                found_upright = True
        else:
            log.debug("BlazeFace full-range skipped (disabled)")

        # 4. dlib HOG — good for standard adult faces
        if _has_face_recognition():
            dlib_faces = _DETECTORS["dlib"](image, min_confidence)
            all_faces.extend(dlib_faces)
            if dlib_faces:
                found_upright = True

    # 5. Try 180° rotation if nothing found upright
    if not found_upright:
        rotated = np.ascontiguousarray(image[::-1, ::-1])
        yunet_rot = _DETECTORS["yunet"](rotated, min_confidence)
        if yunet_rot:
            all_faces.extend(
                (w - x - fw, h - y - fh, fw, fh, conf) for x, y, fw, fh, conf in yunet_rot
            )
        mp_rot = _DETECTORS["mediapipe_sr"](rotated, min_confidence)
        if mp_rot:
            all_faces.extend(
                (w - x - fw, h - y - fh, fw, fh, conf) for x, y, fw, fh, conf in mp_rot
            )
        if _has_face_recognition():
            dlib_rot = _DETECTORS["dlib"](rotated, min_confidence)
            if dlib_rot:
                all_faces.extend(
                    (w - x - fw, h - y - fh, fw, fh, conf) for x, y, fw, fh, conf in dlib_rot
                )

    # 6. Tiled detection — when no faces found OR all detections are marginal
    max_conf = max((c for *_, c in all_faces), default=0.0)
    if not all_faces or max_conf < FACE_GOOD_CONFIDENCE:
        tiled = _tiled_detect(image, min_confidence)
        if tiled:
            all_faces.extend(tiled)

    # 7. Sanity cap — reject if still flooding (texture/crowd noise)
    if len(all_faces) > _MAX_RAW_DETECTIONS:
        log.info(
            "Capping %d raw detections to %d (likely false positives)",
            len(all_faces),
            _MAX_RAW_DETECTIONS,
        )
        all_faces.sort(key=lambda f: f[4], reverse=True)
        all_faces = all_faces[:_MAX_RAW_DETECTIONS]

    return all_faces, found_upright


def _iterative_collect(
    image: np.ndarray,
    min_confidence: float,
    model_toggles: dict[str, bool] | None = None,
) -> list[tuple[int, int, int, int, float]]:
    """Run detectors with iterative confidence relaxation.

    Starts at *min_confidence* and collects faces.  If any detection has
    confidence below ``FACE_GOOD_CONFIDENCE``, or zero faces were found, retries
    at a halved threshold.  Each pass merges new detections into the
    accumulated pool via NMS.  Stops when:
      (a) all detections meet the good-confidence bar, or
      (b) a lower threshold found no new (non-overlapping) faces, or
      (c) we hit ``FACE_CONFIDENCE_FLOOR``.
    """
    accumulated: list[tuple[int, int, int, int, float]] = []
    tried_confidence = min_confidence

    while True:
        new_faces, _upright = _collect_detections(image, tried_confidence, model_toggles)
        if new_faces:
            # Merge with accumulated pool — NMS deduplicates overlaps
            combined = accumulated + new_faces
            merged = _nms_faces(combined, keep_confidence=True)
            prev_count = len(accumulated)
            accumulated = merged  # type: ignore[assignment]

            if not accumulated:
                pass  # NMS filtered everything — keep trying
            elif len(accumulated) > prev_count:
                # Found new faces — check if all are confident
                min_det_conf = min(c for *_, c in accumulated)
                if min_det_conf >= FACE_GOOD_CONFIDENCE:
                    break  # all detections are strong — done
            else:
                break  # lower threshold didn't find new faces — stop
        elif accumulated:
            break  # had results from prior pass, this pass added nothing

        # Step down confidence — but only one retry (halve once, not 4x)
        next_conf = tried_confidence / 2.0
        if next_conf < FACE_CONFIDENCE_FLOOR or tried_confidence < min_confidence:
            break
        tried_confidence = next_conf

    return accumulated
