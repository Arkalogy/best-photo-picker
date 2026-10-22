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
from collections.abc import Callable

from bpp.utils.logging import get_logger

log = get_logger(__name__)

# Default hamming distance threshold (bits out of 64).
# 8 bits catches burst shots; raise to 12+ for more aggressive grouping.
DEFAULT_HAMMING_THRESHOLD = 8


def _date_to_seconds(date: str | None) -> float | None:
    """An ISO-8601 date as seconds, or None when missing / unparseable."""
    if not date:
        return None
    try:
        from datetime import datetime

        fmt = "%Y-%m-%dT%H:%M:%S" if "T" in date else "%Y-%m-%d %H:%M:%S"
        return datetime.strptime(date[:19], fmt).timestamp()
    except Exception:
        return None


DEFAULT_TIME_WINDOW_SECONDS = 60


def _link_near_duplicates(
    rows: list,
    union: Callable[[int, int], None],
    hamming_threshold: int,
    time_window_seconds: int,
) -> None:
    """Union every pair of ``rows`` (``(id, phash, ahash, date)``) that is
    within the hash threshold and the time window.
    """
    # Dates parsed once; rows sorted by time so each row's window is a
    # contiguous slice found by bisection. Undated rows (no EXIF, not yet
    # scored) sit in their own bucket at the end: they are compared with
    # each other only, and all of it vectorised, so a batch that shares
    # one timestamp or none at all is not a Python-level quadratic.
    import bisect

    import numpy as np

    from bpp.selection.diversity import _hamming_many

    u64 = (1 << 64) - 1
    dated: list[tuple[float, int]] = []
    undated: list[int] = []
    for i, (_id, _ph, _ah, date) in enumerate(rows):
        secs = _date_to_seconds(date)
        if secs is None:
            undated.append(i)
        else:
            dated.append((secs, i))
    dated.sort()

    def _buffers(indices: list[int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        ph = np.zeros(len(indices), dtype=np.uint64)
        ah = np.zeros(len(indices), dtype=np.uint64)
        has = np.zeros((len(indices), 2), dtype=bool)
        for k, i in enumerate(indices):
            _id, p, a, _d = rows[i]
            if p is not None:
                ph[k] = np.uint64(int(p) & u64)
                has[k, 0] = True
            if a is not None:
                ah[k] = np.uint64(int(a) & u64)
                has[k, 1] = True
        return ph, ah, has

    def _link_window(order: list[int], lo: int, hi: int, k: int) -> None:
        """Union row ``order[k]`` with every row in ``order[lo:hi]`` within
        the hash threshold (min over the hash types both rows carry)."""
        if hi <= lo:
            return
        i = order[k]
        _id, p, a, _d = rows[i]
        sl = slice(lo, hi)
        best = np.full(hi - lo, 65, dtype=np.int64)
        if p is not None:
            d = _hamming_many(int(p) & u64, ph_buf[sl]).astype(np.int64)
            best = np.where(has_buf[sl, 0], np.minimum(best, d), best)
        if a is not None:
            d = _hamming_many(int(a) & u64, ah_buf[sl]).astype(np.int64)
            best = np.where(has_buf[sl, 1], np.minimum(best, d), best)
        for off in np.nonzero(best <= hamming_threshold)[0]:
            union(i, order[lo + int(off)])

    order = [i for _s, i in dated]
    times = [s for s, _i in dated]
    ph_buf, ah_buf, has_buf = _buffers(order)
    for k in range(len(order)):
        hi = bisect.bisect_right(times, times[k] + time_window_seconds)
        _link_window(order, k + 1, hi, k)
    if undated:
        order = undated
        ph_buf, ah_buf, has_buf = _buffers(order)
        for k in range(len(order)):
            _link_window(order, k + 1, len(order), k)


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

    _link_near_duplicates(rows, union, hamming_threshold, time_window_seconds)

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
    if multi_count:
        from bpp.db.event_hooks import dispatch_post_cluster

        n_clusters = conn.execute(
            "SELECT COUNT(DISTINCT dup_cluster_id) FROM photos WHERE dup_cluster_id > 0"
        ).fetchone()[0]
        dispatch_post_cluster(conn, "near_duplicate", int(n_clusters))
    return multi_count
