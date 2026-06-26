"""Public registry for face-embedding backends.

``bpp.scoring.face_embed`` ships SFace as the primary
embedder with a dlib fallback when SFace's ONNX model isn't
loadable. Both were implementation-detail private functions
inside the module — no API for a plugin to swap in (say) ArcFace
or InsightFace's recognition models.

This registry promotes embedding to a documented extension point.
A plugin registers its embedder via:

    from bpp.scoring.face_embedder_registry import (
        FaceEmbedder, register_embedder
    )

    def my_arcface_embed(image, face_box):
        # ... return numpy ndarray (embedding vector) or None
        ...

    register_embedder(FaceEmbedder(
        name="arcface",
        embed=my_arcface_embed,
        embedding_dim=512,
        license_id="MIT",
    ))

The orchestrator in ``face_embed.extract_face_embeddings`` keeps
its SFace-first / dlib-fallback ordering — that captures real
quality + availability properties. Plugin embedders run via
``run_optional_embedder(name, image, face_box)`` as additional
backends the caller invokes explicitly.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np

from bpp.scoring._registry_base import _ScoringRegistry
from bpp.utils.logging import get_logger

_log = get_logger(__name__)

# Embedder contract:
#   (image, face_box) -> ndarray | None
#   image is a BGR ndarray; face_box is (x, y, w, h) in image coords;
#   return is a 1-D float32 ndarray of length `embedding_dim` or None
#   if the face couldn't be embedded (low quality, model unavailable).
EmbedFn = Callable[[np.ndarray, tuple[int, int, int, int]], "np.ndarray | None"]


@dataclass(frozen=True)
class FaceEmbedder:
    """Metadata + embed function for one embedder backend.

    ``name``: short identifier. Built-ins are ``"sface"`` and
        ``"dlib"``. Plugins should pick something unique unless
        intentionally swapping a built-in.

    ``embed``: callable matching ``EmbedFn``. Must be thread-safe.
        Returns ``None`` rather than raising if the face can't be
        embedded — the caller's clustering pipeline tolerates
        skipped embeddings cleanly, but propagating exceptions
        across a 50k-photo batch breaks the worker.

    ``embedding_dim``: the length of the returned vector. Used by
        the cluster threshold heuristic (different dims have
        different cosine-distance characteristics) and surfaced
        in DB rows so the cluster code can refuse to compare
        embeddings from different backends.

    ``license_id``: SPDX-style identifier for the embedder's model
        weights / code.

    ``description``: one-line tooltip text. Optional.
    """

    name: str
    embed: EmbedFn
    embedding_dim: int
    license_id: str = ""
    description: str = ""


_REGISTRY: _ScoringRegistry[FaceEmbedder] = _ScoringRegistry("face embedder", _log)


def register_embedder(embedder: FaceEmbedder) -> None:
    """Add an embedder to the registry. Idempotent — replaces on same name."""
    _log.debug(
        "Registered face embedder %r (dim=%d, license=%s)",
        embedder.name,
        embedder.embedding_dim,
        embedder.license_id or "unspecified",
    )
    _REGISTRY.register(embedder, embedder.name)


def get_embedder(name: str) -> FaceEmbedder | None:
    """Return the registered embedder by name, or None."""
    return _REGISTRY.get(name)


def list_embedders() -> list[FaceEmbedder]:
    """Return all registered embedders in insertion order."""
    return _REGISTRY.list_all()


def iter_embedders() -> Iterator[FaceEmbedder]:
    """Iterator alternative."""
    return _REGISTRY.iter_all()


def run_optional_embedder(
    name: str,
    image: np.ndarray,
    face_box: tuple[int, int, int, int],
) -> np.ndarray | None:
    """Run a registered embedder by name. Returns None if the
    embedder isn't registered or returns None itself."""
    embedder = get_embedder(name)
    if embedder is None:
        return None
    return embedder.embed(image, face_box)
