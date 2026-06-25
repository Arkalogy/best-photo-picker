"""Central registry of scorer metadata + optional-scorer dispatch.

Adding a new REQUIRED scorer (always runs, like blur/exposure/face)?
Add ONE entry to _SCORER_REGISTRY below for metadata, then call the
scorer fn manually in analyze_single_image() — required scorers have
heterogeneous signatures (some need detected faces, some need
filepath, some take an ndarray).

Adding a new OPTIONAL ML scorer (gated by a toggle, may be missing
deps, like nudity/pets)? Set `optional=True` plus the toggle/avail/
score callables in the registry entry — `run_optional_scorers()`
dispatches them uniformly. No edit to analyze_single_image().

The registry also drives: compute_aggregate weights, DB columns, API
serialization, config defaults, and video frame averaging.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from bpp.utils.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ScorerDef:
    """Metadata for a single scorer."""

    key: str  # e.g., "blur"
    weight_key: str | None = None  # e.g., "blur_weight" (None if not weighted)
    default_weight: float = 0.0  # Default weight in config
    aggregate_default: float = 0.5  # Fallback when score missing in aggregation
    db_columns: tuple[str, ...] = ()  # DB columns this scorer produces
    api_fields: dict[str, object] = field(default_factory=dict)  # field_name → default for API
    video_avg_keys: tuple[str, ...] = ()  # Keys to average across video frames

    # ── Optional-scorer dispatch (set when optional=True) ──
    # When all four are set, run_optional_scorers() will:
    #   1. Skip if model_toggles[toggle_key] is False
    #   2. Skip if is_available_fn() returns False (dep missing)
    #   3. Call score_fn(img, filepath, config) and merge result into
    #      the photo dict.
    # score_fn signature: (img: ndarray, filepath: str, config: dict)
    #     → dict[str, Any]. Receives both img and filepath so each
    #     scorer picks what it needs (nudity uses filepath, pets uses img).
    optional: bool = False
    toggle_key: str | None = None
    is_available_fn: Callable[[], bool] | None = None
    score_fn: Callable[[Any, str, dict[str, Any]], dict[str, Any]] | None = None


_SCORER_REGISTRY: dict[str, ScorerDef] = {
    "blur": ScorerDef(
        key="blur",
        weight_key="blur_weight",
        default_weight=0.30,
        aggregate_default=0.5,
        db_columns=("blur_raw", "blur_score"),
        api_fields={"blur_score": 0},
        video_avg_keys=("blur_raw",),
    ),
    "exposure": ScorerDef(
        key="exposure",
        weight_key="exposure_weight",
        default_weight=0.20,
        aggregate_default=0.5,
        db_columns=("exposure_score",),
        api_fields={"exposure_score": 0},
        video_avg_keys=("exposure_score",),
    ),
    "face": ScorerDef(
        key="face",
        weight_key="face_weight",
        default_weight=0.35,
        aggregate_default=0.2,
        db_columns=("face_score", "face_count", "largest_face_ratio", "face_center_dist"),
        api_fields={"face_score": 0, "face_count": 0, "largest_face_ratio": 0},
        video_avg_keys=("face_score", "face_count", "largest_face_ratio", "face_center_dist"),
    ),
    "composition": ScorerDef(
        key="composition",
        weight_key="composition_weight",
        default_weight=0.15,
        aggregate_default=0.5,
        db_columns=("composition_score",),
        api_fields={"composition_score": 0},
        video_avg_keys=("composition_score",),
    ),
    "skin": ScorerDef(
        key="skin",
        db_columns=("skin_score",),
        api_fields={"skin_score": 0},
        video_avg_keys=("skin_score",),
    ),
    "nudity": ScorerDef(
        key="nudity",
        db_columns=("nudity_score",),
        api_fields={"nudity_score": None},  # None = optional, can be absent
        optional=True,
        toggle_key="model_nudity",
        # is_available_fn + score_fn wired below to avoid circular imports
    ),
    "pets": ScorerDef(
        key="pets",
        db_columns=("pet_count", "has_cat", "has_dog"),
        api_fields={"pet_count": 0, "has_cat": False, "has_dog": False},
        optional=True,
        toggle_key="model_pets",
    ),
}


# Built-in keys reserved against accidental plugin shadowing. Plugins
# must use a plugin-prefixed key (e.g. `myplugin_saturation`) to keep
# the global namespace clean and to make it obvious in logs which
# scorer came from where.
_BUILTIN_SCORER_KEYS: frozenset[str] = frozenset(
    {"blur", "exposure", "face", "composition", "skin", "nudity", "pets", "aggregate"}
)


def register_scorer(scorer: ScorerDef, *, replace: bool = False) -> None:
    """Register a custom scorer (plugin entry point).

    Plugin-registered scorers run inside ``run_optional_scorers()``
    during analyze and their returned dict is merged into the in-memory
    photo result. They differ from built-ins in three ways:

    1. **Always optional.** ``optional=True`` and ``score_fn`` are
       required. Plugins don't have access to bpp's lazy-wire path
       (``_wire_optional_scorers``); they wire themselves at
       registration time.
    2. **In-memory only.** ``db_columns`` MUST be empty — plugins
       cannot add columns to the ``photos`` table. If a plugin needs
       persistence, write to its own DB table. Plugin api_fields
       reflect live values during the analyze run; on subsequent
       loads from DB the fields are absent (treated as defaults
       through ``api_fields``).
    3. **Aggregate effect, if weighted.** If ``weight_key`` is set,
       the plugin scorer joins ``compute_aggregate`` with its
       ``default_weight`` (which the user can override via YAML
       config; the Settings UI sliders are static and won't show
       plugin scorers). Setting a non-zero default_weight will
       re-normalize ``aggregate_score`` for every existing photo on
       the next analyze — disclose this in your plugin README.

    Naming: ``key`` MUST be plugin-prefixed (e.g.
    ``myplugin_saturation``) to avoid colliding with built-ins.
    Reserved keys: blur, exposure, face, composition, skin, nudity,
    pets, aggregate.

    Idempotency: re-registering the same ScorerDef is a no-op (helps
    when a plugin's setup() runs more than once per process across
    library switches). A different ScorerDef for the same key raises;
    pass ``replace=True`` to swap intentionally.
    """
    if scorer.key in _BUILTIN_SCORER_KEYS and not replace:
        raise ValueError(
            f"Scorer key {scorer.key!r} is reserved for a built-in. "
            "Plugins must use a prefixed key like "
            f"'myplugin_{scorer.key}' to avoid namespace collisions."
        )
    if not scorer.optional:
        raise ValueError(
            "Plugin scorers must set optional=True (the lazy-wire path "
            "for built-ins is not available to plugins)."
        )
    if scorer.score_fn is None:
        raise ValueError("Plugin scorers must provide score_fn at registration time.")
    if scorer.db_columns:
        raise ValueError(
            f"Plugin scorers cannot declare db_columns ({scorer.db_columns!r}). "
            "Plugin score data lives in-memory only; bulk_upsert_photos "
            "would silently drop any column the photos table doesn't have. "
            "If you need persistence, manage your own DB table from your "
            "plugin code."
        )
    existing = _SCORER_REGISTRY.get(scorer.key)
    if existing is not None and existing != scorer and not replace:
        raise ValueError(
            f"Scorer {scorer.key!r} already registered with a different "
            "definition (pass replace=True if intentional)."
        )
    _SCORER_REGISTRY[scorer.key] = scorer
    log.info(
        "Registered scorer %r (weight_key=%s, default_weight=%s, fields=%s)",
        scorer.key,
        scorer.weight_key,
        scorer.default_weight,
        list(scorer.api_fields.keys()),
    )


def _reset_plugin_scorers_for_tests() -> None:
    """Drop every non-built-in scorer from the registry. Test-only."""
    for key in list(_SCORER_REGISTRY.keys()):
        if key not in _BUILTIN_SCORER_KEYS:
            del _SCORER_REGISTRY[key]


def _wire_optional_scorers() -> None:
    """Bind is_available_fn + score_fn to optional registry entries.

    Done lazily here (not in the literal dict above) because importing
    bpp.scoring.nudity / bpp.scoring.pets at module load would pull in
    optional ML deps before they're known to be installed. The actual
    `is_available()` checks return False fast when deps are missing.
    """
    # Already wired? Skip.
    if _SCORER_REGISTRY["nudity"].is_available_fn is not None:
        return

    from bpp.scoring.nudity import is_available as _nudity_avail
    from bpp.scoring.nudity import score_nudity as _score_nudity_raw
    from bpp.scoring.pets import detect_pets as _detect_pets
    from bpp.scoring.pets import is_available as _pets_avail

    def _nudity_score_fn(img: Any, filepath: str, config: dict[str, Any]) -> dict[str, Any]:
        # NudeNet runs on the file directly (it does its own decode).
        return {"nudity_score": _score_nudity_raw(filepath)}

    def _pets_score_fn(img: Any, filepath: str, config: dict[str, Any]) -> dict[str, Any]:
        pet_conf = config.get("pet_detection_confidence", 0.2)
        pet_size = int(config.get("pet_input_size", 1024))
        try:
            r = _detect_pets(img, input_size=pet_size, conf_threshold=pet_conf)
        except Exception:
            log.debug("Pet detection unavailable for %s", filepath, exc_info=True)
            return {}
        return {
            "pet_count": r["pet_count"],
            "has_cat": r["has_cat"],
            "has_dog": r["has_dog"],
            "pet_detections": r.get("pet_detections", []),
        }

    # Frozen dataclass — replace via dict-level reassignment.
    _SCORER_REGISTRY["nudity"] = ScorerDef(
        **{
            **_SCORER_REGISTRY["nudity"].__dict__,
            "is_available_fn": _nudity_avail,
            "score_fn": _nudity_score_fn,
        }
    )
    _SCORER_REGISTRY["pets"] = ScorerDef(
        **{
            **_SCORER_REGISTRY["pets"].__dict__,
            "is_available_fn": _pets_avail,
            "score_fn": _pets_score_fn,
        }
    )


def run_optional_scorers(
    img: Any,
    filepath: str,
    model_toggles: dict[str, bool],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Run every optional scorer whose toggle is on AND deps are present.

    Returns a flat dict to merge into the photo's analysis result.
    Skipped scorers return nothing — callers can rely on missing keys
    rather than zero/null sentinels.
    """
    _wire_optional_scorers()
    out: dict[str, Any] = {}
    for s in _SCORER_REGISTRY.values():
        if not s.optional:
            continue
        if s.toggle_key and not model_toggles.get(s.toggle_key, True):
            log.debug("%s skipped (toggled off)", s.key)
            continue
        if s.is_available_fn is not None and not s.is_available_fn():
            log.debug("%s unavailable (deps missing)", s.key)
            continue
        if s.score_fn is None:
            continue
        try:
            out.update(s.score_fn(img, filepath, config))
        except Exception:
            # WARNING (not debug): a scorer crash silently strips its
            # columns from the analysis result, which skews scoring
            # without an obvious breadcrumb. Surfacing in /api/logs
            # tells operators which optional scorer to disable / repair.
            log.warning("%s scorer raised on %s", s.key, filepath, exc_info=True)
    return out


