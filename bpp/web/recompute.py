"""Pure recompute function: reweight + dedupe + choose with overrides."""

from __future__ import annotations

import copy
import itertools
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bpp.config import DEFAULTS
from bpp.constants import (
    OPTIMIZE_FACE_COVERAGE_W,
    OPTIMIZE_MAX_FACE_OVERLAP,
    OPTIMIZE_QUALITY_W,
)
from bpp.dedupe.cluster import deduplicate
from bpp.dedupe.semantic import semantic_deduplicate
from bpp.dedupe.strategy import resolve_strategy
from bpp.scoring.aggregate import compute_aggregate, normalize_blur_scores
from bpp.scoring.registry import get_weight_keys
from bpp.selection.choose import choose
from bpp.utils.logging import get_logger
from bpp.web.photo_dict import is_sensitive_item

_log = get_logger(__name__)

# shared cap for the non-delta recompute response shape.
# Non-delta serializes a full build_photo_dict() per active+deleted
# photo (active selected/scored, plus deleted) — at 50k photos this
# is hundreds of MB of JSON the browser can't parse. Above the cap
# we 413 with `delta_required: true` so the client switches to
# delta mode + the paginated /api/v1/photos (or /api/v1/albums/<id>/
# photos) endpoint. Keep this in sync between bp_photos.py and
# bp_albums.py — duplicated locals drift.
RECOMPUTE_FULL_PAYLOAD_LIMIT = 5000

# Config keys that accept weight-clamped float values from the UI.
RECOMPUTE_WEIGHT_KEYS = (
    *get_weight_keys(),  # blur_weight, exposure_weight, face_weight, composition_weight
    "hash_distance_threshold",
    "time_window_seconds",
    "max_per_day",
    "min_per_month",
    "max_per_month",
    "global_hash_distance_threshold",
    "face_selection_boost",
    "selection_similarity_threshold",
)


@dataclass
class RecomputeOptions:
    """Options for the recompute pipeline."""

    analysis: list[dict[str, Any]]
    config: dict[str, Any]
    k: int = DEFAULTS["default_selection_k"]
    seed: int = DEFAULTS["default_selection_seed"]
    force_include: list[str] = field(default_factory=list)
    force_exclude: list[str] = field(default_factory=list)
    selected_faces: list[int] = field(default_factory=list)
    face_cluster_map: dict[str, list[int]] | None = None
    skip_dedupe: bool = False
    clip_embeddings: dict[int, np.ndarray] | None = None
    clip_threshold: float | None = None


