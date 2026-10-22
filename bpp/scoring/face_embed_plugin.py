"""Embedding faces with a plugin embedder (``FaceEmbedder.embed``):
the license check, the per-vector validation and the scaling that
makes a plugin's vectors comparable under the SFace grouping threshold.
Split from ``face_embed.py`` for the 500-line cap."""

from __future__ import annotations

import numpy as np

from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _extract_plugin(
    image, embedder, min_confidence, embedding_confidence, min_quality, model_toggles=None
):
    """Embed every detected face with a plugin's ``embed(image, box)``,
    once its registry entry's license allows it.

    Each vector must have the declared ``embedding_dim`` and finite,
    non-zero values; it is then normalised to unit length and scaled
    like SFace's, so the face-grouping threshold applies. Anything else
    is dropped (one warning per plugin)."""
    from bpp.scoring.face_embed_buffalo import _extract_with_embedder
    from bpp.scoring.face_embed_sface_runtime import SFACE_DISTANCE_SCALE
    from bpp.scoring.model_load_gate import license_allows

    feature = f"Face embedder {embedder.name}"
    if not license_allows(embedder.registry_id, feature=feature):
        return []

    def embed(img, box, _row):
        raw = embedder.embed(img, box)
        if raw is None:  # the contract's "can't embed this face"
            return None
        vec = np.asarray(raw, dtype=np.float64).ravel()
        norm = float(np.linalg.norm(vec)) if np.isfinite(vec).all() else 0.0
        if vec.shape != (embedder.embedding_dim,) or norm <= 0.0:
            _warn_bad_plugin_vector(embedder, vec)
            return None
        return vec / norm * SFACE_DISTANCE_SCALE

    return _extract_with_embedder(
        image,
        min_confidence,
        embedding_confidence,
        min_quality,
        embed,
        embedder.name,
        model_toggles=model_toggles,
    )


_bad_vector_warned: set[str] = set()


def _warn_bad_plugin_vector(embedder, vec) -> None:
    if embedder.name in _bad_vector_warned:
        return
    _bad_vector_warned.add(embedder.name)
    log.warning(
        "Face embedder %r returned an unusable vector (shape %s, declared %d floats, "
        "or non-finite / all-zero); those faces are skipped.",
        embedder.name,
        vec.shape,
        embedder.embedding_dim,
    )
