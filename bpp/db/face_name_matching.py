"""Matching old person clusters to new ones by overlap: one rule for the
recluster, the face-model switch's final placement and the smart-album
refresh's orphan transfer. Split out of :mod:`bpp.db.face_identity_remap`
for the 500-LOC cap; that module re-exports both matchers."""

from __future__ import annotations

from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _match_named_clusters(
    named_clusters: dict[int, str],
    old_cluster_photos: dict[int, set[int]],
    new_cluster_photos: dict[int, set[int]],
    remap: dict[int, int],
    claimed: set[int],
) -> None:
    """Each named old cluster claims the unclaimed new cluster with the
    most overlapping photos (no minimum: a name survives any overlap).

    Pairs are taken in order of overlap, strongest first, so a person
    with five photos in a cluster is not beaten to it by one whose single
    group photo happens to sit there. A tie is not decided: a name whose
    best overlap is shared by two clusters, or a cluster whose best
    claimant is tied between two names, is left unmatched (kept unplaced
    by the caller) rather than given to one of them arbitrarily."""
    pairs: list[tuple[int, int, int | str, int]] = []
    for old_cid in named_clusters:
        old_pids = old_cluster_photos.get(old_cid, set())
        for new_cid, new_pids in new_cluster_photos.items():
            if new_cid in claimed:
                continue
            overlap = len(old_pids & new_pids)
            if overlap:
                pairs.append((overlap, len(old_pids), old_cid, new_cid))
    # Level by level, strongest overlap first, over what is still open: a
    # pair is taken when its name and its cluster have no other open pair at
    # that level. A tie at a level blocks both the names and the clusters
    # in it for good (no falling back to a weaker cluster, which may be a
    # stranger's); a tie an earlier level already resolved blocks nothing.
    blocked_old: set = set()
    blocked_new: set = set()
    for level in sorted({t[0] for t in pairs}, reverse=True):
        open_pairs = [
            (old_cid, new_cid)
            for overlap, _n, old_cid, new_cid in pairs
            if overlap == level
            and old_cid not in remap
            and old_cid not in blocked_old
            and new_cid not in claimed
            and new_cid not in blocked_new
        ]
        per_old: dict = {}
        per_new: dict = {}
        for old_cid, new_cid in open_pairs:
            per_old[old_cid] = per_old.get(old_cid, 0) + 1
            per_new[new_cid] = per_new.get(new_cid, 0) + 1
        for old_cid, new_cid in open_pairs:
            if per_old[old_cid] > 1 or per_new[new_cid] > 1:
                blocked_old.add(old_cid)
                blocked_new.add(new_cid)
        for old_cid, new_cid in open_pairs:
            if old_cid in blocked_old or new_cid in blocked_new:
                continue
            remap[old_cid] = new_cid
            claimed.add(new_cid)
            log.info(
                "Name remap: '%s' cluster %s → %d (overlap %d/%d photos)",
                named_clusters[old_cid],
                old_cid,
                new_cid,
                level,
                len(old_cluster_photos.get(old_cid, ())),
            )
    for old_cid in sorted(blocked_old, key=str):
        log.info("Not deciding '%s': tied evidence", named_clusters[old_cid])


def _match_tag_clusters(
    tag_only_cids: set[int],
    old_cluster_photos: dict[int, set[int]],
    new_cluster_photos: dict[int, set[int]],
    remap: dict[int, int],
    claimed: set[int],
    min_jaccard: float,
) -> None:
    """A tag-only old cluster claims the unclaimed new cluster with the
    best Jaccard overlap, when it reaches ``min_jaccard``."""
    for old_cid in tag_only_cids:
        old_pids = old_cluster_photos.get(old_cid, set())
        if not old_pids:
            continue
        best_new_cid = None
        best_jaccard = 0.0
        for new_cid, new_pids in new_cluster_photos.items():
            if new_cid in claimed:
                continue
            intersection = len(old_pids & new_pids)
            if intersection == 0:
                continue
            union = len(old_pids | new_pids)
            jaccard = intersection / union
            if jaccard > best_jaccard:
                best_jaccard = jaccard
                best_new_cid = new_cid
        if best_new_cid is not None and best_jaccard >= min_jaccard:
            remap[old_cid] = best_new_cid
            claimed.add(best_new_cid)
