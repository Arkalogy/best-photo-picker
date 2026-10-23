"""The one dedupe step every selection path runs.

``bpp select``, the web recompute and the weight optimizer all choose
from a deduplicated candidate list. The choice of strategy is made here
once: a strategy the user named in config (``dedupe_strategy``, a
plugin's or a built-in's name) wins; else CLIP similarity when embeddings
are at hand; else the caller's skip flag; else perceptual hashes.
"""

from __future__ import annotations

from typing import Any

from bpp.config import cfg_get
from bpp.dedupe.cluster import deduplicate
from bpp.dedupe.semantic import semantic_deduplicate
from bpp.dedupe.strategy import resolve_strategy
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def dedupe_candidates(
    items: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    clip_embeddings: Any = None,
    skip_dedupe: bool = False,
    clip_threshold: float | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Return ``(candidates, mode)``; ``mode`` names what ran (a strategy
    name, ``"clip"``, ``"skipped"`` or ``"hash"``)."""
    strategy = resolve_strategy(
        config.get("dedupe_strategy"), have_clip_embeddings=bool(clip_embeddings)
    )
    if strategy is not None:
        log.info(
            "Dedupe strategy %r (%s)",
            strategy.name,
            "built-in" if strategy.is_builtin else "plugin",
        )
        return strategy.dedupe_fn(items, config, clip_embeddings=clip_embeddings), strategy.name
    if clip_embeddings:
        threshold = clip_threshold or cfg_get(config, "clip_similarity_threshold")
        return (
            semantic_deduplicate(items, clip_embeddings, threshold=threshold, config=config),
            "clip",
        )
    if skip_dedupe:
        return items, "skipped"
    return deduplicate(items, config=config), "hash"
