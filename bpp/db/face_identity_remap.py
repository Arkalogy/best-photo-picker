"""Identity reconstruction + cluster remapping after re-clustering.

Re-clustering mints fresh cluster_ids that don't match the IDs the
user named, so we have to remap:

  1. Named smart_person albums get repointed to whichever new cluster
     contains the most photos from the old named cluster (no minimum
     threshold — if the user named someone, preserve that name as
     long as any photos match).
  2. photo_person_tags rows follow the same remap.
  3. Any face row carrying an ``identity`` label is the source of
     truth — find the cluster with the most labeled faces for that
     name and pin the album to it (``_reconstruct_identities``).

Plus the new-faces assignment helper that decides whether a freshly
extracted embedding joins an existing cluster (centroid distance +
hard-negative gate) or seeds a new one (leftover clustering pass).

These helpers are pure DB/numpy logic with no worker state — re-
exported from face_worker.py so the existing ``from bpp.web.face_worker
import _reconstruct_identities`` style still resolves.
"""

from __future__ import annotations

import json
import sqlite3

import numpy as np

from bpp.constants import CLUSTER_UNASSIGNED
from bpp.db.dialect import dialect
from bpp.utils.logging import get_logger

log = get_logger(__name__)


# ── Identity reconstruction ──────────────────────────────────────────


def _reconstruct_identities(conn: sqlite3.Connection) -> None:
    """Reconstruct named albums from identity labels on face_embeddings.

    After re-clustering, cluster IDs may change. Identity labels (set
    when the user names a person) persist across re-clustering because
    they live on the face row, not on the cluster. This function
    finds the best cluster for each identity and updates the
    smart_person album.
    """
    cols = dialect.column_names(conn, "face_embeddings")
    if "identity" not in cols:
        return

    rows = conn.execute(
        "SELECT identity, cluster_id, COUNT(*) as cnt "
        "FROM face_embeddings "
        "WHERE identity IS NOT NULL AND cluster_id >= 0 "
        "GROUP BY identity, cluster_id "
        "ORDER BY identity, cnt DESC"
    ).fetchall()

    if not rows:
        return

    # For each identity, find the cluster with the most labeled faces
    identity_best: dict[str, tuple[int, int]] = {}
    for name, cid, cnt in rows:
        if name not in identity_best or cnt > identity_best[name][1]:
            identity_best[name] = (cid, cnt)

    if not identity_best:
        return

    remapped = 0
    for name, (best_cid, _count) in identity_best.items():
        # P5b: read the shadow column directly instead of parsing
        # rule_json. Positional access so this works without
        # sqlite3.Row factory.
        album = conn.execute(
            "SELECT id, smart_person_cluster_id FROM albums "
            "WHERE album_type='smart_person' AND name=?",
            (name,),
        ).fetchone()
        if not album:
            continue
        old_cid = album[1]
        if old_cid == best_cid:
            continue  # Already correct
        new_rule = json.dumps({"cluster_id": best_cid}, sort_keys=True)
        conn.execute(
            "UPDATE albums SET rule_json=?, smart_person_cluster_id=? WHERE id=?",
            (new_rule, best_cid, album[0]),
        )
        log.info(
            "Identity reconstruct: '%s' cluster %s → %d (%d labeled faces)",
            name,
            old_cid,
            best_cid,
            _count,
        )
        remapped += 1

    if remapped:
        conn.commit()
        log.info("Reconstructed %d named albums from identity labels", remapped)


# ── Cluster snapshot + name/tag remap ────────────────────────────────


def _snapshot_cluster_photos(conn: sqlite3.Connection) -> dict[int, set[int]]:
    """Return ``{cluster_id: {photo_id, ...}}`` for all assigned embeddings."""
    rows = conn.execute(
        "SELECT cluster_id, photo_id FROM face_embeddings WHERE cluster_id >= 0"
    ).fetchall()
    result: dict[int, set[int]] = {}
    for cid, pid in rows:
        result.setdefault(cid, set()).add(pid)
    return result


