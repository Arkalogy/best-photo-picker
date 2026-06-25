"""Dedup strategy registry — built-in + plugin strategies.

Bpp's recompute pipeline auto-picks a strategy based on what's
available (CLIP embeddings → semantic, otherwise → phash). Plugins
can register additional named strategies via
`register_dedupe_strategy()`; the user opts in by setting the
`dedupe_strategy` config key to the strategy name, which overrides
the auto-pick.

Strategy contract:

    dedupe_fn(items, config, **kwargs) -> list[dict]

Where ``items`` is the per-photo analysis list (each dict has
``filepath``, ``aggregate_score``, optional ``phash``/``ahash``,
optional ``id``/``date``/``date_month``), ``config`` is the merged
runtime config dict, and ``kwargs`` may include ``clip_embeddings``
(``dict[int, np.ndarray]`` keyed by photo id) when those are
already loaded.

The return value is the per-cluster representative list. Each
representative SHOULD set ``cluster_size`` (count of photos folded
into the cluster, 1 = singleton); semantic strategies usually also
attach a ``similar_photos`` list of dicts the lightbox renders.

Plugins must:
  * Use a plugin-prefixed name (built-in names ``hash``, ``clip``,
    ``none`` are reserved).
  * Tolerate missing kwargs — if your strategy needs CLIP
    embeddings, declare ``requires_clip_embeddings=True`` so the
    runner skips you when none are loaded; or call with a default.
  * Return a list (never None). Empty input → empty output.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from bpp.utils.logging import get_logger

log = get_logger(__name__)


# Plugin strategies must NOT use these names; they're reserved for
# built-ins so the auto-pick router and config override stay
# unambiguous.
_BUILTIN_STRATEGY_NAMES: frozenset[str] = frozenset({"hash", "clip", "none"})


@dataclass(frozen=True)
class DedupeStrategy:
    """Public dedup-strategy descriptor.

    ``name`` is the user-facing key the ``dedupe_strategy`` config
    selects by. Plugin strategies must use a plugin-prefixed name.

    ``dedupe_fn`` is the per-call worker. Signature:

        dedupe_fn(
            items: list[dict],
            config: dict,
            *,
            clip_embeddings: dict[int, np.ndarray] | None = None,
            **_unused,
        ) -> list[dict]

    ``description`` is a one-line UI/log summary.

    ``requires_clip_embeddings`` lets the runner skip a strategy
    when CLIP embeddings are unavailable instead of letting it crash
    on a None lookup. The auto-pick router uses this flag to fall
    through to the next-best built-in.

    ``is_builtin`` distinguishes shipped-with-bpp strategies from
    plugin-registered ones. ``_reset_for_tests`` preserves built-ins
    so test isolation only drops plugin entries.
    """

    name: str
    dedupe_fn: Callable[..., list[dict]]
    description: str = ""
    requires_clip_embeddings: bool = False
    is_builtin: bool = False


class DedupeStrategyRegistry:
    """Registry of named dedup strategies (built-in + plugin)."""

    _strategies: ClassVar[dict[str, DedupeStrategy]] = {}

    @classmethod
    def register(cls, strategy: DedupeStrategy, *, replace: bool = False) -> None:
        existing = cls._strategies.get(strategy.name)
        if existing is not None and not replace:
            if existing.is_builtin:
                raise ValueError(
                    f"Cannot register dedup strategy {strategy.name!r}: "
                    "name is reserved for a built-in (pass replace=True "
                    "to override, but expect tests to scream)"
                )
            if existing.dedupe_fn is not strategy.dedupe_fn:
                raise ValueError(
                    f"Dedup strategy {strategy.name!r} already registered "
                    "with a different function (pass replace=True if "
                    "intentional)"
                )
        cls._strategies[strategy.name] = strategy

    @classmethod
    def get(cls, name: str) -> DedupeStrategy | None:
        return cls._strategies.get(name)

    @classmethod
    def all(cls) -> list[DedupeStrategy]:
        return list(cls._strategies.values())

    @classmethod
    def names(cls) -> list[str]:
        return list(cls._strategies.keys())

    @classmethod
    def _reset_for_tests(cls) -> None:
        """Drop plugin-registered strategies; keep built-ins."""
        for name in list(cls._strategies.keys()):
            if not cls._strategies[name].is_builtin:
                del cls._strategies[name]


def register_dedupe_strategy(strategy: DedupeStrategy, *, replace: bool = False) -> None:
    """Public plugin entry point — add a custom dedup strategy.

    Plugins must use a plugin-prefixed name (built-in names ``hash``,
    ``clip``, ``none`` are reserved). The user opts in to a plugin
    strategy by setting ``dedupe_strategy: <name>`` in the bpp config
    (YAML or DB-backed), which overrides the auto-pick.

    See ``DedupeStrategy`` and ``bpp/dedupe/strategy.py`` for the
    contract details.
    """
    if strategy.name in _BUILTIN_STRATEGY_NAMES and not replace and not strategy.is_builtin:
        raise ValueError(
            f"Dedup strategy name {strategy.name!r} is reserved for a "
            "built-in. Plugins must use a prefixed name like "
            "'myplugin_<strategy>' to avoid namespace collisions."
        )
    DedupeStrategyRegistry.register(strategy, replace=replace)
    log.info(
        "Registered dedup strategy %r (requires_clip=%s, is_builtin=%s)",
        strategy.name,
        strategy.requires_clip_embeddings,
        strategy.is_builtin,
    )


# ---------------------------------------------------------------------------
# Built-in strategies. The wrappers exist so the registry can dispatch with a
# uniform signature; the underlying dedup functions stay backwards-compatible
# for any external callers (CLI `bpp pick`, etc.).
# ---------------------------------------------------------------------------


def _hash_strategy(
    items: list[dict],
    config: dict,
    **_kwargs: object,
) -> list[dict]:
    """phash + aHash time-windowed dedup. Imported lazily to avoid cycles."""
    from bpp.dedupe.cluster import deduplicate

    return deduplicate(items, config=config)


def _clip_strategy(
    items: list[dict],
    config: dict,
    *,
    clip_embeddings: dict | None = None,
    **_kwargs: object,
) -> list[dict]:
    """CLIP semantic dedup. Requires loaded embeddings; raises if absent."""
    from bpp.dedupe.semantic import semantic_deduplicate

    if clip_embeddings is None:
        raise ValueError(
            "Dedup strategy 'clip' requires clip_embeddings; "
            "fall back to 'hash' or load embeddings first."
        )
    threshold = config.get("clip_similarity_threshold", 0.92)
    return semantic_deduplicate(items, clip_embeddings, threshold=threshold, config=config)


def _none_strategy(
    items: list[dict],
    _config: dict,
    **_kwargs: object,
) -> list[dict]:
    """Skip dedup — return every item with cluster_size=1."""
    out: list[dict] = []
    for item in items:
        item["cluster_size"] = 1
        out.append(item)
    return out


# Pre-register built-ins at import time. Plugins extend the registry from
# their setup() callable.
DedupeStrategyRegistry.register(
    DedupeStrategy(
        name="hash",
        dedupe_fn=_hash_strategy,
        description=("Perceptual-hash dedup (dHash + aHash) with EXIF time windowing."),
        is_builtin=True,
    )
)
DedupeStrategyRegistry.register(
    DedupeStrategy(
        name="clip",
        dedupe_fn=_clip_strategy,
        description="CLIP semantic-similarity dedup (cosine distance).",
        requires_clip_embeddings=True,
        is_builtin=True,
    )
)
DedupeStrategyRegistry.register(
    DedupeStrategy(
        name="none",
        dedupe_fn=_none_strategy,
        description="Skip dedup — every photo is its own cluster.",
        is_builtin=True,
    )
)


# ---------------------------------------------------------------------------
# Dispatch helper — used by recompute.py to honor the dedupe_strategy config
# override before falling back to the auto-pick logic.
# ---------------------------------------------------------------------------


def resolve_strategy(
    name: str | None,
    *,
    have_clip_embeddings: bool,
) -> DedupeStrategy | None:
    """Return the strategy to invoke given a config name + availability.

    - ``name`` is whatever the ``dedupe_strategy`` config key holds (None
      means auto-pick — return None and let the caller use its existing
      auto-pick logic).
    - If a name is set but the strategy ``requires_clip_embeddings`` and
      none are loaded, log a warning and return None so the caller falls
      back to its default. Better to dedup with the wrong strategy than
      to crash a recompute.
    - Unknown names also return None with a warning. Same fall-back
      reasoning.
    """
    if not name:
        return None
    strategy = DedupeStrategyRegistry.get(name)
    if strategy is None:
        log.warning(
            "Configured dedup strategy %r is not registered — falling back "
            "to auto-pick. Built-ins: hash, clip, none. Plugins must "
            "register via bpp.dedupe.strategy.register_dedupe_strategy.",
            name,
        )
        return None
    if strategy.requires_clip_embeddings and not have_clip_embeddings:
        log.warning(
            "Configured dedup strategy %r requires CLIP embeddings but "
            "none are loaded — falling back to auto-pick.",
            name,
        )
        return None
    return strategy