def recompute(opts: RecomputeOptions) -> dict[str, Any]:
    """Reweight, deduplicate, apply overrides, and select photos.

    Works on a deep copy of analysis data so the caller's data is not mutated.
    When clip_embeddings are provided, uses semantic dedup (CLIP cosine similarity)
    instead of hash-based dedup. This also bypasses the skip_dedupe flag since
    CLIP embeddings are pre-computed and loaded from DB.

    Returns dict with keys:
        photos: list of all photos with updated aggregate_scores
        selected_paths: set of filepaths that were selected
        stats: dict with counts
    """
    analysis = opts.analysis
    config = opts.config
    force_include = set(opts.force_include)
    force_exclude = set(opts.force_exclude)

    # Shallow copy each dict to avoid mutating cached analysis.
    # Hash values and other primitives are safe to share across copies.
    data = [dict(item) for item in analysis]

    # Only re-normalize/recompute for photos that have been analyzed.
    # Unanalyzed photos get aggregate_score=0 so sorting/selection still works.
    analyzed = [d for d in data if d.get("blur_raw") is not None]
    if analyzed:
        normalize_blur_scores(analyzed)
        compute_aggregate(analyzed, config)
    for d in data:
        if d.get("aggregate_score") is None:
            d["aggregate_score"] = 0.0

    # Apply face selection boost
    if opts.selected_faces and opts.face_cluster_map:
        selected_set = set(opts.selected_faces)
        face_boost = config.get("face_selection_boost", 0.15)
        for item in data:
            fp = item["filepath"]
            item_clusters = set(opts.face_cluster_map.get(fp, []))
            overlap = item_clusters & selected_set
            if overlap:
                n_overlap = min(len(overlap), OPTIMIZE_MAX_FACE_OVERLAP)
                boost = face_boost * n_overlap / OPTIMIZE_MAX_FACE_OVERLAP
                item["aggregate_score"] = min(1.0, item["aggregate_score"] + boost)

    # Quality scoring is nudity-agnostic — nudity_score / skin_score do
    # NOT penalize aggregate_score. Sensitivity is a pick-time policy
    # (sensitive_in_picks), applied below after the force-include split.

    # Remove force-excluded before dedupe
    for_dedupe = [d for d in data if d["filepath"] not in force_exclude]

    # Extract force-includes BEFORE dedup — they bypass both dedup and diversity
    forced_in = [d for d in for_dedupe if d["filepath"] in force_include]
    normal_for_dedupe = [d for d in for_dedupe if d["filepath"] not in force_include]

    # Sensitive-photo policy. In "exclude" mode, photos flagged sensitive
    # (NudeNet score over threshold or a user override — see
    # is_sensitive_item) are hard-filtered from the auto-pick candidate
    # pool. Force-included sensitive photos are NOT filtered (they were
    # split into `forced_in` above) — manual include always wins. In the
    # default "allow" mode sensitive photos compete normally.
    if config.get("sensitive_in_picks", "allow") == "exclude":
        from bpp.constants import SENSITIVE_NUDITY_THRESHOLD

        sens_threshold = config.get("sensitive_nudity_threshold", SENSITIVE_NUDITY_THRESHOLD)
        normal_for_dedupe = [
            d for d in normal_for_dedupe if not is_sensitive_item(d, sens_threshold)
        ]

    # Deduplicate only normal candidates.
    #
    # Plugin override: if the user (or YAML config) sets
    # `dedupe_strategy` to a registered strategy name, the registry
    # picks it up. resolve_strategy() falls back to None on unknown
    # / clip-required-but-missing-embeddings cases so we never crash
    # the recompute path; the auto-pick below covers those.
    have_clip = bool(opts.clip_embeddings)
    plugin_strategy = resolve_strategy(
        config.get("dedupe_strategy"), have_clip_embeddings=have_clip
    )
    if plugin_strategy is not None:
        candidates = plugin_strategy.dedupe_fn(
            normal_for_dedupe,
            config,
            clip_embeddings=opts.clip_embeddings,
        )
        dedup_mode = plugin_strategy.name
        _log.info(
            "Recompute using dedup strategy %r (%s)",
            plugin_strategy.name,
            "plugin" if not plugin_strategy.is_builtin else "built-in",
        )
    elif opts.clip_embeddings:
        threshold = opts.clip_threshold or config.get("clip_similarity_threshold", 0.92)
        candidates = semantic_deduplicate(
            normal_for_dedupe, opts.clip_embeddings, threshold=threshold, config=config
        )
        dedup_mode = "clip"
    elif opts.skip_dedupe:
        candidates = normal_for_dedupe
        dedup_mode = "skipped"
    else:
        candidates = deduplicate(normal_for_dedupe, config=config)
        dedup_mode = "hash"

    # Reduce k budget by number of force-includes
    remaining_k = max(0, opts.k - len(forced_in))

    # Select from normal candidates (pass CLIP embeddings for similarity diversity)
    selected = choose(
        candidates,
        k=remaining_k,
        config=config,
        seed=opts.seed,
        clip_embeddings=opts.clip_embeddings,
    )

    # Combine forced + selected
    selected_paths = {item["filepath"] for item in forced_in}
    selected_paths.update(item["filepath"] for item in selected)

    # Build lookup of aggregate scores from recomputed data
    score_map = {d["filepath"]: d["aggregate_score"] for d in data}

    return {
        "photos": data,
        "selected_paths": selected_paths,
        "score_map": score_map,
        "stats": {
            "total": len(analysis),
            "after_exclude": len(for_dedupe),
            "after_dedupe": len(candidates) + len(forced_in),
            "force_included": len(forced_in),
            "auto_selected": len(selected),
            "total_selected": len(selected_paths),
            "dedup_mode": dedup_mode,
        },
    }


