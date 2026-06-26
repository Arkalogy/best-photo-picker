"""dlib fallback + SCRFD-supplementary face extractors.

The primary SFace pipeline and the research buffalo_s pipeline live in
their own modules (``face_embed_sface`` / ``face_embed_buffalo``); this
module holds the dlib fallback (used when SFace finds nothing) plus the
opt-in SCRFD supplementary pass that finds faces YuNet missed.

For backward compatibility this module re-exports ``_extract_sface`` and
``_extract_buffalo_s`` so ``face_embed`` — and the tests that drive the
dlib path via ``face_embed_extractors.<name>`` — keep their import path.
"""

from __future__ import annotations

import numpy as np

from bpp.scoring.face_embed import (
    DEFAULT_EMBEDDING_CONFIDENCE,
    MAX_FACES_PER_PHOTO,
    MIN_EMBEDDING_QUALITY,
)
from bpp.scoring.face_embed_buffalo import _extract_buffalo_s  # noqa: F401
from bpp.scoring.face_embed_landmarks import (
    _blur_score,
    _validate_face_landmarks,
)
from bpp.scoring.face_embed_sface import _extract_sface  # noqa: F401
from bpp.scoring.model_load_gate import MemoizedLoadGate
from bpp.utils.logging import get_logger

log = get_logger(__name__)


# ── dlib fallback extraction ──


def _dlib_face_quality(
    w: int,
    h: int,
    conf: float,
    image: np.ndarray | None = None,
    bbox: tuple[int, int, int, int] | None = None,
) -> float:
    """Score face quality 0.0-1.0 for dlib fallback detections.

    Mirrors ``_face_quality_from_landmarks`` but without YuNet landmarks.
    Components:
    - Size (0.4 weight): 112px is the SFace alignment target — larger is better
    - Confidence (0.2 weight): detector confidence
    - Aspect ratio (0.2 weight): closer to square is better for faces
    - Sharpness (0.2 weight): Laplacian blur detection on the crop
    """
    face_size = max(w, h)
    size_score = min(face_size / 112.0, 1.0)
    aspect = w / h if h > 0 else 0
    aspect_score = max(0.0, 1.0 - abs(aspect - 1.0))

    if image is not None and bbox is not None:
        blur = _blur_score(image, bbox)
        return size_score * 0.4 + conf * 0.2 + aspect_score * 0.2 + blur * 0.2

    return size_score * 0.5 + conf * 0.3 + aspect_score * 0.2


# Fail-closed acceptance gate for the dlib / ``face_recognition``
# embedder. Unlike every other restricted model, dlib's weights ship
# *inside* the ``face_recognition`` pip dependency, so they never pass
# through ``download_file`` — the download-time policy gate can't see
# them. And dlib is not the default embedder, so the orchestrator
# chokepoint (which checks the default) doesn't cover it either. This
# gate is therefore the ONLY enforcement point for the dlib (Boost
# Software License) attribution ack before inference. Memoized via the
# shared MemoizedLoadGate so the per-photo hot path
# (``_extract_dlib`` + ``_supplement_with_scrfd``) doesn't re-read the
# acceptance log on every call.
_dlib_gate = MemoizedLoadGate("dlib_face_recognition_resnet_v1")
#: Raises ModelLoadBlockedError when dlib isn't accepted; no-op once passed.
_enforce_dlib_policy = _dlib_gate.enforce


