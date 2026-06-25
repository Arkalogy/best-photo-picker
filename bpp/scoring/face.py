"""Face detection and scoring — public entry points.

Runs YuNet, BlazeFace (short-range + full-range), SCRFD, and dlib HOG,
merges results with confidence-weighted Non-Maximum Suppression
(``cv2.dnn.NMSBoxes``). Falls back to Haar cascade if neither is
available. Tries 180° rotation when no upright faces are found.

Detection confidence is configurable via ``min_confidence`` (default 0.3).

The detector implementations and NMS/filter/tiling helpers live in
sibling modules so this file stays at the documented 500-LOC cap:

- :mod:`bpp.scoring.face_fallback` — dlib HOG + Haar cascade
- :mod:`bpp.scoring.face_pipeline` — ``_DETECTORS`` registry, NMS,
  size filters, tiled detection, ``_collect_detections``,
  ``_iterative_collect``
- :mod:`bpp.scoring.face_yunet` / :mod:`bpp.scoring.face_mediapipe` /
  :mod:`bpp.scoring.face_blazeface_fr` / :mod:`bpp.scoring.face_scrfd`
  — per-backend detectors
- :mod:`bpp.scoring.face_expression` /
  :mod:`bpp.scoring.face_hand_filter` — landmark-based quality + hand
  false-positive suppression
- :mod:`bpp.scoring.face_score` — composite face score

Every symbol previously importable from this module remains importable
from the same path via the re-exports below.
"""

from __future__ import annotations

import numpy as np

from bpp.constants import FACE_GOOD_CONFIDENCE, MIN_FACE_AREA_FRAC, MIN_FACE_IMAGE_PX
from bpp.utils.logging import get_logger

log = get_logger(__name__)

# ── Re-exports: keep historical imports working ──────────────────────
from bpp.scoring.face_blazeface_fr import _fr_detector  # noqa: E402
from bpp.scoring.face_blazeface_fr import (  # noqa: E402, F401
    blazeface_fr_detect as _blazeface_fr_detect,
)
from bpp.scoring.face_detector_registry import FaceDetector, register_detector  # noqa: E402, F401
from bpp.scoring.face_expression import (  # noqa: E402, F401
    _BLINK_LEFT,
    _BLINK_RIGHT,
    _BLINK_THRESH,
    _JAW_LEFT,
    _JAW_RIGHT,
    _LANDMARKER_PATH,
    _LANDMARKER_URL,
    _SMILE_FULL,
    _SMILE_LEFT,
    _SMILE_RIGHT,
    _create_landmarker,
    _face_landmarker,
    _get_landmarker,
    _landmarker_lock,
    _score_expression,
)
from bpp.scoring.face_fallback import (  # noqa: E402, F401
    _CASCADE,
    _USE_DLIB,
    _cascade_lock,
    _dlib_detect,
    _dlib_lock,
    _get_cascade,
    _haar_detect,
    _has_face_recognition,
)
from bpp.scoring.face_hand_filter import (  # noqa: E402, F401
    _HAND_FACE_IOU_THRESH,
    _HAND_MODEL_PATH,
    _HAND_MODEL_URL,
    _create_hand_detector,
    _detect_hand_bboxes,
    _get_hand_detector,
    _hand_landmarker,
    _hand_lock,
    _iou,
    _suppress_hand_faces,
)
from bpp.scoring.face_mediapipe import (  # noqa: E402, F401
    _BUNDLED_DIR,
    _MODEL_DIR,
    _MODEL_PATH,
    _MODEL_SHA256,
    _MODEL_URL,
    _create_mediapipe_detector,
    _get_mediapipe_detector,
    _mediapipe_detect,
    _mp_blazeface,
    _mp_lock,
    ensure_mediapipe_models,
)

# Importing face_pipeline triggers detector-registry side effects (the
# six register_detector(...) calls). Keep this import even if the
# symbols below were unused locally.
from bpp.scoring.face_pipeline import (  # noqa: E402, F401
    _DETECTORS,
    _TILE_MIN_IMAGE_PX,
    _TILE_OVERLAP,
    _TILE_SIZE,
    _collect_detections,
    _dlib_detect_wrapper,
    _filter_small_faces,
    _filter_small_faces_conf,
    _haar_detect_wrapper,
    _iterative_collect,
    _nms_faces,
    _scrfd_detect_wrapper,
    _tiled_detect,
)
from bpp.scoring.face_score import score_face  # noqa: E402, F401
from bpp.scoring.face_scrfd import (  # noqa: E402, F401
    _MAX_RAW_DETECTIONS,
    _SCRFD_INPUT_SIZE,
    _SCRFD_MODEL_PATH,
    _SCRFD_MODEL_URL,
    _SCRFD_NMS_THRESH,
    _SCRFD_NUM_ANCHORS,
    _SCRFD_STRIDES,
    _scrfd_anchor_centers,
    _scrfd_create,
    _scrfd_distance2bbox,
    _scrfd_model,
    _scrfd_nms,
    detect_faces_scrfd,
)
from bpp.scoring.face_yunet import (  # noqa: E402, F401
    _YUNET_AVAILABLE,
    _YUNET_MODEL_PATH,
    _YUNET_MODEL_SHA256,
    _YUNET_MODEL_URL,
    _ensure_yunet_model,
    _get_yunet_detector,
    _yunet_detect,
    _yunet_detect_raw,
    _yunet_lock,
    _yunet_tls,
)
from bpp.scoring.face_yunet import (  # noqa: E402
    reset_yunet_cache as _reset_yunet_cache,
)


