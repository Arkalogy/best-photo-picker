"""Cluster near-duplicate images for deduplication."""

from __future__ import annotations

from typing import Any

import numpy as np

from bpp.config import cfg_get
from bpp.constants import CLUSTER_UNASSIGNED
from bpp.dedupe.common import within_time_window
from bpp.dedupe.phash import (
    compute_hashes_from_file,
    dual_hash_distance,
    hamming_distance,
)
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_U64 = (1 << 64) - 1


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

    # A partial config (a plugin, the strategy hook, a test) falls back to
    # the app's own defaults — never a stricter private copy.
    hash_threshold = cfg_get(config, "hash_distance_threshold")
    time_window = cfg_get(config, "time_window_seconds")

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
    global_threshold = cfg_get(config, "global_hash_distance_threshold")
    if global_threshold > 0:
        selected = _global_dedup(selected, global_threshold)

    log.info("Dedup final: %d representatives", len(selected))

    return selected


#: Past this many bands (each under 3 bits) the band index stops helping.
_MAX_BANDS = 21


def _band_edges(threshold: int) -> list[tuple[int, int]]:
    """``[(lo, hi), …]`` bit ranges of the threshold+1 bands of a 64-bit
    hash, or ``[]`` when the threshold is too wide for banding."""
    n = int(threshold) + 1
    if n < 2 or n > _MAX_BANDS:
        return []
    edges = [round(64 * i / n) for i in range(n + 1)]
    return [(edges[i], edges[i + 1]) for i in range(n)]


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
    # The representatives' hashes as growing uint64 buffers: one item is
    # compared with all of them in a single vectorised popcount instead
    # of a Python loop (200k x 200k / 2 comparisons at the cap).
    from bpp.selection.diversity import _hamming_many

    rep_ph = np.empty(len(sorted_items), dtype=np.uint64)
    rep_ah = np.empty(len(sorted_items), dtype=np.uint64)
    rep_has_ah = np.zeros(len(sorted_items), dtype=bool)
    n_reps = 0
    # Band index: two hashes within `threshold` bits agree on at least one
    # of threshold+1 bands (pigeonhole), so only representatives sharing
    # a band value with the item need an exact distance. Exact, and far
    # fewer than all of them at the cap. (A wide threshold makes the
    # bands too narrow to help: then every representative is compared.)
    bands = _band_edges(threshold)
    index: dict[tuple[int, int, bool], list[int]] = {}

    def _band_keys(value: int, is_ahash: bool) -> list[tuple[int, int, bool]]:
        return [
            (b, (value >> lo) & ((1 << (hi - lo)) - 1), is_ahash)
            for b, (lo, hi) in enumerate(bands)
        ]

    def _remember(it: dict[str, Any]) -> None:
        nonlocal n_reps
        ph = int(it["phash"]) & _U64
        rep_ph[n_reps] = np.uint64(ph)
        ah = it.get("ahash")
        rep_ah[n_reps] = np.uint64(int(ah) & _U64) if ah is not None else np.uint64(0)
        rep_has_ah[n_reps] = ah is not None
        if bands:
            for key in _band_keys(ph, False):
                index.setdefault(key, []).append(n_reps)
            if ah is not None:
                for key in _band_keys(int(ah) & _U64, True):
                    index.setdefault(key, []).append(n_reps)
        n_reps += 1

    def _candidates(ph: int, ah: int | None) -> np.ndarray | None:
        """Indices of the representatives worth an exact distance, or None
        for all of them."""
        if not bands:
            return None
        found: set[int] = set()
        for key in _band_keys(ph, False):
            found.update(index.get(key, ()))
        if ah is not None:
            for key in _band_keys(ah, True):
                found.update(index.get(key, ()))
        return np.fromiter(found, dtype=np.int64, count=len(found))

    for item in sorted_items:
        if item.get("phash") is None:
            representatives.append(item)
            continue

        # Month representatives are always kept
        if item["filepath"] in month_reps:
            representatives.append(item)
            _remember(item)
            continue

        matched = False
        if n_reps:
            ph = int(item["phash"]) & _U64
            ah = item.get("ahash")
            ah = int(ah) & _U64 if ah is not None else None
            cand = _candidates(ph, ah)
            if cand is None:
                reps_ph, reps_ah, reps_has = rep_ph[:n_reps], rep_ah[:n_reps], rep_has_ah[:n_reps]
            else:
                reps_ph, reps_ah, reps_has = rep_ph[cand], rep_ah[cand], rep_has_ah[cand]
            if len(reps_ph):
                dist = _hamming_many(ph, reps_ph)
                if ah is not None:
                    # Dual hash where both sides have one: min of the two.
                    a_dist = _hamming_many(ah, reps_ah)
                    dist = np.where(reps_has, np.minimum(dist, a_dist), dist)
                matched = bool((dist <= threshold).any())

        if not matched:
            representatives.append(item)
            _remember(item)

    if len(representatives) < len(items):
        log.info(
            "Dedup pass 2 (global hash): %d -> %d",
            len(items),
            len(representatives),
        )

    return representatives
