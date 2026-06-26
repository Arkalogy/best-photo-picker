"""buffalo_s face-embedding extractor (YuNet detection + ArcFace 512-d).

The research-only ArcFace recognition pipeline. Split out of
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
    """Extract face embeddings via YuNet detection + buffalo_s ArcFace
    recognition.

    Mirrors :func:`bpp.scoring.face_embed_sface._extract_sface`
    structurally — same iterative confidence relaxation, same landmark
    validation, same quality gate. Only the recognition step differs:
    SFace's 128-d output is swapped for the 512-d ArcFace output from
    :func:`bpp.scoring.face_embed_buffalo_s.embed_face`.

    The runtime policy gate fires inside ``embed_face`` on first
    use. An unaccepted ``insightface_buffalo_s`` entry raises
    ``ModelLoadBlockedError`` from inside the embedder; the
    per-face try/except below converts that into a skipped face so
    one bad photo doesn't break the entire analyze run. The error
    is also logged so the user sees what's wrong.

    Returns a list of result dicts or ``None`` when YuNet found no
    faces (so the caller can decide whether to retry with dlib).
    """
    from bpp.scoring import face_embed_buffalo_s
    from bpp.scoring.face_embed_detect import detect_face_rows, detect_inverted_face

    if not face_embed_buffalo_s.is_available():
        return None

    # Full-coverage detection (see face_embed_detect): the whole detector
    # pipeline plus per-face YuNet landmark recovery, so the ArcFace path
    # embeds every face scoring found — not just the ones YuNet sees at
    # full image scale. Mirrors _extract_sface.
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

        try:
            # Pass the full YuNet row so the embedder can use
            # 5-point similarity-transform alignment, producing
            # embeddings compatible with the upstream reference.
            emb = face_embed_buffalo_s.embed_face(
                align_image,
                (int(align_row[0]), int(align_row[1]), int(align_row[2]), int(align_row[3])),
                yunet_row=align_row,
            )
        except Exception:
            # buffalo_s.embed_face already swallows expected
            # failures and returns None; this catch is for the
            # unexpected case where the policy gate raises
            # ModelLoadBlockedError on first use.
            log.warning(
                "buffalo_s embed raised for bbox=(%d,%d,%d,%d); skipping this face",
                fx,
                fy,
                fw,
                fh,
                exc_info=True,
            )
            continue
        if emb is None:
            continue
        # buffalo_s already L2-normalizes the output, so use the
        # vector as-is. The clustering threshold (cosine distance)
        # is tuned for the 512-d arcface range — see
        # FACE_CLUSTER_THRESHOLD_FALLBACK and the clusterer.
        # Quality from the orientation-corrected row — the hallucinated
        # upright landmarks on an inverted face fake a frontality score.
        quality = _face_quality_from_landmarks(align_row, align_image)
        if quality < min_quality:
            continue
        results.append(
            {
                "bbox": (fx, fy, fw, fh),
                "embedding": emb,
                "quality": quality,
            }
        )
    return results
