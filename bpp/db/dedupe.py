"""Near-duplicate cluster assignment for the web app.

Background
----------
The CLI ``bpp select`` pipeline uses ``deduplicate()`` (bpp/dedupe/cluster.py)
to pick ONE best photo per cluster and discard the rest.  That works for
batch export but is wrong for the photo picker: the user needs to SEE all
near-duplicates so they can make the keep/delete decision themselves.

This module provides ``assign_near_duplicate_clusters()``, which:

  1. Loads all active, non-sidecar photos with their phash/ahash from the DB.
  2. Groups them into near-duplicate clusters using hamming distance.
  3. Writes ``dup_cluster_id`` and ``cluster_size`` back to the DB for every
     photo in a multi-photo cluster.

The results drive two things in the web app:

  * **Duplicates smart album** — queries ``WHERE cluster_size > 1``
  * **Review flow** — ``/api/v1/duplicates/groups`` groups by ``dup_cluster_id``

Design decisions
----------------
Hamming threshold (default 8 bits)
    Two 64-bit perceptual hashes with ≤8 differing bits are treated as
    near-duplicates.  8 handles burst shots of the same scene with minor
    subject motion.  Tighter than the CLI default (10) to avoid false
    positives in diverse libraries.

Time window (default 60 seconds)
    Photos must have been taken within ``time_window_seconds`` of the
    cluster representative to join it.  This prevents the Union-Find
    transitivity from chaining hundreds of visually-similar-but-unrelated
    photos (e.g. every sunset in a year-long library) into one giant cluster.
    Burst shots are always within seconds of each other; 60 s is generous
    while still blocking false positives across different sessions.

Hash metric
    ``min(hamming(phash1, phash2), hamming(ahash1, ahash2))`` — same as
    cluster.py.  Taking the minimum means either hash agreeing on similarity
    is enough to form a cluster.

Clustering algorithm
    Union-Find (disjoint-set union) over all pairs with distance ≤ threshold.
    O(n²) in the number of candidate photos; acceptable for n ≤ 100k (BPP
    libraries are typically 1k-50k).  Sorted by phash before the quadratic
    pass so the inner loop breaks early when phash values diverge beyond
    any possible threshold.

Sidecar exclusion
    Live Photo sidecars (is_live_photo_sidecar=1) are never clustered.  They
    would always match their parent and inflate the duplicate count.

Idempotency
    Safe to call on every import.  Existing dup_cluster_id values are
    overwritten with the freshly computed assignment.
"""

from __future__ import annotations

import sqlite3

from bpp.utils.logging import get_logger

log = get_logger(__name__)

# Default hamming distance threshold (bits out of 64).
# 8 bits catches burst shots; raise to 12+ for more aggressive grouping.
DEFAULT_HAMMING_THRESHOLD = 8


def _hamming(a: int, b: int) -> int:
    """Count differing bits between two 64-bit integers."""
    return bin(a ^ b).count("1")


def _date_seconds_diff(a: str | None, b: str | None) -> float:
    """Return elapsed seconds between two ISO-8601 date strings.

    Returns 0 if either is None/empty (treat as same time — still cluster).
    Returns a large value if parsing fails (conservative: don't cluster).
    """
    if not a or not b:
        return 0.0
    try:
        from datetime import datetime

        fmt = "%Y-%m-%dT%H:%M:%S" if "T" in a else "%Y-%m-%d %H:%M:%S"
        ta = datetime.strptime(a[:19], fmt)
        tb = datetime.strptime(b[:19], fmt)
        return abs((tb - ta).total_seconds())
    except Exception:
        return float("inf")


def _min_hash_distance(
    ph1: int | None,
    ah1: int | None,
    ph2: int | None,
    ah2: int | None,
) -> int:
    """Return the minimum hamming distance across the two hash types.

    A photo pair is near-duplicate if either dHash OR aHash agrees on
    similarity — matching on one robust hash is sufficient evidence.
    Returns 65 (impossible value) when both comparisons are unavailable.
    """
    distances = []
    if ph1 is not None and ph2 is not None:
        distances.append(_hamming(ph1, ph2))
    if ah1 is not None and ah2 is not None:
        distances.append(_hamming(ah1, ah2))
    return min(distances) if distances else 65


DEFAULT_TIME_WINDOW_SECONDS = 60


