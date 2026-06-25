"""Cluster near-duplicate images for deduplication."""

from __future__ import annotations

from typing import Any

from bpp.constants import CLUSTER_UNASSIGNED
from bpp.dedupe.common import within_time_window
from bpp.dedupe.phash import (
    compute_hashes_from_file,
    dual_hash_distance,
    hamming_distance,
)
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _hash_distance(item1: dict[str, Any], item2: dict[str, Any]) -> int:
    """Compute perceptual distance using dual-hash (min of dHash, aHash)."""
    ah1, ah2 = item1.get("ahash"), item2.get("ahash")
    dh1, dh2 = item1["phash"], item2["phash"]
    # Use dual-hash if both items have ahash, otherwise fall back to dHash only
    if ah1 is not None and ah2 is not None:
        return dual_hash_distance(dh1, ah1, dh2, ah2)
    return hamming_distance(dh1, dh2)


def deduplicate(
    analysis: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Cluster near-duplicate images and pick the best from each cluster.

    Uses EXIF time window + perceptual hash distance to form clusters.
    Computes both dHash and aHash for more robust burst detection.
    Returns one representative per cluster (highest aggregate_score).
    """
    if config is None:
        config = {}

    hash_threshold = config.get("hash_distance_threshold", 10)
    time_window = config.get("time_window_seconds", 30)

    # Compute hashes for all images (both dHash and aHash)
    for item in analysis:
        if "phash" not in item:
            dhash, ahash = compute_hashes_from_file(item["filepath"])
            item["phash"] = dhash
            item["ahash"] = ahash
        elif "ahash" not in item and item["phash"] is not None:
            # Have dHash but missing aHash — compute it
            from bpp.dedupe.phash import _load_image, compute_ahash

            try:
                img = _load_image(item["filepath"])
                item["ahash"] = compute_ahash(img)
            except Exception:
                item["ahash"] = None

    # Sort by date for efficient clustering
    items = sorted(analysis, key=lambda x: x.get("date") or "")

    # Greedy clustering: assign each image to existing cluster or create new
    clusters: list[list[dict[str, Any]]] = []
    cluster_ids: list[int] = [CLUSTER_UNASSIGNED] * len(items)

    for i, item in enumerate(items):
        if item["phash"] is None:
            # Can't hash, treat as unique
            clusters.append([item])
            cluster_ids[i] = len(clusters) - 1
            continue

        assigned = False
        # Check recent clusters (within time window)
        for ci in range(len(clusters) - 1, max(-1, len(clusters) - 50), -1):
            if not clusters[ci]:
                continue
            rep = clusters[ci][0]
            if rep["phash"] is None:
                continue
            # Check time proximity
            if not within_time_window(item["date"], rep["date"], time_window):
                continue
            # Check hash similarity (uses min of dHash, aHash distances)
            dist = _hash_distance(item, rep)
            if dist <= hash_threshold:
                clusters[ci].append(item)
                cluster_ids[i] = ci
                assigned = True
                break

        if not assigned:
            clusters.append([item])
            cluster_ids[i] = len(clusters) - 1

    # Pick best from each cluster
    selected: list[dict[str, Any]] = []
    for cluster in clusters:
        best = max(cluster, key=lambda x: x.get("aggregate_score", 0))
        best["cluster_size"] = len(cluster)
        selected.append(best)

    log.info(
        "Dedup pass 1 (time+hash): %d images -> %d clusters",
        len(analysis),
        len(clusters),
    )

    # Pass 2: Global phash dedup (no time constraint, tighter threshold)
    # Catches near-identical photos taken at different times
    global_threshold = config.get("global_hash_distance_threshold", 8)
    if global_threshold > 0:
        selected = _global_dedup(selected, global_threshold)

    log.info("Dedup final: %d representatives", len(selected))

    return selected


def _global_dedup(items: list[dict[str, Any]], threshold: int) -> list[dict[str, Any]]:
    """Second-pass dedup: cluster by hash alone (ignoring time).

    Protects the best-scoring photo from each calendar month so that
    global dedup cannot wipe entire months of similar-looking photos.
    """
    # Sort by score descending so best photos become cluster representatives
    sorted_items = sorted(items, key=lambda x: -x.get("aggregate_score", 0))

    # Build set of month-representative filepaths that must survive.
    # Since sorted_items is score-descending, first seen per month is best.
    month_reps: set[str] = set()
    seen_months: set[str] = set()
    for item in sorted_items:
        month = item.get("date_month") or "unknown"
        if month not in seen_months:
            seen_months.add(month)
            month_reps.add(item["filepath"])

    representatives: list[dict[str, Any]] = []
    rep_items: list[dict[str, Any]] = []

    for item in sorted_items:
        if item.get("phash") is None:
            representatives.append(item)
            continue

        # Month representatives are always kept
        if item["filepath"] in month_reps:
            representatives.append(item)
            rep_items.append(item)
            continue

        matched = False
        for rep in rep_items:
            dist = _hash_distance(item, rep)
            if dist <= threshold:
                matched = True
                break

        if not matched:
            representatives.append(item)
            rep_items.append(item)

    if len(representatives) < len(items):
        log.info(
            "Dedup pass 2 (global hash): %d -> %d",
            len(items),
            len(representatives),
        )

    return representatives
