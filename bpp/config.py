"""Configuration loading and defaults for bpp."""

from __future__ import annotations

import os
from typing import Any

from bpp.constants import CLIP_MODEL_NAME, MODEL_TOGGLE_KEYS
from bpp.scoring.registry import get_weight_defaults

DEFAULTS: dict[str, Any] = {
    # Scoring weights (0.0-1.0, should sum to 1.0) — driven by scorer registry
    **get_weight_defaults(),
    # Image processing
    "max_long_side": 1024,  # px, max dimension for scoring resize (256-4096)
    "thumbnail_size": 64,  # px, grid thumbnail size
    # Selection
    "default_selection_k": 50,  # number of photos to select (1-10000)
    "default_selection_seed": 42,  # RNG seed for reproducibility
    # Dedup — perceptual hash distance (0=exact, higher=more lenient)
    "hash_distance_threshold": 20,  # within time window (0-64)
    "time_window_seconds": 30,  # seconds between shots for near-dedup
    "global_hash_distance_threshold": 10,  # across all photos (0-64)
    # Diversity — per-day/month caps for selection variety
    "max_per_day": 3,  # max photos from one day (0=unlimited)
    "min_per_month": 1,  # guaranteed minimum from each month
    "max_per_month": 0,  # 0 = unlimited
    # Face detection
    "face_detection_confidence": 0.3,  # min confidence (0.0-1.0)
    # Face recognition / embedding
    "face_embedding_confidence": 0.65,  # min quality for embedding (0.0-1.0)
    "min_face_area_pct": 0.20,  # min face area as % of image (0.05-1.0)
    "min_embedding_quality": 0.25,  # min quality score for embedding (0.1-0.5)
    "face_cluster_threshold": 0.80,  # clustering distance (0.3-1.2, lower=stricter)
    "face_selection_boost": 0.15,  # score boost for selected faces (0.0-1.0)
    # Groups (people who appear together)
    "group_min_photos": 3,  # photos two people must share to form a group (1-10)
    # Pet detection
    "pet_detection_confidence": 0.2,  # min confidence (0.0-1.0)
    "pet_input_size": 1024,  # YOLO input resolution (640-2048)
    # Sensitive photos (NudeNet flag / user override): whether they
    # compete in auto-picks ("allow", default) or are filtered out
    # ("exclude"). Quality scoring is nudity-agnostic; this is purely a
    # pick-time policy (see recompute()).
    "sensitive_in_picks": "allow",
    # NudeNet confidence at/above which a photo is flagged "may be
    # sensitive" (feeds both is_sensitive_item and the Sensitive smart
    # album). 0.7 default — see SENSITIVE_NUDITY_THRESHOLD in constants.py
    # for the calibration. Higher = fewer false positives.
    "sensitive_nudity_threshold": 0.7,
    # Selection diversity (CLIP cosine similarity; hash fallback distance)
    "selection_similarity_threshold": 0.85,  # dedupe threshold (0.0-1.0)
    # CLIP semantic dedup
    "clip_similarity_threshold": 0.92,  # near-duplicate threshold (0.0-1.0)
    "clip_model_name": CLIP_MODEL_NAME,
    # Plugin-extensible dedup strategy override. None = auto-pick
    # (the runtime resolves the best built-in for the available
    # embeddings). Setting this to a registered strategy name
    # (built-in or plugin-provided) forces that strategy; see
    # bpp/dedupe/strategy.py for the contract and
    # bpp/plugins/example.py for a plugin registration example.
    "dedupe_strategy": None,
    # Security
    "follow_symlinks": False,  # whether to follow symlinks during scan
    # File extensions accepted by import / scan / serve. Comma-separated
    # string so YAML can override cleanly. was hardcoded in
    # WebAppState.__init__ + bpp/io_scan.py + bpp/cli.py — moved here so
    # plugin contributors who add (say) AVIF support can extend via
    # config rather than patching three call sites.
    "scan_extensions": "jpg,jpeg,png,heic",
    # Model toggles (all enabled by default)
    **{k: True for k in MODEL_TOGGLE_KEYS},
}


def parse_scan_extensions(value: str | list[str] | None) -> list[str]:
    """Normalise ``scan_extensions`` into a list of bare extensions
    (no leading dot, lowercased). Accepts a comma-separated string
    (canonical YAML form), a pre-split list, or None (returns the
    DEFAULTS list)."""
    if value is None:
        value = DEFAULTS["scan_extensions"]
    if isinstance(value, str):
        parts = [p.strip().lstrip(".").lower() for p in value.split(",")]
    else:
        parts = [str(p).strip().lstrip(".").lower() for p in value]
    return [p for p in parts if p]


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load config from YAML file, merged over defaults.

    each key is validated through
    ``bpp.config_schema.validate_value`` before merging. ``Config.set()``
    already validated runtime writes; the YAML path previously bypassed
    that gate entirely so a typo like
    ``face_detection_confidence: -42`` booted silently and corrupted
    downstream code. Now bad values fail loudly at startup.

    Unschematized keys (plugin settings without a registered schema
    entry) pass through unchanged — the registry is additive, not
    mandatory.
    """
    # Lazy import to avoid a circular dependency at module load.
    from bpp.config_schema import validate_value

    config = dict(DEFAULTS)
    if path is not None:
        import yaml

        abspath = os.path.abspath(path)
        with open(abspath) as f:
            user = yaml.safe_load(f)
        if isinstance(user, dict):
            for key, value in user.items():
                # `validate_value` returns the (possibly coerced)
                # value for schematized keys and passes through
                # unschematized keys verbatim. A bad value raises
                # `ConfigValidationError` (a `ValueError` subclass),
                # which propagates up to the CLI so the user sees
                # the bad-key reason and can fix the YAML.
                config[key] = validate_value(key, value)
    return config