def _is_default_person_name(name: str) -> bool:
    """Return True if the name is an auto-generated default like ``Person 5``."""
    import re

    return bool(re.match(r"^Person \d+$", name))


def _remap_names_and_tags(
    conn: sqlite3.Connection,
    old_cluster_photos: dict[int, set[int]],
    new_cluster_photos: dict[int, set[int]],
) -> None:
    """Remap named person albums and photo_person_tags from old → new cluster IDs.

    Matching:
      - Named clusters: pick the new cluster with the *most* overlapping
        photos. No minimum threshold — if the user named someone,
        preserve the name when any photos match. This handles embedding-
        method changes where clusters shift dramatically.
      - Tag-only clusters: Jaccard >= 0.1 (permissive but requires some
        overlap so we don't claim an unrelated cluster).

    SAVEPOINT around the bulk UPDATEs so a partial failure rolls back
    cleanly instead of leaving half the albums pointing at stale IDs.
    """
    TAG_MIN_JACCARD = 0.1

    remap: dict[int, int] = {}
    claimed: set[int] = set()

    # Collect named albums: old_cid → name.
    # P5b: indexed shadow-column lookup via the canonical helper.
    named_clusters: dict[int, str] = {}
    try:
        from bpp.db.albums import get_smart_person_cluster_name_map

        for cid, name in get_smart_person_cluster_name_map(conn).items():
            if not _is_default_person_name(name):
                named_clusters[cid] = name
    except sqlite3.OperationalError as e:
        log.debug("albums table not ready, skipping: %s", e)

    # Also collect photo_person_tags cluster IDs
    tag_cids: set[int] = set()
    try:
        tag_rows = conn.execute("SELECT DISTINCT cluster_id FROM photo_person_tags").fetchall()
        tag_cids = {r[0] for r in tag_rows}
    except sqlite3.OperationalError as e:
        log.debug("photo_person_tags table not ready, skipping: %s", e)

    # Remap named clusters first (priority — best-overlap, no threshold)
    for old_cid in sorted(named_clusters, key=lambda c: -len(old_cluster_photos.get(c, set()))):
        old_pids = old_cluster_photos.get(old_cid, set())
        if not old_pids:
            continue
        best_new_cid = None
        best_overlap = 0
        for new_cid, new_pids in new_cluster_photos.items():
            if new_cid in claimed:
                continue
            overlap = len(old_pids & new_pids)
            if overlap > best_overlap:
                best_overlap = overlap
                best_new_cid = new_cid
        if best_new_cid is not None and best_overlap > 0:
            remap[old_cid] = best_new_cid
            claimed.add(best_new_cid)
            log.info(
                "Name remap: '%s' cluster %d → %d (overlap %d/%d photos)",
                named_clusters[old_cid],
                old_cid,
                best_new_cid,
                best_overlap,
                len(old_pids),
            )

    # Remap tag-only clusters (Jaccard threshold)
    tag_only_cids = tag_cids - set(named_clusters.keys())
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
        if best_new_cid is not None and best_jaccard >= TAG_MIN_JACCARD:
            remap[old_cid] = best_new_cid
            claimed.add(best_new_cid)

    if not remap:
        log.warning(
            "No cluster remapping found — %d named clusters orphaned",
            len(named_clusters),
        )
        return

    log.info(
        "Remapping %d clusters (%d named, %d tags) to new IDs",
        len(remap),
        len(set(remap) & set(named_clusters)),
        len(set(remap) & tag_only_cids),
    )

    # All-or-nothing: if any UPDATE fails, roll back the whole remap
    # so we don't leave half the albums pointing at stale cluster IDs.
    conn.execute("SAVEPOINT remap_clusters")
    try:
        # Pre-load all smart_person albums keyed by smart_person_cluster_id
        # for O(1) lookups (P5b: use the v36 shadow column instead of
        # rule_json as the key — same data, cheaper lookup). Positional
        # access so this works without sqlite3.Row factory.
        person_albums_by_cid: dict[int, tuple[int, str]] = {}
        for row in conn.execute(
            "SELECT id, name, smart_person_cluster_id FROM albums "
            "WHERE album_type='smart_person' "
            "AND smart_person_cluster_id IS NOT NULL"
        ).fetchall():
            person_albums_by_cid[int(row[2])] = (row[0], row[1])

        # Remap album rule_json for named clusters
        orphan_album_ids: list[int] = []
        for old_cid, name in named_clusters.items():
            new_cid = remap.get(old_cid)
            if new_cid is None or new_cid == old_cid:
                continue  # No remap needed (unmapped or identity)
            new_rule = json.dumps({"cluster_id": new_cid}, sort_keys=True)
            existing = person_albums_by_cid.get(new_cid)
            if existing:
                # New cluster already has an album — update its name
                conn.execute("UPDATE albums SET name=? WHERE id=?", (name, existing[0]))
                old_entry = person_albums_by_cid.get(old_cid)
                if old_entry:
                    orphan_album_ids.append(old_entry[0])
            else:
                # No album for the new cluster yet — repoint the old
                # album's rule_json AND shadow column to the new cid.
                conn.execute(
                    "UPDATE albums SET rule_json=?, smart_person_cluster_id=? "
                    "WHERE album_type='smart_person' "
                    "AND smart_person_cluster_id=?",
                    (new_rule, new_cid, old_cid),
                )
            log.info("Remapped person '%s': cluster %d → %d", name, old_cid, new_cid)

        # Batch-delete orphaned albums
        if orphan_album_ids:
            ph = ",".join("?" * len(orphan_album_ids))
            conn.execute(f"DELETE FROM album_photos WHERE album_id IN ({ph})", orphan_album_ids)
            conn.execute(f"DELETE FROM albums WHERE id IN ({ph})", orphan_album_ids)

        # Remap photo_person_tags in batch (UPDATE OR IGNORE handles
        # primary-key conflicts where both old and new cluster tags exist).
        remap_pairs = [
            (old_cid, new_cid)
            for old_cid, new_cid in remap.items()
            if old_cid != new_cid and old_cid in tag_cids
        ]
        if remap_pairs:
            conn.executemany(
                "UPDATE OR IGNORE photo_person_tags SET cluster_id=? WHERE cluster_id=?",
                [(new, old) for old, new in remap_pairs],
            )
            old_cids = [old for old, _ in remap_pairs]
            ph = ",".join("?" * len(old_cids))
            conn.execute(
                f"DELETE FROM photo_person_tags WHERE cluster_id IN ({ph})",
                old_cids,
            )

        conn.execute("RELEASE remap_clusters")
    except Exception:
        conn.execute("ROLLBACK TO remap_clusters")
        conn.execute("RELEASE remap_clusters")
        log.warning("Cluster remapping failed — rolled back", exc_info=True)
        return

    conn.commit()


