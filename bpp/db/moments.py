"""Moments — visually-similar shots grouped for review & pruning.

A *Moment* is a set of photos that are visually similar (CLIP cosine within
a threshold) AND taken near in time. It's the looser cousin of the tight
phash near-duplicate clustering in :mod:`bpp.db.dedupe`: "Duplicates" catches
near-identical frames, "Moments" catches the burst-plus-variation case — the
baby blinked, the baby shifted, the cat wandered in — that a user wants to
review and prune down to the keeper(s).

:func:`assign_moment_clusters` is the writer (mirrors
``assign_near_duplicate_clusters``): it loads active photos + their CLIP
embeddings, runs the shared pass-1 time-windowed clustering
(:func:`bpp.dedupe.semantic.cluster_time_windowed`), and writes
``moment_cluster_id`` / ``moment_size`` for every photo. The Moments review
surface queries ``WHERE moment_size > 1``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from bpp.utils.logging import get_logger

log = get_logger(__name__)

# Defaults per the Moments spec (pm.md). Looser than the 0.92 pick-dedup and
# wider than the 30s pick time window: the cost of slightly-too-loose is one
# extra glance at a batch; too-tight silently splits similar shots apart and
# defeats the feature.
DEFAULT_MOMENT_THRESHOLD = 0.90
DEFAULT_MOMENT_TIME_WINDOW_SECONDS = 90

# Photos eligible to be grouped: present, not trashed, not hidden, not a Live
# Photo sidecar. Same eligibility as the phash dup clusterer.
_ACTIVE_PHOTO_WHERE = (
    "missing=0 AND deleted_at IS NULL AND hidden_at IS NULL AND is_live_photo_sidecar=0"
)


def assign_moment_clusters(
    conn: sqlite3.Connection,
    *,
    threshold: float = DEFAULT_MOMENT_THRESHOLD,
    time_window_seconds: int = DEFAULT_MOMENT_TIME_WINDOW_SECONDS,
) -> int:
    """Compute Moment clusters and write them to the DB.

    Loads active, non-sidecar photos + their CLIP embeddings, groups them by
    CLIP cosine ``threshold`` within ``time_window_seconds`` (the shared
    pass-1 clustering), and writes ``moment_cluster_id`` / ``moment_size`` for
    every photo. Singletons (no similar sibling, or no embedding) get
    ``moment_cluster_id=0`` / ``moment_size=1``.

    Degrades gracefully: if CLIP embeddings exceed the load cap, logs and
    returns 0 without touching the columns (a too-large library just has no
    Moments rather than OOMing).

    Returns the count of photos that landed in a non-singleton Moment.
    """
    from bpp.db.clip import ClipEmbeddingsTooLarge, get_all_clip_embeddings
    from bpp.dedupe.semantic import cluster_time_windowed

    try:
        clip_embeddings = get_all_clip_embeddings(conn)
    except ClipEmbeddingsTooLarge as e:
        log.warning("Moments: CLIP embeddings too large to cluster (%s) — skipping", e)
        return 0

    rows = conn.execute(
        f"SELECT id, filepath, date FROM photos WHERE {_ACTIVE_PHOTO_WHERE}"
    ).fetchall()

    # Reset everything NOT in the candidate set (sidecars, trashed, hidden,
    # missing) so stale moment values can't linger after a re-run.
    conn.execute(
        "UPDATE photos SET moment_cluster_id=0, moment_size=1 "
        "WHERE NOT (" + _ACTIVE_PHOTO_WHERE + ")"
    )

    if not rows:
        conn.commit()
        return 0

    # Map filepath -> embedding for the items that have one; photos without a
    # CLIP embedding pass through as singletons (cluster_time_windowed keeps
    # them, they just never match anything).
    items: list[dict[str, Any]] = [
        {"id": pid, "filepath": fp, "date": date} for (pid, fp, date) in rows
    ]
    fp_to_emb = {
        item["filepath"]: clip_embeddings[item["id"]]
        for item in items
        if item["id"] in clip_embeddings
    }

    # cluster_time_windowed expects items pre-sorted by date.
    items.sort(key=lambda x: x.get("date") or "")
    clusters = cluster_time_windowed(items, fp_to_emb, threshold, time_window_seconds)

    # Assign ids: multi-photo groups get a unique counter from 1; singletons
    # get 0 so "0" always means "no Moment" across re-runs.
    updates: list[tuple[int, int, int]] = []  # (moment_cluster_id, moment_size, photo_id)
    counter = 1
    multi_count = 0
    for cluster in clusters:
        size = len(cluster)
        if size == 1:
            updates.append((0, 1, cluster[0]["id"]))
        else:
            cid = counter
            counter += 1
            for item in cluster:
                updates.append((cid, size, item["id"]))
            multi_count += size

    conn.executemany(
        "UPDATE photos SET moment_cluster_id=?, moment_size=? WHERE id=?",
        updates,
    )
    conn.commit()

    log.info(
        "Moments: %d active photos -> %d in %d multi-photo moment(s) (threshold=%.2f, window=%ds)",
        len(rows),
        multi_count,
        counter - 1,
        threshold,
        time_window_seconds,
    )
    return multi_count


def get_moment_groups(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return every multi-photo Moment as a group of photo ids.

    Each group: ``{"moment_cluster_id": int, "size": int, "photo_ids": [int]}``,
    ordered by cluster id. The Phase-2 review UI hydrates these into the
    keeper-ranked review cards; this query is the storage-side contract.
    """
    rows = conn.execute(
        f"SELECT moment_cluster_id, id FROM photos "
        f"WHERE moment_size > 1 AND {_ACTIVE_PHOTO_WHERE} "
        f"ORDER BY moment_cluster_id, id"
    ).fetchall()

    groups: dict[int, list[int]] = {}
    for moment_id, photo_id in rows:
        groups.setdefault(moment_id, []).append(photo_id)

    return [
        {"moment_cluster_id": mid, "size": len(pids), "photo_ids": pids}
        for mid, pids in groups.items()
    ]
