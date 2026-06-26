"""Schema + validation registry for config values.

``bpp.config.DEFAULTS`` is a plain dict — no metadata
about valid ranges, no enum constraints, no UI hints. ``Config.set()``
accepted any value of any type for any key:

  ctx.config.set("face_detection_confidence", -42)   # negative confidence
  ctx.config.set("default_selection_k", "fifty")     # string for an int
  ctx.config.set("misspeled_key", True)              # silent typo creates a row

The first two corrupt downstream code (face detection threshold
goes negative; recompute crashes with TypeError on int(\"fifty\")).
The third is a typo footgun — ``.get("misspelled_key")`` reads
back the row, but ``.get("misspeled_key")`` falls through to
DEFAULTS.

This module is the single source of truth for what's settable,
what types / ranges are valid, and what the UI / CLI should show
when surfacing the option. ``Config.set()`` routes through
``validate_value()`` so a misconfig fails at write time rather
than mysteriously corrupting a later read.

Plugin authors register their own settings the same way scoring
plugins register weighted scorers — one ``register_field()`` call
per new key. The shape is forward-compatible; ``ui_type`` and
``choices`` aren't read by any code yet but are reserved for a
future ``/api/v1/settings/schema`` endpoint.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from bpp.errors import BppError
from bpp.utils.logging import get_logger

_log = get_logger(__name__)


class ConfigValidationError(BppError, ValueError):
    """Raised when a value fails the schema's validation contract.

    Carries the key + reason so the caller can surface a useful
    error to the operator (CLI / API). Inherits ``BppError`` (P7)
    so ``except BppError`` catches it; inherits ``ValueError`` so
    pre-P7 ``try / except ValueError`` catches still work.
    """

    http_status = 400
    code = "config_validation_error"

    def __init__(self, key: str, reason: str) -> None:
        self.key = key
        self.reason = reason
        super().__init__(f"config[{key!r}]: {reason}", key=key, reason=reason)


@dataclass(frozen=True)
class ConfigField:
    """Metadata for a single config key.

    Required:
      ``key`` — the setting name (matches the DEFAULTS dict).
      ``type`` — the Python type the resolver should coerce DB
      strings to (also enforced on write).

    Optional (UI / validation):
      ``label`` — human-friendly name for the Settings UI.
      ``description`` — one-sentence explanation. Surface in
        tooltips / API docs.
      ``min`` / ``max`` — numeric bounds for int / float fields
        (inclusive). Ignored for non-numeric types.
      ``choices`` — closed enumeration of allowed values. If set,
        any other value fails validation.
      ``ui_type`` — UI rendering hint (``slider`` / ``toggle`` /
        ``text`` / ``select``). Reserved for a future
        /api/v1/settings/schema endpoint; not consumed today.
      ``category`` — optional grouping for the UI ("Scoring",
        "Dedup", "Face detection", ...).
      ``validator`` — escape hatch for keys whose constraints
        don't fit min/max/choices (e.g. \"comma-separated list of
        valid file extensions\"). Receives the candidate value
        AFTER type coercion; raises ConfigValidationError to
        reject.
    """

    key: str
    type: type
    label: str = ""
    description: str = ""
    min: float | int | None = None
    max: float | int | None = None
    choices: tuple[Any, ...] | None = None
    ui_type: str = "text"
    category: str = ""
    validator: Callable[[Any], None] | None = field(default=None, repr=False)


_SCHEMA_REGISTRY: dict[str, ConfigField] = {}


def register_field(field: ConfigField) -> None:
    """Add a schema entry. Idempotent — re-registering the same key
    replaces the existing entry (useful for tests; in production
    the registry is populated once at import time).

    debug-log on registration so a plugin
    author can confirm their config field landed in the registry.
    """
    replacing = field.key in _SCHEMA_REGISTRY
    _SCHEMA_REGISTRY[field.key] = field
    _log.debug(
        "Registered config field %r (type=%s, category=%s)%s",
        field.key,
        field.type.__name__,
        field.category or "uncategorized",
        " — replacing existing entry" if replacing else "",
    )


def get_schema(key: str) -> ConfigField | None:
    """Return the registered ConfigField for ``key``, or None if
    the key has no schema entry. None means \"unconstrained\" —
    Config.set() falls back to its previous accept-anything
    behavior so unschematized keys (and plugin keys without a
    schema) keep working."""
    return _SCHEMA_REGISTRY.get(key)


def iter_schema() -> Iterator[ConfigField]:
    """Iterate every registered schema entry. Used by the
    (future) /api/v1/settings/schema endpoint and by tests that
    spot-check coverage."""
    yield from _SCHEMA_REGISTRY.values()


def validate_value(key: str, value: Any) -> Any:
    """Coerce ``value`` to the field's declared type and check
    the bounds / choices / validator constraints. Returns the
    coerced value on success.

    Unschematized keys pass through unchanged (the registry is
    additive — keys without entries keep the previous behavior).

    Raises ``ConfigValidationError`` with a specific reason on:
      - type coercion failure
      - bound violation (min / max)
      - choices mismatch
      - validator-raised failure
    """
    schema = get_schema(key)
    if schema is None:
        return value

    coerced = _coerce_for_schema(key, value, schema.type)

    if schema.choices is not None and coerced not in schema.choices:
        raise ConfigValidationError(
            key, f"value {coerced!r} not in allowed choices {list(schema.choices)!r}"
        )

    if isinstance(coerced, (int, float)) and not isinstance(coerced, bool):
        if schema.min is not None and coerced < schema.min:
            raise ConfigValidationError(key, f"value {coerced} below minimum {schema.min}")
        if schema.max is not None and coerced > schema.max:
            raise ConfigValidationError(key, f"value {coerced} above maximum {schema.max}")

    if schema.validator is not None:
        # Validator raises ConfigValidationError on its own to keep
        # the error surface uniform.
        schema.validator(coerced)

    return coerced


def _coerce_for_schema(key: str, value: Any, target_type: type) -> Any:
    """Type-coerce on write. Mirrors the read-side coercion in
    ``bpp.config_resolver._coerce`` but stricter — a failed coerce
    on write raises ConfigValidationError instead of silently
    yielding 0 / 0.0 / "".
    """
    if isinstance(value, target_type) and not (target_type is int and isinstance(value, bool)):
        # `bool` is a subclass of `int` in Python, so `isinstance(True, int)`
        # is True. Reject bool-for-int when target is int specifically.
        return value

    if target_type is bool:
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "1", "yes", "on"):
                return True
            if low in ("false", "0", "no", "off"):
                return False
            raise ConfigValidationError(key, f"cannot coerce {value!r} to bool")
        if isinstance(value, (int, float)):
            return bool(value)
        raise ConfigValidationError(
            key, f"cannot coerce {value!r} (type {type(value).__name__}) to bool"
        )

    if target_type is int:
        if isinstance(value, bool):
            raise ConfigValidationError(key, f"refused bool {value!r} for int field")
        try:
            return int(value)
        except (TypeError, ValueError) as e:
            raise ConfigValidationError(key, f"cannot coerce {value!r} to int: {e}") from None

    if target_type is float:
        try:
            return float(value)
        except (TypeError, ValueError) as e:
            raise ConfigValidationError(key, f"cannot coerce {value!r} to float: {e}") from None

    if target_type is str:
        return str(value)

    # Unknown target type — accept as-is. Plugin authors who use
    # exotic types (lists, custom dataclasses) own the validation
    # themselves via the `validator` callback.
    return value


# ─── Built-in schema registrations ────────────────────────────
#
# Populates the registry for every numerically-bounded config key
# in DEFAULTS. The bounds match the inline comments in config.py
# (kept in sync; if you change one, change the other).


def _register_builtins() -> None:
    """Idempotent populate-the-registry routine. Called at import
    time, also from tests that need to reset state."""
    fields = [
        # Image processing
        ConfigField(
            key="max_long_side",
            type=int,
            label="Max scoring resolution",
            description="Photos are downscaled to this max dimension before scoring.",
            min=256,
            max=4096,
            ui_type="slider",
            category="Performance",
        ),
        ConfigField(
            key="thumbnail_size",
            type=int,
            label="Grid thumbnail size",
            min=32,
            max=512,
            ui_type="slider",
            category="UI",
        ),
        # Selection
        ConfigField(
            key="default_selection_k",
            type=int,
            label="Default selection size",
            description="How many photos to select per album by default.",
            min=1,
            max=10000,
            ui_type="slider",
            category="Selection",
        ),
        ConfigField(
            key="default_selection_seed",
            type=int,
            label="Selection RNG seed",
            description="Reproducibility seed for the greedy chooser.",
            ui_type="text",
            category="Selection",
        ),
        # Dedup
        ConfigField(
            key="hash_distance_threshold",
            type=int,
            min=0,
            max=64,
            ui_type="slider",
            category="Dedup",
        ),
        ConfigField(
            key="time_window_seconds",
            type=int,
            min=0,
            max=600,
            ui_type="slider",
            category="Dedup",
        ),
        ConfigField(
            key="global_hash_distance_threshold",
            type=int,
            min=0,
            max=64,
            ui_type="slider",
            category="Dedup",
        ),
        # Diversity
        ConfigField(
            key="max_per_day",
            type=int,
            min=0,
            max=1000,
            ui_type="slider",
            category="Diversity",
        ),
        ConfigField(
            key="min_per_month",
            type=int,
            min=0,
            max=1000,
            ui_type="slider",
            category="Diversity",
        ),
        ConfigField(
            key="max_per_month",
            type=int,
            min=0,
            max=10000,
            ui_type="slider",
            category="Diversity",
        ),
        # Face detection
        ConfigField(
            key="face_detection_confidence",
            type=float,
            label="Face detection confidence",
            min=0.0,
            max=1.0,
            ui_type="slider",
            category="Face detection",
        ),
        ConfigField(
            key="face_embedding_confidence",
            type=float,
            min=0.0,
            max=1.0,
            ui_type="slider",
            category="Face detection",
        ),
        ConfigField(
            key="min_face_area_pct",
            type=float,
            min=0.05,
            max=1.0,
            ui_type="slider",
            category="Face detection",
        ),
        ConfigField(
            key="min_embedding_quality",
            type=float,
            min=0.1,
            max=0.5,
            ui_type="slider",
            category="Face detection",
        ),
        ConfigField(
            key="face_cluster_threshold",
            type=float,
            min=0.3,
            max=1.2,
            ui_type="slider",
            category="Face detection",
        ),
        ConfigField(
            key="face_selection_boost",
            type=float,
            min=0.0,
            max=1.0,
            ui_type="slider",
            category="Selection",
        ),
        ConfigField(
            key="group_min_photos",
            type=int,
            label="Group detection",
            description="Photos two people must share before they form a group",
            min=1,
            max=10,
            ui_type="slider",
            category="Face detection",
        ),
        # Pet detection
        ConfigField(
            key="pet_detection_confidence",
            type=float,
            min=0.0,
            max=1.0,
            ui_type="slider",
            category="Pet detection",
        ),
        ConfigField(
            key="pet_input_size",
            type=int,
            min=640,
            max=2048,
            ui_type="slider",
            category="Pet detection",
        ),
        # Content filter
        ConfigField(
            key="sensitive_in_picks",
            type=str,
            label="Sensitive photos in auto-picks",
            description=(
                "Whether photos flagged sensitive compete in auto-picks "
                "('allow') or are filtered out ('exclude'). Manual force-"
                "includes are always kept."
            ),
            choices=("allow", "exclude"),
            ui_type="select",
            category="Content filter",
        ),
        ConfigField(
            key="sensitive_nudity_threshold",
            type=float,
            label="Sensitive flag threshold",
            description=(
                "NudeNet confidence at/above which a photo is flagged "
                "'may be sensitive'. Higher = fewer false positives."
            ),
            min=0.0,
            max=1.0,
            ui_type="slider",
            category="Content filter",
        ),
        # Selection diversity
        ConfigField(
            key="selection_similarity_threshold",
            type=float,
            min=0.0,
            max=1.0,
            ui_type="slider",
            category="Dedup",
        ),
        ConfigField(
            key="clip_similarity_threshold",
            type=float,
            min=0.0,
            max=1.0,
            ui_type="slider",
            category="Dedup",
        ),
        # Security
        ConfigField(
            key="follow_symlinks",
            type=bool,
            label="Follow symlinks during scan",
            ui_type="toggle",
            category="Security",
        ),
        # Scan extensions
        ConfigField(
            key="scan_extensions",
            type=str,
            label="File extensions to import",
            description="Comma-separated, no leading dots.",
            ui_type="text",
            category="Import",
        ),
        # Deployment
        ConfigField(
            key="behind_proxy",
            type=bool,
            label="Behind reverse proxy",
            description=(
                "Enable ProxyFix to read X-Forwarded-For/Proto from a trusted "
                "reverse proxy. Requires BPP_TRUSTED_PROXIES to be set — "
                "enabling without it is refused at startup."
            ),
            ui_type="toggle",
            category="Deployment",
        ),
    ]
    for f in fields:
        register_field(f)


_register_builtins()