def ensure_face_models() -> list[str]:
    """Pre-download all face models.

    Downloads: YuNet, BlazeFace short-range, BlazeFace full-range,
    FaceLandmarker, HandLandmarker.
    Returns list of warnings for unavailable models.
    """
    warnings = []
    if not _ensure_yunet_model():
        warnings.append("YuNet model unavailable — primary face detection degraded")
    warnings.extend(ensure_mediapipe_models())
    if _fr_detector.ensure_model() is None:
        warnings.append("BlazeFace full-range model unavailable — profile detection degraded")
    if _face_landmarker.ensure_model() is None:
        warnings.append("FaceLandmarker model unavailable — expression quality scoring disabled")
    if _hand_landmarker.ensure_model() is None:
        warnings.append("HandLandmarker model unavailable — hand FP suppression disabled")
    return warnings


# ── Public API ────────────────────────────────────────────────────────


def detect_faces(
    image: np.ndarray,
    *,
    min_confidence: float = 0.3,
    min_face_area_frac: float = MIN_FACE_AREA_FRAC,
    model_toggles: dict[str, bool] | None = None,
) -> list[tuple[int, int, int, int]]:
    """Detect faces, return list of (x, y, w, h) bounding boxes.

    Runs YuNet, BlazeFace, and dlib with iterative confidence relaxation,
    then merges results with confidence-weighted NMS.
    Tries 180° rotation when no upright faces are found.
    Falls back to Haar cascade if nothing found.

    *model_toggles* can disable specific detectors.
    """
    mt = model_toggles or {}
    h, w = image.shape[:2]

    if h < MIN_FACE_IMAGE_PX or w < MIN_FACE_IMAGE_PX:
        return []

    all_faces = _iterative_collect(image, min_confidence, mt)

    if all_faces:
        has_marginal = any(c < FACE_GOOD_CONFIDENCE for *_, c in all_faces)
        if has_marginal:
            if mt.get("model_hand_landmarker", True):
                all_faces = _suppress_hand_faces(all_faces, image)
            else:
                log.debug("HandLandmarker skipped (disabled)")
        return _filter_small_faces(
            [(x, y, fw, fh) for x, y, fw, fh, _c in all_faces],
            h * w,
            min_face_area_frac,
        )

    haar = _haar_detect(image)
    if haar:
        return _filter_small_faces(_nms_faces(haar), h * w, min_face_area_frac)
    return []


def detect_faces_with_confidence(
    image: np.ndarray,
    *,
    min_confidence: float = 0.3,
    min_face_area_frac: float = MIN_FACE_AREA_FRAC,
    model_toggles: dict[str, bool] | None = None,
) -> list[tuple[int, int, int, int, float]]:
    """Like ``detect_faces`` but returns (x, y, w, h, confidence) tuples.

    Used by the embedding pipeline to apply a stricter confidence
    threshold for clustering quality.
    *model_toggles* can disable specific detectors (same as ``detect_faces``).
    """
    mt = model_toggles or {}
    h, w = image.shape[:2]

    if h < MIN_FACE_IMAGE_PX or w < MIN_FACE_IMAGE_PX:
        return []

    all_faces = _iterative_collect(image, min_confidence, mt)

    if all_faces:
        has_marginal = any(c < FACE_GOOD_CONFIDENCE for *_, c in all_faces)
        if has_marginal:
            if mt.get("model_hand_landmarker", True):
                all_faces = _suppress_hand_faces(all_faces, image)
            else:
                log.debug("HandLandmarker skipped (disabled)")
        return _filter_small_faces_conf(all_faces, h * w, min_face_area_frac)

    haar = _haar_detect(image)
    if haar:
        return _filter_small_faces_conf(
            _nms_faces(haar, keep_confidence=True),
            h * w,
            min_face_area_frac,  # type: ignore[arg-type]
        )
    return []


# ── Model lifecycle registry ──────────────────────────────────────────
# Entries for Settings → Advanced → ML Models.
# Names MUST match the corresponding `_file_info` calls in
# `bpp/web/models_status.py`.
from bpp.scoring.model_base import ModelEntry, ModelRegistry  # noqa: E402

ModelRegistry.register(
    ModelEntry(
        name="BlazeFace short-range",
        path=_MODEL_PATH,
        url=_MODEL_URL,
        sha256=_MODEL_SHA256,
        reset=_mp_blazeface.reset,
    )
)
ModelRegistry.register(
    ModelEntry(
        name="YuNet (primary)",
        path=_YUNET_MODEL_PATH,
        url=_YUNET_MODEL_URL,
        sha256=_YUNET_MODEL_SHA256,
        reset=_reset_yunet_cache,
    )
)