def assign_near_duplicate_clusters(
    conn: sqlite3.Connection,
    hamming_threshold: int = DEFAULT_HAMMING_THRESHOLD,
    time_window_seconds: int = DEFAULT_TIME_WINDOW_SECONDS,
) -> int:
    """Compute near-duplicate clusters and write them to the DB.

    Loads all active, non-sidecar photos with phash/ahash and date, clusters
    them using Union-Find with hamming distance ≤ ``hamming_threshold`` AND
    taken within ``time_window_seconds`` of the cluster representative, then
    updates ``dup_cluster_id`` and ``cluster_size`` for every photo.

    The time window prevents transitivity chaining: without it, a sequence of
    landscape photos each 8 bits from the next could form one cluster of 100+
    photos.  Burst shots are always <10 s apart; 60 s catches any plausible
    burst sequence while excluding unrelated sessions.

    Args:
        conn: DB connection (write access required).
        hamming_threshold: Photos with min(dHash, aHash) distance ≤ this
            value are candidates.  Default: 8 bits.
        time_window_seconds: Maximum elapsed time between a photo and the
            cluster representative's date.  Default: 60 s.

    Returns:
        Count of photos that ended up in a non-singleton cluster.
    """
    rows = conn.execute(
        "SELECT id, phash, ahash, date FROM photos "
        "WHERE missing=0 AND deleted_at IS NULL AND hidden_at IS NULL "
        "  AND is_live_photo_sidecar=0 "
        "  AND (phash IS NOT NULL OR ahash IS NOT NULL)"
    ).fetchall()

    if not rows:
        return 0

    # Union-Find implementation
    # parent[i] = index of root for photo at index i
    n = len(rows)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    # Sort by phash to allow early-exit: once phash values differ by
    # more than 2^threshold, no later pair can be within threshold.
    # (Note: this is an approximation — phash bit difference doesn't map
    # linearly to hamming distance, but it dramatically reduces inner loop
    # iterations in practice.)
    # Sort by date so the inner loop can break early once timestamps diverge
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda t: t[1][3] or "")  # sort by date ISO string

    for i in range(len(indexed)):
        idx_i, (_id_i, ph_i, ah_i, date_i) = indexed[i]
        for j in range(i + 1, len(indexed)):
            idx_j, (_id_j, ph_j, ah_j, date_j) = indexed[j]
            secs = _date_seconds_diff(date_i, date_j)
            # Sorted by date: once the gap exceeds the window no later j qualifies
            if secs > time_window_seconds:
                break
            dist = _min_hash_distance(ph_i, ah_i, ph_j, ah_j)
            if dist <= hamming_threshold:
                union(idx_i, idx_j)

    # Build cluster groups from union-find roots
    from collections import defaultdict

    clusters: dict[int, list[int]] = defaultdict(list)
    for i, (photo_id, _ph, _ah, _date) in enumerate(rows):
        root = find(i)
        clusters[root].append(photo_id)

    # Assign cluster IDs — roots of multi-photo clusters get unique IDs.
    # Singletons get dup_cluster_id=0, cluster_size=1.
    # Use a simple incrementing counter starting at 1 so 0 always means
    # "no near-duplicate" even across multiple runs.
    updates: list[tuple[int, int, int]] = []  # (cluster_id, cluster_size, photo_id)
    cluster_counter = 1
    multi_count = 0

    for _root, members in clusters.items():
        size = len(members)
        if size == 1:
            updates.append((0, 1, members[0]))
        else:
            cid = cluster_counter
            cluster_counter += 1
            for photo_id in members:
                updates.append((cid, size, photo_id))
            multi_count += size

    # Reset photos not in the candidate set (missing phash, sidecar, etc.)
    # to ensure cluster_size reflects current state after any re-run.
    conn.execute(
        "UPDATE photos SET dup_cluster_id=0, cluster_size=1 "
        "WHERE missing=0 AND deleted_at IS NULL AND hidden_at IS NULL "
        "  AND (is_live_photo_sidecar=1 OR (phash IS NULL AND ahash IS NULL))"
    )
    conn.executemany(
        "UPDATE photos SET dup_cluster_id=?, cluster_size=? WHERE id=?",
        updates,
    )
    conn.commit()

    log.info(
        "Near-duplicate clustering: %d photos → %d non-singleton clusters "
        "(%d photos have a near-duplicate; threshold=%d bits)",
        len(rows),
        cluster_counter - 1,
        multi_count,
        hamming_threshold,
    )
    return multi_count
