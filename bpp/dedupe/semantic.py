"""Semantic deduplication using CLIP embeddings with cosine similarity."""

from __future__ import annotations

import datetime
import os
from typing import Any

import numpy as np

from bpp.config import cfg_get
from bpp.scoring.clip_embed import cosine_similarity
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def cluster_time_windowed(
    items: list[dict[str, Any]],
    fp_to_emb: dict[str, np.ndarray],
    threshold: float,
    time_window: float,
) -> list[list[dict[str, Any]]]:
    """Pass-1 grouping: visually-similar shots near in time.

    Groups *items* (pre-sorted by date) so that each cluster's members are
    within CLIP cosine ``threshold`` of the cluster rep AND taken within
    ``time_window`` seconds of it. Returns the clusters as lists of the
    original item dicts (membership preserved — nothing dropped).

    Shared by :func:`semantic_deduplicate` (which then keeps one rep per
    cluster for pick diversity) and :func:`bpp.db.moments.assign_moment_clusters`
    (which writes the full membership to ``moment_cluster_id``). The Moments
    feature wants exactly this time-windowed grouping — NOT the global,
    time-agnostic pass-2 dedup, which would merge look-alike shots from
    different days into one group.
    """
    # Pre-parse each item's date ONCE to a float-seconds timestamp.
    # `within_time_window` re-parses both dates on every comparison — at
    # ~100 comparisons per item across thousands of items that's hundreds of
    # thousands of datetime.fromisoformat() calls. NaN for unparseable dates
    # so the proximity check naturally fails (NaN comparisons are False).
    item_ts = np.empty(len(items), dtype=np.float64)
    for i, it in enumerate(items):
        d = it.get("date") or ""
        try:
            item_ts[i] = datetime.datetime.fromisoformat(d).timestamp()
        except (ValueError, TypeError):
            item_ts[i] = float("nan")

    # Each cluster's rep is cluster[0] (first item assigned), so we track a
    # parallel array of rep embeddings + rep timestamps indexed by cluster idx.
    clusters: list[list[dict[str, Any]]] = []
    first_emb = next(iter(fp_to_emb.values()), None)
    if first_emb is not None:
        emb_dim = first_emb.shape[0]
        # Grow on demand rather than pre-allocating the worst case (one cluster
        # per item = len(items) x emb_dim). At 200k photos that worst case
        # reserves ~410 MB up front even though most photos cluster together
        # and far fewer reps ever exist. Start modest and double when full
        # (amortized O(N) copies, capped at len(items)).
        _rep_cap = min(len(items), 1024)
        rep_emb_buf = np.empty((_rep_cap, emb_dim), dtype=first_emb.dtype)
    else:
        rep_emb_buf = None
    rep_ts = np.empty(len(items), dtype=np.float64)
    # rep_has_emb[ci] = True if clusters[ci]'s rep has an embedding in
    # rep_emb_buf — separates the indexable similarity universe from
    # reps that pass through without one.
    rep_has_emb = np.zeros(len(items), dtype=bool)
    n_clusters = 0

    LOOKBACK = 100

    for i, item in enumerate(items):
        emb = fp_to_emb.get(item["filepath"])
        if emb is None:
            # No embedding — treat as unique, but still register so the
            # cluster list and rep_ts stay aligned by index.
            clusters.append([item])
            rep_ts[n_clusters] = item_ts[i]
            rep_has_emb[n_clusters] = False
            n_clusters += 1
            continue

        assigned = False
        if n_clusters > 0 and rep_emb_buf is not None:
            # Restrict the candidate window: last LOOKBACK clusters AND
            # those whose rep has an embedding AND within time window.
            lo = max(0, n_clusters - LOOKBACK)
            # Mask out reps without embeddings + outside time window.
            ts_slice = rep_ts[lo:n_clusters]
            has_emb_slice = rep_has_emb[lo:n_clusters]
            # NaN handling: subtraction with NaN → NaN → fails comparison.
            in_window = np.abs(ts_slice - item_ts[i]) <= time_window
            candidates = has_emb_slice & in_window
            if candidates.any():
                # Compute similarities for the entire window in one BLAS
                # call, then mask non-candidates to -inf so argmax picks
                # only from valid ones.
                sims = rep_emb_buf[lo:n_clusters] @ emb
                sims_masked = np.where(candidates, sims, -np.inf)
                # Bind to the most recent qualifying cluster (highest index
                # among those over threshold) — matches the old inner-break
                # "first match scanning recent-first" behavior.
                qualifies = sims_masked >= threshold
                if qualifies.any():
                    best_local_idx = int(np.flatnonzero(qualifies).max())
                    ci = lo + best_local_idx
                    clusters[ci].append(item)
                    assigned = True

        if not assigned:
            clusters.append([item])
            rep_ts[n_clusters] = item_ts[i]
            if rep_emb_buf is not None:
                if n_clusters >= rep_emb_buf.shape[0]:
                    # Double the rep buffer (capped at len(items)).
                    new_cap = min(len(items), max(rep_emb_buf.shape[0] * 2, n_clusters + 1))
                    grown = np.empty((new_cap, rep_emb_buf.shape[1]), dtype=rep_emb_buf.dtype)
                    grown[: rep_emb_buf.shape[0]] = rep_emb_buf
                    rep_emb_buf = grown
                rep_emb_buf[n_clusters] = emb
                rep_has_emb[n_clusters] = True
            n_clusters += 1

    return clusters


