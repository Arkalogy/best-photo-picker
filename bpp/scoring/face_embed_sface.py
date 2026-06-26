"""SFace face-embedding extractor (YuNet detection + SFace 128-d).

The primary face-recognition pipeline. Split out of
:mod:`bpp.scoring.face_embed_extractors` for the 500-LOC cap;
``face_embed_extractors`` re-exports :func:`_extract_sface` so existing
callers (``face_embed``) keep their import path.
"""

from __future__ import annotations

import numpy as np

from bpp.scoring.face_embed import (
    DEFAULT_EMBEDDING_CONFIDENCE,
    MAX_FACES_PER_PHOTO,
    MIN_EMBEDDING_QUALITY,
    SFACE_DISTANCE_SCALE,
    _get_sface_recognizer,
)
from bpp.scoring.face_embed_landmarks import (
    _face_quality_from_landmarks,
    _validate_yunet_landmarks,
)
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _extract_sface(
    image: np.ndarray,
    min_confidence: float,
    embedding_confidence: float = DEFAULT_EMBEDDING_CONFIDENCE,
    min_quality: float = MIN_EMBEDDING_QUALITY,
) -> list[dict] | None:
    """Extract face embeddings using YuNet + SFace pipeline.

    Uses iterative confidence relaxation: starts at *min_confidence*,
    and if results are marginal or empty, retries at progressively lower
    thresholds.  Each pass merges new detections via bbox overlap
    deduplication.  Stops when all detections are confident, no new
    faces are found, or we hit ``FACE_CONFIDENCE_FLOOR``.

    Returns list of {bbox, embedding, quality} dicts, or None if SFace
    unavailable.
    """
    from bpp.scoring.face_embed_detect import detect_face_rows, detect_inverted_face

    recognizer = _get_sface_recognizer()
    if recognizer is None:
        return None

    # Full-coverage detection: scoring counts faces with the whole detector
    # pipeline, so extraction must too or it silently drops faces. YuNet
    # alone missed most faces in group photos; detect_face_rows runs the
    # pipeline and recovers a real YuNet landmark row per face for SFace
    # alignment. See face_embed_detect for the recall-favoring rationale.
    all_raw = detect_face_rows(image, min_confidence, embedding_confidence)

    if not all_raw:
        return None  # no faces found — let caller try dlib fallback

    if len(all_raw) > MAX_FACES_PER_PHOTO:
        log.info(
            "Skipping image with %d YuNet detections (likely false positives)",
            len(all_raw),
        )
        return []

    results = []
    rotated_image = None  # lazily built 180° view, shared across faces
    for face in all_raw:
        fx, fy, fw, fh = int(face[0]), int(face[1]), int(face[2]), int(face[3])

        # Validate landmark geometry — rejects hands, toys, etc.
        if not _validate_yunet_landmarks(face):
            log.debug(
                "Rejected non-face YuNet detection at (%d,%d,%d,%d) conf=%.2f",
                fx,
                fy,
                fw,
                fh,
                float(face[-1]),
            )
            continue

        # Upside-down faces: YuNet detects them but hallucinates upright
        # landmarks, so aligning against the original orientation yields
        # an embedding that never matches the person's upright cluster.
        # Align against the 180°-rotated view instead; the stored bbox
        # stays in original-image coordinates for the UI overlay.
        align_image, align_row = image, face
        inverted_row = detect_inverted_face(image, face)
        if inverted_row is not None:
            if rotated_image is None:
                rotated_image = np.ascontiguousarray(image[::-1, ::-1])
            align_image, align_row = rotated_image, inverted_row

        # SFace alignment + feature extraction — per-face guard so one
        # bad bbox doesn't skip remaining good faces on the same photo.
        try:
            aligned = recognizer.alignCrop(align_image, align_row)
            emb = recognizer.feature(aligned)
        except Exception:
            log.debug(
                "SFace align/feature failed for face at (%d,%d,%d,%d), skipping",
                fx,
                fy,
                fw,
                fh,
            )
            continue

        # Squeeze (1, 128) → (128,), L2-normalize, then scale to match
        # dlib's distance range so existing clustering thresholds work.
        # SFace outputs float32 natively; keeping the float32 dtype halves
        # the on-disk blob size AND the peak RAM of the face-clustering
        # matrix (schema v35 re-encodes existing rows).
        emb = emb.flatten().astype(np.float32)
        norm = np.linalg.norm(emb)
        if norm > 0:
            emb = emb / norm * SFACE_DISTANCE_SCALE

        # Quality from the orientation-corrected row — the hallucinated
        # upright landmarks on an inverted face fake a frontality score.
        quality = _face_quality_from_landmarks(align_row, align_image)
        if quality < min_quality:
            log.debug(
                "Skipping low-quality face at (%d,%d,%d,%d) quality=%.2f",
                fx,
                fy,
                fw,
                fh,
                quality,
            )
            continue

        results.append(
            {
                "bbox": (fx, fy, fw, fh),
                "embedding": emb,
                "quality": quality,
            }
        )

    return results