def optimize(
    opts: RecomputeOptions,
    face_filepaths: set[str] | None = None,
) -> dict[str, Any]:
    """Sweep weight combinations to find optimal settings.

    When faces are provided, scoring blends face coverage (60%) with quality (40%).
    Without faces, scoring is pure quality (aggregate score of selected photos).

    Returns the best config dict and its score breakdown.
    """
    analysis = opts.analysis
    config = opts.config
    has_faces = bool(opts.selected_faces and opts.face_cluster_map and face_filepaths)

    # Pre-compute hash map and normalized/deduped base once
    hash_map = {item["filepath"]: (item.get("phash"), item.get("ahash")) for item in analysis}
    base = copy.deepcopy(analysis)
    for item in base:
        ph, ah = hash_map.get(item["filepath"], (None, None))
        if ph is not None:
            item["phash"] = ph
        if ah is not None:
            item["ahash"] = ah
    normalize_blur_scores(base)

    # Pre-deduplicate once (dedup params stay fixed)
    deduped = base if opts.skip_dedupe else deduplicate(copy.deepcopy(base), config=config)

    # Weight grid — coarse sweep
    blur_weights = [0.15, 0.25, 0.35]
    exposure_weights = [0.10, 0.20, 0.30]
    composition_weights = [0.05, 0.15, 0.25]
    face_weights = [0.20, 0.35, 0.50, 0.65] if has_faces else [0.15, 0.30]
    face_boosts = [0.05, 0.15, 0.25, 0.40] if has_faces else [0.0]

    best_score = -1.0
    best_cfg = None
    best_breakdown = None
    selected_set = set(opts.selected_faces) if opts.selected_faces else set()

    # Pre-build face cluster sets per filepath to avoid rebuilding each iteration
    face_cluster_sets: dict[str, set[int]] = {}
    if has_faces:
        for item in deduped:
            fp = item["filepath"]
            clusters = opts.face_cluster_map.get(fp)
            if clusters:
                face_cluster_sets[fp] = set(clusters)

    for fw, fb, bw, ew, cw in itertools.product(
        face_weights, face_boosts, blur_weights, exposure_weights, composition_weights
    ):
        trial_cfg = dict(config)
        trial_cfg["face_weight"] = fw
        trial_cfg["face_selection_boost"] = fb
        trial_cfg["blur_weight"] = bw
        trial_cfg["exposure_weight"] = ew
        trial_cfg["composition_weight"] = cw

        # Shallow copy: only aggregate_score changes per iteration
        candidates = [dict(item) for item in deduped]
        compute_aggregate(candidates, trial_cfg)

        # Apply face boost if faces selected
        if has_faces:
            for item in candidates:
                overlap = face_cluster_sets.get(item["filepath"], set()) & selected_set
                if overlap:
                    n_overlap = min(len(overlap), OPTIMIZE_MAX_FACE_OVERLAP)
                    boost = fb * n_overlap / OPTIMIZE_MAX_FACE_OVERLAP
                    item["aggregate_score"] = min(1.0, item["aggregate_score"] + boost)

        # Select
        selected = choose(
            candidates,
            k=opts.k,
            config=trial_cfg,
            seed=opts.seed,
            clip_embeddings=opts.clip_embeddings,
        )
        avg_quality = sum(s["aggregate_score"] for s in selected) / max(len(selected), 1)

        if has_faces:
            sel_paths = {s["filepath"] for s in selected}
            face_hits = sum(1 for fp in sel_paths if fp in face_filepaths)
            coverage = face_hits / max(len(sel_paths), 1)
            composite = coverage * OPTIMIZE_FACE_COVERAGE_W + avg_quality * OPTIMIZE_QUALITY_W
        else:
            face_hits = 0
            coverage = 0.0
            composite = avg_quality

        if composite > best_score:
            best_score = composite
            best_cfg = {
                "face_weight": fw,
                "face_selection_boost": fb,
                "blur_weight": bw,
                "exposure_weight": ew,
                "composition_weight": cw,
            }
            best_breakdown = {
                "avg_quality": round(avg_quality, 3),
                "composite_score": round(composite, 3),
                "total_selected": len(selected),
            }
            if has_faces:
                best_breakdown["face_coverage"] = round(coverage, 3)
                best_breakdown["face_photos_selected"] = face_hits

    return {"settings": best_cfg, "breakdown": best_breakdown}
