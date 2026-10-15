"""Per-face embedding extractors: buffalo_s (ArcFace 512-d) and any
plugin embedder, sharing one detect → align → embed → quality loop.

The buffalo_s path is the research-only ArcFace recognition pipeline. Split out of
:mod:`bpp.scoring.face_embed_extractors` for the 500-LOC cap;
``face_embed_extractors`` re-exports :func:`_extract_buffalo_s` so
existing callers (``face_embed``) keep their import path.
"""

from __future__ import annotations

import numpy as np

from bpp.scoring.face_embed import (
    DEFAULT_EMBEDDING_CONFIDENCE,
    MAX_FACES_PER_PHOTO,
    MIN_EMBEDDING_QUALITY,
)
from bpp.scoring.face_embed_landmarks import (
    _face_quality_from_landmarks,
    _validate_yunet_landmarks,
)
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _extract_buffalo_s(
    image: np.ndarray,
    min_confidence: float,
    embedding_confidence: float = DEFAULT_EMBEDDING_CONFIDENCE,
    min_quality: float = MIN_EMBEDDING_QUALITY,
) -> list[dict] | None:
    """Extract face embeddings via the full detector pipeline + buffalo_s
    ArcFace recognition (512-d).

    The runtime policy gate fires inside ``embed_face`` on first use; an
    unaccepted ``insightface_buffalo_s`` entry raises
    ``ModelLoadBlockedError``, which :func:`_extract_with_embedder`
    turns into a skipped face (logged) so one photo can't break a run.

    Returns ``None`` when no faces were found (so the caller can decide
    whether to retry with dlib).
    """
    from bpp.scoring import face_embed_buffalo_s

    if not face_embed_buffalo_s.is_available():
        return None

    def embed(align_image, bbox, row):
        # The full YuNet row lets the embedder use 5-point
        # similarity-transform alignment, matching the upstream reference.
        return face_embed_buffalo_s.embed_face(align_image, bbox, yunet_row=row)

    return _extract_with_embedder(
        image, min_confidence, embedding_confidence, min_quality, embed, "buffalo_s"
    )


def _extract_with_embedder(
    image: np.ndarray,
    min_confidence: float,
    embedding_confidence: float,
    min_quality: float,
    embed,
    name: str,
) -> list[dict] | None:
    """Shared detect → align → embed → quality loop for every embedder
    that works per face (buffalo_s, plugin embedders).

    ``embed(align_image, (x, y, w, h), yunet_row)`` returns a vector or
    ``None``. Mirrors :func:`bpp.scoring.face_embed_sface._extract_sface`
    structurally — same detection, landmark validation, upside-down
    handling and quality gate. Returns ``None`` when no faces were found.
    """
    from bpp.scoring.face_embed_detect import detect_face_rows, detect_inverted_face

    # Full-coverage detection (see face_embed_detect): the whole detector
    # pipeline plus per-face YuNet landmark recovery, so every face
    # scoring found gets embedded. Mirrors _extract_sface.
    all_raw = detect_face_rows(image, min_confidence, embedding_confidence)

    if not all_raw:
        return None

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
        if not _validate_yunet_landmarks(face):
            continue

        # Upside-down faces: align against the 180°-rotated view (see
        # detect_inverted_face / _extract_sface). Stored bbox stays in
        # original-image coordinates for the UI overlay.
        align_image, align_row = image, face
        inverted_row = detect_inverted_face(image, face)
        if inverted_row is not None:
            if rotated_image is None:
                rotated_image = np.ascontiguousarray(image[::-1, ::-1])
            align_image, align_row = rotated_image, inverted_row

        bbox = (int(align_row[0]), int(align_row[1]), int(align_row[2]), int(align_row[3]))
        try:
            emb = embed(align_image, bbox, align_row)
        except Exception:
            # Expected failures return None; this catches the unexpected
            # ones, e.g. the policy gate raising ModelLoadBlockedError.
            log.warning(
                "%s embed raised for bbox=(%d,%d,%d,%d); skipping this face",
                name,
                fx,
                fy,
                fw,
                fh,
                exc_info=True,
            )
            continue
        if emb is None:
            continue
        # Quality from the orientation-corrected row — the hallucinated
        # upright landmarks on an inverted face fake a frontality score.
        quality = _face_quality_from_landmarks(align_row, align_image)
        if quality < min_quality:
            continue
        results.append(
            {
                "bbox": (fx, fy, fw, fh),
                # float32 is the storage contract for every embedder.
                "embedding": np.asarray(emb, dtype=np.float32),
                "quality": quality,
            }
        )
    return results
