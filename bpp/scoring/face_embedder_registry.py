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
        registry_id="my_arcface",  # a ModelEntry you register_entry()'d
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

from bpp.constants import FACE_CLUSTER_THRESHOLD_FALLBACK
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

    ``registry_id``: id of the :mod:`bpp.registry` entry for the
        embedder's weights. The license is read from that entry.

    ``description``: one-line tooltip text. Optional.

    ``cluster_threshold``: the grouping distance used when the user has
        not picked one for this embedder (same metric as the stored
        embeddings, lower = stricter). Each embedder spreads faces
        differently, so one shared default groups well for some and
        merges strangers for others; measure yours on labelled photos.
    """

    name: str
    embed: EmbedFn
    embedding_dim: int
    registry_id: str
    description: str = ""
    cluster_threshold: float = FACE_CLUSTER_THRESHOLD_FALLBACK

    @property
    def license_label(self) -> str:
        """Plain-English license, derived from the registry entry."""
        from bpp.registry.labels import license_label_for

        return license_label_for(self.registry_id)


_REGISTRY: _ScoringRegistry[FaceEmbedder] = _ScoringRegistry("face embedder", _log)


#: Names of the built-in embedders. A plugin can't register under one of
#: them: the pipeline would ignore it (or, for an embedder, tag the
#: built-in's stored rows with the plugin's registry id).
BUILTIN_NAMES: set[str] = set()


def register_embedder(embedder: FaceEmbedder, *, builtin: bool = False) -> None:
    """Add an embedder to the registry. Re-registering a name replaces it,
    except that only built-in code (``builtin=True``) may use a
    built-in name."""
    if builtin:
        BUILTIN_NAMES.add(embedder.name)
    elif embedder.name in BUILTIN_NAMES:
        raise ValueError(
            f"{embedder.name!r} is a built-in face embedder name; register the "
            "plugin under its own name."
        )
    _log.debug(
        "Registered face embedder %r (dim=%d, registry_id=%s)",
        embedder.name,
        embedder.embedding_dim,
        embedder.registry_id,
    )
    _REGISTRY.register(embedder, embedder.name)


#: Size of the default (SFace / dlib) embedding, and of embeddings from
#: an unknown method.
DEFAULT_EMBEDDING_DIM = 128


def embedding_dim_for(method: str | None) -> int:
    """Number of floats an embedding made by ``method`` has — the
    embedder's declared ``embedding_dim`` (one source for the size
    check on write, in the startup cleanup and on read)."""
    import bpp.scoring.face_embed  # noqa: F401 — registers the built-ins

    embedder = get_embedder(method) if method else None
    return embedder.embedding_dim if embedder is not None else DEFAULT_EMBEDDING_DIM


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