def _extract_dlib(
    image: np.ndarray,
    min_confidence: float,
    embedding_confidence: float = DEFAULT_EMBEDDING_CONFIDENCE,
    min_quality: float = MIN_EMBEDDING_QUALITY,
) -> list[dict]:
    """Extract face embeddings using dlib (face_recognition) pipeline."""
    # Fail-closed before touching dlib: an unaccepted entry raises
    # ModelLoadBlockedError, which the face worker's broad except
    # converts into a skipped photo (no embeddings) rather than a crash.
    _enforce_dlib_policy()

    import face_recognition

    from bpp.scoring.face import detect_faces_with_confidence

    # Disable heavy detectors during embedding extraction to prevent OOM —
    # scoring phase already ran all detectors, embedding only needs YuNet + dlib.
    boxes_conf = detect_faces_with_confidence(
        image,
        min_confidence=min_confidence,
        model_toggles={"model_scrfd": False, "model_blazeface_fr": False},
    )
    if not boxes_conf:
        return []

    # Apply stricter confidence threshold for embedding quality
    boxes_conf = [(x, y, w, h, c) for x, y, w, h, c in boxes_conf if c >= embedding_confidence]
    if not boxes_conf:
        return []

    if len(boxes_conf) > MAX_FACES_PER_PHOTO:
        log.info(
            "Skipping image with %d detections (likely false positives)",
            len(boxes_conf),
        )
        return []

    boxes = [(x, y, w, h) for x, y, w, h, _c in boxes_conf]

    # face_recognition expects RGB contiguous array
    rgb = np.ascontiguousarray(image[:, :, ::-1])

    # Convert (x, y, w, h) → face_recognition format (top, right, bottom, left)
    locations = [(y, x + w, y + h, x) for (x, y, w, h) in boxes]

    # Validate each detection with landmark geometry (fast 'small' model: 5 pts)
    all_landmarks = face_recognition.face_landmarks(rgb, locations, model="small")
    valid_with_conf: list[tuple[int, int, int, int, float]] = []
    valid_locations = []
    for (bx, by, bw, bh), (_, _, _, _, conf), loc, lm in zip(
        boxes, boxes_conf, locations, all_landmarks, strict=True
    ):
        if _validate_face_landmarks(lm, (bx, by, bw, bh)):
            valid_with_conf.append((bx, by, bw, bh, conf))
            valid_locations.append(loc)
        else:
            log.debug(
                "Rejected non-face detection at (%d,%d,%d,%d)",
                bx,
                by,
                bw,
                bh,
            )

    if not valid_with_conf:
        return []

    encodings = face_recognition.face_encodings(
        rgb,
        known_face_locations=valid_locations,
    )

    results = []
    for (x, y, w, h, conf), encoding in zip(valid_with_conf, encodings, strict=True):
        quality = _dlib_face_quality(w, h, conf, image=image, bbox=(x, y, w, h))
        if quality < min_quality:
            log.debug(
                "Skipping low-quality dlib face at (%d,%d,%d,%d) quality=%.2f",
                x,
                y,
                w,
                h,
                quality,
            )
            continue
        results.append(
            {
                "bbox": (x, y, w, h),
                # face_recognition.face_encodings returns float64 (1024
                # bytes per 128-d vector). The SFace path stores
                # float32 (512 bytes). When the same DB sees both,
                # Protection A's read-side decode rejects the float64
                # rows as "wrong size 1024 vs expected 512" — silently
                # losing every face the dlib fallback found. Cast to
                # float32 here so the storage contract is uniform.
                "embedding": encoding.astype(np.float32),
                "quality": quality,
            }
        )
    return results


# ── SCRFD supplementary pass ──

_SCRFD_OVERLAP_THRESH = 0.3  # IoU above this = same face, skip


def _bbox_iou(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    """Compute IoU between two (x, y, w, h) bboxes."""
    ax1, ay1, aw, ah = a[:4]
    bx1, by1, bw, bh = b[:4]
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _supplement_with_scrfd(
    image: np.ndarray,
    sface_results: list[dict],
    min_confidence: float,
    embedding_confidence: float,
    min_embedding_quality: float,
) -> list[dict]:
    """Find faces SCRFD detected that SFace/YuNet missed, extract via dlib.

    Compares SCRFD detections against existing SFace bboxes by IoU.
    New (non-overlapping) detections get embeddings via face_recognition.
    """
    try:
        from bpp.scoring.face import detect_faces_scrfd
    except ImportError:
        return sface_results

    scrfd_dets = detect_faces_scrfd(image, min_confidence=embedding_confidence)
    if not scrfd_dets:
        return sface_results

    existing_boxes = [r["bbox"] for r in sface_results]

    # Find SCRFD detections not covered by SFace results
    new_dets: list[tuple[int, int, int, int, float]] = []
    for det in scrfd_dets:
        x, y, w, h, conf = det
        overlaps = any(_bbox_iou((x, y, w, h), eb) > _SCRFD_OVERLAP_THRESH for eb in existing_boxes)
        if not overlaps:
            new_dets.append(det)

    if not new_dets or len(new_dets) + len(sface_results) > MAX_FACES_PER_PHOTO:
        return sface_results

    # Extract embeddings for new detections via dlib (if available)
    try:
        import face_recognition
    except ImportError:
        return sface_results

    rgb = np.ascontiguousarray(image[:, :, ::-1])
    locations = [(y, x + w, y + h, x) for x, y, w, h, _c in new_dets]

    from bpp.registry import ModelLoadBlockedError

    try:
        # Same dlib acceptance gate as _extract_dlib. Here a block must
        # NOT discard the SFace results we already have — skip only the
        # dlib-supplemented faces and return what SFace found.
        _enforce_dlib_policy()
        encodings = face_recognition.face_encodings(
            rgb,
            known_face_locations=locations,
        )
    except ModelLoadBlockedError:
        log.info(
            "dlib not accepted; skipping SCRFD-supplementary faces (keeping %d SFace embedding(s))",
            len(sface_results),
        )
        return sface_results
    except Exception:
        log.debug("dlib encoding failed for SCRFD supplement faces")
        return sface_results

    for (x, y, w, h, conf), encoding in zip(new_dets, encodings, strict=True):
        quality = _dlib_face_quality(w, h, conf, image=image, bbox=(x, y, w, h))
        if quality < min_embedding_quality:
            continue
        sface_results.append(
            {
                "bbox": (x, y, w, h),
                # Match the float32 contract enforced everywhere else
                # — see the comment in _extract_dlib above. SCRFD-
                # supplementary faces flowed through dlib's float64
                # encoder; without the cast they get dropped by
                # Protection A's read-side decode and never appear in
                # any cluster.
                "embedding": encoding.astype(np.float32),
                "quality": quality,
            }
        )

    return sface_results