# ── Quality + assignment ─────────────────────────────────────────────


def _bbox_quality(bbox_w: int) -> float:
    """Estimate face embedding quality from bbox width.

    112 px is the optimal input size for SFace; smaller crops produce
    noisier embeddings. Returns ``[0.0, 1.0]``.
    """
    _OPTIMAL_PX = 112
    return min(bbox_w / _OPTIMAL_PX, 1.0) if bbox_w > 0 else 0.0


# Faces smaller than this are too unreliable for cluster assignment;
# they form their own clusters instead.
_MIN_ASSIGN_PX = 40


def _assign_new_faces(
    assigned: list[tuple[str, int, int, np.ndarray, int, int, float | None]],
    unassigned: list[tuple[str, int, int, np.ndarray, int, float | None]],
    threshold: float,
    hard_negatives: dict[int, set[int]] | None = None,
) -> list[int]:
    """Assign new faces to existing clusters or create new ones.

    For each unassigned face, find the nearest existing cluster centroid.
    If distance < threshold, assign to that cluster. Otherwise, cluster
    all remaining unassigned faces among themselves so genuinely new
    people form groups, then give each new group a fresh ID.

    Quality gating: small faces (< ``_MIN_ASSIGN_PX``) skip centroid
    matching and go straight to leftover clustering, preventing noisy
    embeddings from contaminating established clusters.

    Hard negative gating: if the best-match cluster has a hard negative
    partner that the face is also close to (within ``threshold * 1.5``),
    skip assignment to avoid sibling/lookalike confusion.

    Centroids are quality-weighted using stored quality scores
    (frontality, size, confidence, sharpness). Falls back to bbox-width
    proxy when quality is not available.

    Returns list of cluster IDs, same length as ``unassigned``.
    """
    from bpp.scoring.face_cluster import cluster_faces

    if hard_negatives is None:
        hard_negatives = {}

    # Build quality-weighted centroids for existing clusters
    cluster_embs: dict[int, list[tuple[np.ndarray, float]]] = {}
    for _fp, _pid, _fi, emb, cid, bw, quality in assigned:
        q = quality if quality is not None else _bbox_quality(bw)
        cluster_embs.setdefault(cid, []).append((emb, q))

    centroids: dict[int, np.ndarray] = {}
    for cid, emb_q_pairs in cluster_embs.items():
        embs = np.stack([e for e, _q in emb_q_pairs])
        weights = np.array([max(q, 0.1) for _e, q in emb_q_pairs])
        weights = weights / weights.sum()
        centroids[cid] = np.average(embs, axis=0, weights=weights)

    max_existing = max(centroids.keys()) if centroids else -1

    labels: list[int] = []
    leftover_indices: list[int] = []

    for i, (_fp, _pid, _fi, emb, bw, _quality) in enumerate(unassigned):
        # Small faces produce unreliable embeddings — don't assign
        # to existing clusters, let them form their own groups.
        if bw < _MIN_ASSIGN_PX:
            labels.append(CLUSTER_UNASSIGNED)
            leftover_indices.append(i)
            continue

        if centroids:
            best_cid = CLUSTER_UNASSIGNED
            best_dist = float("inf")
            for cid, centroid in centroids.items():
                dist = float(np.linalg.norm(emb - centroid))
                if dist < best_dist:
                    best_dist = dist
                    best_cid = cid
            if best_dist < threshold:
                # Hard negative check: defer to leftover clustering if
                # the best cluster has a known confusable partner that
                # this face is also close to.
                skip = False
                neg_partners = hard_negatives.get(best_cid, set())
                if neg_partners:
                    ambiguity_radius = threshold * 1.5
                    for neg_cid in neg_partners:
                        if neg_cid in centroids:
                            neg_dist = float(np.linalg.norm(emb - centroids[neg_cid]))
                            if neg_dist < ambiguity_radius:
                                skip = True
                                break
                if skip:
                    labels.append(CLUSTER_UNASSIGNED)
                    leftover_indices.append(i)
                else:
                    labels.append(best_cid)
            else:
                labels.append(CLUSTER_UNASSIGNED)
                leftover_indices.append(i)
        else:
            labels.append(CLUSTER_UNASSIGNED)
            leftover_indices.append(i)

    # Cluster leftover faces among themselves to form new groups
    if leftover_indices:
        leftover_embs = [unassigned[i][3] for i in leftover_indices]
        if len(leftover_embs) == 1:
            sub_labels = [0]
        else:
            sub_labels = cluster_faces(leftover_embs, threshold=threshold)

        next_id = max_existing + 1
        used_ids = set(centroids.keys())
        sub_remap: dict[int, int] = {}
        for sl in sub_labels:
            if sl not in sub_remap:
                while next_id in used_ids:
                    next_id += 1
                sub_remap[sl] = next_id
                used_ids.add(next_id)
                next_id += 1

        for j, idx in enumerate(leftover_indices):
            labels[idx] = sub_remap[sub_labels[j]]

    return labels
