"""Deterministic greedy selection with diversity constraints."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from bpp.config import cfg_get
from bpp.selection.diversity import DiversityTracker
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def choose(
    candidates: list[dict[str, Any]],
    k: int = 50,
    config: dict[str, Any] | None = None,
    clip_embeddings: dict[int, np.ndarray] | None = None,
) -> list[dict[str, Any]]:
    """Select k photos using deterministic greedy algorithm with diversity constraints.

    Strategy:
    1. Sort by score desc, tiebreak by date then filename (stable).
    2. First pass: ensure month coverage (pick best uncovered month photo).
    3. Second pass: fill remaining slots greedily respecting per-day cap
       and visual similarity constraints.
    """
    if config is None:
        config = {}

    # A partial config falls back to the app's own defaults (a private
    # fallback here once left the similarity check off while the default
    # had it on).
    max_per_day = cfg_get(config, "max_per_day")
    min_per_month = cfg_get(config, "min_per_month")
    max_per_month = cfg_get(config, "max_per_month")
    similarity_threshold = cfg_get(config, "selection_similarity_threshold")

    tracker = DiversityTracker(
        max_per_day=max_per_day,
        min_per_month=min_per_month,
        max_per_month=max_per_month,
        similarity_threshold=similarity_threshold,
        clip_embeddings=clip_embeddings,
    )

    # Determine available months
    all_months = {c.get("date_month", "unknown") for c in candidates}
    tracker.set_available_months(all_months)

    # Sort: score desc, date asc, filename asc (deterministic)
    sorted_candidates = sorted(
        candidates,
        key=lambda x: (
            -x.get("aggregate_score", 0),
            x.get("date", ""),
            x.get("filepath", ""),
        ),
    )

    selected: list[dict[str, Any]] = []
    used_paths: set[str] = set()

    # Phase 1: Ensure month coverage (pick min_per_month from each month).
    # Candidates are bucketed by month once (still in score order) so
    # this pass is one walk over the list, not one per month.
    by_month: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for cand in sorted_candidates:
        by_month[cand.get("date_month")].append(cand)
    for month in sorted(all_months):
        if len(selected) >= k:
            break
        picked_for_month = 0
        for cand in by_month.get(month, ()):
            if picked_for_month >= min_per_month:
                break
            if len(selected) >= k:
                break
            if cand["filepath"] in used_paths:
                continue
            ok, _reason = tracker.can_accept(cand)
            if ok:
                selection_reason = tracker.accept(cand)
                cand_copy = dict(cand)
                cand_copy["selection_reason"] = selection_reason
                selected.append(cand_copy)
                used_paths.add(cand["filepath"])
                picked_for_month += 1

    # Phase 2: Fill remaining slots greedily by score
    for cand in sorted_candidates:
        if len(selected) >= k:
            break
        if cand["filepath"] in used_paths:
            continue
        ok, _reason = tracker.can_accept(cand)
        if not ok:
            continue
        selection_reason = tracker.accept(cand)
        cand_copy = dict(cand)
        cand_copy["selection_reason"] = selection_reason
        selected.append(cand_copy)
        used_paths.add(cand["filepath"])

    # If still under k and we have remaining candidates, relax day constraint
    # but still respect visual similarity
    if len(selected) < k:
        for cand in sorted_candidates:
            if len(selected) >= k:
                break
            if cand["filepath"] in used_paths:
                continue
            if tracker.is_too_similar(cand):
                continue
            tracker.accept(cand)
            cand_copy = dict(cand)
            cand_copy["selection_reason"] = "relaxed day constraint; score={:.3f}".format(
                cand.get("aggregate_score", 0)
            )
            selected.append(cand_copy)
            used_paths.add(cand["filepath"])

    log.info(
        "Selection: %d/%d chosen from %d candidates (%d months covered)",
        len(selected),
        k,
        len(candidates),
        len(tracker.month_counts),
    )

    return selected