def semantic_deduplicate(
    analysis: list[dict[str, Any]],
    clip_embeddings: dict[int, np.ndarray],
    threshold: float = 0.92,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Cluster semantically similar photos using CLIP cosine similarity.

    Two-pass dedup mirroring dedupe/cluster.py:
      Pass 1: time-window + cosine similarity >= threshold
      Pass 2: global cosine similarity >= (threshold + 0.03)

    Each representative is annotated with:
      - cluster_size: total photos in its cluster
      - similar_photos: list of {filepath, similarity} for deduped siblings

    Photos without embeddings are treated as unique (pass through).
    """
    if config is None:
        config = {}

    # Server-log breadcrumb: at 200K-scale this function runs for
    # 6+ minutes (O(N²) global pass). The existing pass-1 + final
    # log lines below land too late for a maintainer wondering
    # whether it started at all. Project convention: nothing should be silent
    # — anchor every long phase in server.log.
    import time as _time

    _t0 = _time.perf_counter()
    log.info(
        "Semantic dedup starting: %d candidate images, %d CLIP embeddings, threshold=%.3f",
        len(analysis),
        len(clip_embeddings),
        threshold,
    )

    time_window = cfg_get(config, "time_window_seconds")

    # Build filepath -> photo_id lookup for embedding access
    # analysis items may have "id" (from DB) or not
    fp_to_emb: dict[str, np.ndarray] = {}
    for item in analysis:
        photo_id = item.get("id")
        if photo_id is not None and photo_id in clip_embeddings:
            fp_to_emb[item["filepath"]] = clip_embeddings[photo_id]

    # Sort by date for temporal locality, then pass-1 cluster: visually
    # similar (CLIP cosine >= threshold) AND within the time window. The
    # clustering itself lives in cluster_time_windowed() so the Moments
    # feature can reuse the exact same grouping.
    items = sorted(analysis, key=lambda x: x.get("date") or "")
    clusters = cluster_time_windowed(items, fp_to_emb, threshold, time_window)

    # Pick best from each cluster, annotate with similar_photos
    selected: list[dict[str, Any]] = []
    for cluster in clusters:
        best = max(cluster, key=lambda x: x.get("aggregate_score", 0))
        best["cluster_size"] = len(cluster)
        if len(cluster) > 1:
            best_emb = fp_to_emb.get(best["filepath"])
            siblings = []
            for item in cluster:
                if item["filepath"] == best["filepath"]:
                    continue
                sim = 0.0
                item_emb = fp_to_emb.get(item["filepath"])
                if best_emb is not None and item_emb is not None:
                    sim = cosine_similarity(best_emb, item_emb)
                siblings.append(
                    {
                        "filepath": item["filepath"],
                        "similarity": round(sim, 3),
                        "aggregate_score": item.get("aggregate_score", 0),
                        "blur_score": item.get("blur_score", 0),
                        "exposure_score": item.get("exposure_score", 0),
                        "face_score": item.get("face_score", 0),
                        "composition_score": item.get("composition_score", 0),
                        "date_day": item.get("date_day", ""),
                        "filename": os.path.basename(item["filepath"]),
                    }
                )
            best["similar_photos"] = siblings
        selected.append(best)

    log.info(
        "Semantic dedup pass 1 (time+clip): %d images -> %d clusters",
        len(analysis),
        len(clusters),
    )

    # Pass-1 scratch (rep embedding buffer + timestamp arrays) is now local
    # to cluster_time_windowed() and freed when it returns — so the pass-2
    # buffers below don't pile on top of it at peak recompute.

    # Pass 2: global semantic dedup (no time constraint, tighter threshold)
    global_threshold = threshold + 0.03
    log.info(
        "Semantic dedup pass 2 (global) starting: %d pass-1 reps, threshold=%.3f",
        len(selected),
        global_threshold,
    )
    selected = _global_semantic_dedup(selected, fp_to_emb, global_threshold)

    log.info(
        "Semantic dedup final: %d representatives in %.1fs",
        len(selected),
        _time.perf_counter() - _t0,
    )
    return selected


#: Candidates scored per matrix product in pass 2, and the most bytes one
#: block's (candidates x reps) similarity matrix may take: as the rep
#: count grows toward the CLIP cap the block shrinks, so the product is
#: never a 200 MB allocation on top of the matrix itself.
_PASS2_BLOCK = 256
_PASS2_BLOCK_BYTES = 32 * 1024 * 1024


def _pass2_block_rows(n_reps: int) -> int:
    """Candidates per block so the block's similarity matrix stays
    under ``_PASS2_BLOCK_BYTES`` (float32)."""
    if n_reps <= 0:
        return _PASS2_BLOCK
    return max(1, min(_PASS2_BLOCK, _PASS2_BLOCK_BYTES // (4 * n_reps)))


def _global_semantic_dedup(
    items: list[dict[str, Any]],
    fp_to_emb: dict[str, np.ndarray],
    threshold: float,
) -> list[dict[str, Any]]:
    """Second-pass dedup: cluster by CLIP similarity alone (ignoring time).

    Vectorized: maintains a pre-allocated rep-embedding matrix and uses
    a single batched dot product per candidate to find its best match,
    instead of a Python loop comparing one-by-one. On a 3500-photo
    library this drops the pass from ~10s to <500ms — most of the
    recompute cost on every k change came from here.
    """
    # Sort by score descending so best photos become representatives.
    sorted_items = sorted(items, key=lambda x: -x.get("aggregate_score", 0))
    representatives: list[dict[str, Any]] = []

    # Pre-allocate the rep embedding matrix. We size to the worst case
    # (every item becomes a rep) — for N=3500 D=512 float32 that's 7 MB,
    # cheap. Tracking n_emb separately lets us slice rep_emb_buf[:n_emb]
    # for the dot product without copying.
    first_emb = next(iter(fp_to_emb.values()), None)
    if first_emb is None or not sorted_items:
        # No embeddings at all — every item passes through.
        for item in sorted_items:
            representatives.append(item)
        return representatives

    emb_dim = first_emb.shape[0]
    rep_emb_buf = np.empty((len(sorted_items), emb_dim), dtype=first_emb.dtype)
    # rep_index_for_emb_row[i] = index into `representatives` of the rep
    # whose embedding lives at rep_emb_buf[i]. Decouples buffer row from
    # rep order so reps without an embedding can still be in the list.
    rep_index_for_emb_row: list[int] = []
    n_emb = 0

    def _absorb(rep_item: dict[str, Any], item: dict[str, Any], sim: float) -> None:
        rep_item.setdefault("similar_photos", []).append(
            {
                "filepath": item["filepath"],
                "similarity": round(sim, 3),
                "aggregate_score": item.get("aggregate_score", 0),
                "blur_score": item.get("blur_score", 0),
                "exposure_score": item.get("exposure_score", 0),
                "face_score": item.get("face_score", 0),
                "composition_score": item.get("composition_score", 0),
                "date_day": item.get("date_day", ""),
                "filename": os.path.basename(item["filepath"]),
            }
        )
        rep_item["cluster_size"] = rep_item.get("cluster_size", 1) + 1

    # Candidates are scored a block at a time: one matrix product per
    # block against the reps that existed before the block, plus small
    # dot products against the reps created inside the block. Same result
    # as the one-candidate-at-a-time pass; a fraction of the memory
    # traffic, because the rep matrix is streamed once per block, not
    # once per candidate.
    start = 0
    while start < len(sorted_items):
        block = sorted_items[start : start + _pass2_block_rows(n_emb)]
        start += len(block)
        with_emb = [
            (i, fp_to_emb[it["filepath"]])
            for i, it in enumerate(block)
            if it["filepath"] in fp_to_emb
        ]
        block_sims = None
        if with_emb and n_emb > 0:
            block_mat = np.stack([e for _i, e in with_emb])
            block_sims = block_mat @ rep_emb_buf[:n_emb].T  # (len(with_emb), n_emb)
        col = {i: k for k, (i, _e) in enumerate(with_emb)}
        n_before = n_emb

        for i, item in enumerate(block):
            emb = fp_to_emb.get(item["filepath"])
            if emb is None:
                # No embedding — emit as a unique rep, don't add to the
                # similarity index (future items can't match against it).
                representatives.append(item)
                continue

            matched_rep_idx: int | None = None
            matched_sim: float = 0.0
            best_sim = -1.0
            best_row = -1
            if block_sims is not None:
                sims = block_sims[col[i]]
                best_row = int(np.argmax(sims))
                best_sim = float(sims[best_row])
            if n_emb > n_before:
                new_sims = rep_emb_buf[n_before:n_emb] @ emb
                r = int(np.argmax(new_sims))
                if float(new_sims[r]) > best_sim:
                    best_sim = float(new_sims[r])
                    best_row = n_before + r
            if best_row >= 0 and best_sim >= threshold:
                matched_rep_idx = rep_index_for_emb_row[best_row]
                matched_sim = best_sim

            if matched_rep_idx is not None:
                _absorb(representatives[matched_rep_idx], item, matched_sim)
                continue

            # New rep — record it and index its embedding for future matching.
            representatives.append(item)
            rep_emb_buf[n_emb] = emb
            rep_index_for_emb_row.append(len(representatives) - 1)
            n_emb += 1

    if len(representatives) < len(items):
        log.info(
            "Semantic dedup pass 2 (global clip): %d -> %d",
            len(items),
            len(representatives),
        )

    return representatives
