"""The built-in config fields, by category.

Populates the registry in :mod:`bpp.config_schema` for every
numerically-bounded config key in DEFAULTS. The bounds match the inline
comments in config.py (kept in sync; if you change one, change the
other). One function per settings category; :func:`builtin_fields` is
the whole list in registration order.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bpp.config_schema import ConfigField


def builtin_fields() -> list[ConfigField]:
    # config_schema registers these at ITS import time, so it is imported
    # here, at call time, never at module top: either module may be
    # imported first.
    from bpp.config_schema import ConfigField

    return [
        *_performance_and_ui(ConfigField),
        *_selection(ConfigField),
        *_dedup(ConfigField),
        *_diversity(ConfigField),
        *_faces(ConfigField),
        *_pets(ConfigField),
        *_content_filter(ConfigField),
        *_similarity(ConfigField),
        *_security_import_deployment(ConfigField),
    ]


def _performance_and_ui(ConfigField: type[ConfigField]) -> list[ConfigField]:
    return [
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
    ]


def _selection(ConfigField: type[ConfigField]) -> list[ConfigField]:
    return [
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
    ]


def _dedup(ConfigField: type[ConfigField]) -> list[ConfigField]:
    return [
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
    ]


def _diversity(ConfigField: type[ConfigField]) -> list[ConfigField]:
    return [
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
    ]


def _faces(ConfigField: type[ConfigField]) -> list[ConfigField]:
    return [
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
    ]


def _pets(ConfigField: type[ConfigField]) -> list[ConfigField]:
    return [
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
    ]


def _content_filter(ConfigField: type[ConfigField]) -> list[ConfigField]:
    return [
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
    ]


def _similarity(ConfigField: type[ConfigField]) -> list[ConfigField]:
    return [
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
    ]


def _security_import_deployment(ConfigField: type[ConfigField]) -> list[ConfigField]:
    return [
        ConfigField(
            key="follow_symlinks",
            type=bool,
            label="Follow symlinks during scan",
            ui_type="toggle",
            category="Security",
        ),
        ConfigField(
            key="scan_extensions",
            type=str,
            label="File extensions to import",
            description="Comma-separated, no leading dots.",
            ui_type="text",
            category="Import",
        ),
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