# ── Derived helpers ──


def get_weighted_scorers() -> list[ScorerDef]:
    """Scorers that contribute to aggregate_score via weights."""
    return [s for s in _SCORER_REGISTRY.values() if s.weight_key]


def get_weight_defaults() -> dict[str, float]:
    """Config DEFAULTS for scorer weights: {weight_key: default_weight}."""
    return {s.weight_key: s.default_weight for s in _SCORER_REGISTRY.values() if s.weight_key}


def get_score_db_columns() -> tuple[str, ...]:
    """All DB columns produced by scorers (for _SCORE_COLUMNS)."""
    cols: list[str] = []
    for s in _SCORER_REGISTRY.values():
        cols.extend(s.db_columns)
    # Always include aggregate_score at the end
    cols.append("aggregate_score")
    return tuple(cols)


def get_api_score_fields() -> dict[str, object]:
    """All score fields for API serialization with defaults."""
    fields: dict[str, object] = {}
    for s in _SCORER_REGISTRY.values():
        fields.update(s.api_fields)
    fields["aggregate_score"] = 0
    return fields


def get_video_avg_keys() -> list[str]:
    """Keys to average across video frames."""
    keys: list[str] = []
    for s in _SCORER_REGISTRY.values():
        keys.extend(s.video_avg_keys)
    return keys


def get_weight_keys() -> tuple[str, ...]:
    """Weight config keys for RECOMPUTE_WEIGHT_KEYS."""
    return tuple(s.weight_key for s in _SCORER_REGISTRY.values() if s.weight_key)
