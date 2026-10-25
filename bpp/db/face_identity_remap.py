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

    # Each identity's labelled face rows, and which cluster holds each:
    # matched by the shared rule (strongest first, a tie decided for no
    # one), so this step never undoes a tie a re-clustering left open.
    labelled: dict[str, set[int]] = {}
    by_cluster: dict[int, set[int]] = {}
    for fid, name, cid in conn.execute(
        "SELECT id, identity, cluster_id FROM face_embeddings "
        "WHERE identity IS NOT NULL AND cluster_id >= 0"
    ):
        labelled.setdefault(name, set()).add(int(fid))
        by_cluster.setdefault(int(cid), set()).add(int(fid))
    if not labelled:
        return
    best: dict[str, int] = {}
    _match_named_clusters({n: n for n in labelled}, labelled, by_cluster, best, set())

    # The moves are applied in one order-independent pass (park, then
    # place), so two people whose clusters swapped cannot collide on the
    # unique (album_type, rule_json) index. A default "Person N" album on a
    # destination gives way; another named person's album does not.
    from bpp.db.face_name_placement import move_named_albums

    albums = {
        row[1]: (int(row[0]), int(row[2]))
        for row in conn.execute(
            "SELECT id, name, smart_person_cluster_id FROM albums "
            "WHERE album_type='smart_person' AND smart_person_cluster_id IS NOT NULL"
        ).fetchall()
    }
    moves: list[tuple[int, int]] = []
    for name, best_cid in best.items():
        entry = albums.get(name)
        if entry is None or entry[1] == best_cid:
            continue  # no album for this identity, or already correct
        moves.append((entry[0], best_cid))
        log.info("Identity reconstruct: '%s' cluster %s → %d", name, entry[1], best_cid)

    applied, _ = move_named_albums(conn, moves)
    remapped = len(applied)
    if applied:
        from bpp.db.face_name_relocate import _move_tags
        from bpp.db.face_switch_record import restore_unplaced_tags

        # The manual tags a person had on the cluster the album leaves go
        # with the album (a renumbering the names missed left them there);
        # tags already on the destination are the user's and are kept.
        left = {aid: cid for aid, cid in albums.values()}
        _move_tags(
            conn,
            [(left[aid], new_cid) for aid, new_cid in applied if left[aid] >= 0],
            set(),
            clear_targets=False,
        )
        restore_unplaced_tags(conn, dict(applied))  # an unplaced person's tags come back
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


def _snapshot_cluster_faces(conn: sqlite3.Connection) -> dict[int, set[int]]:
    """Return ``{cluster_id: {face row id, ...}}``. A recluster keeps every
    face row, so matching by row says which face was whose; matching by
    photo cannot tell two people in the same photos apart."""
    result: dict[int, set[int]] = {}
    for cid, fid in conn.execute(
        "SELECT cluster_id, id FROM face_embeddings WHERE cluster_id >= 0"
    ):
        result.setdefault(cid, set()).add(fid)
    return result


def _is_default_person_name(name: str) -> bool:
    """Return True if the name is an auto-generated default like ``Person 5``."""
    import re

    return bool(re.match(r"^Person \d+$", name))


from bpp.db.face_name_matching import (  # noqa: E402  (re-exported for callers and tests)
    _match_named_clusters,
    _match_tag_clusters,
)


def _remap_names_and_tags(
    conn: sqlite3.Connection,
    old_cluster_photos: dict[int, set[int]],
    new_cluster_photos: dict[int, set[int]],
    evidence_photos: dict[int, set[int]] | None = None,
) -> bool:
    """Remap named person albums and photo_person_tags from old → new cluster IDs.
    The two maps may hold face row ids (a recluster: exact) or photo ids
    (a re-extraction); ``evidence_photos`` is then the old cluster → photo
    map an unplaced person's album keeps (default: ``old_cluster_photos``).
    Returns False when the moves failed and were rolled back (nothing committed).

    Matching:
      - Named clusters: pick the new cluster with the *most* overlapping
        photos. No minimum threshold — if the user named someone,
        preserve the name when any photos match. This handles embedding-
        method changes where clusters shift dramatically.
      - Tag-only clusters: Jaccard >= 0.1 (permissive but requires some
        overlap so we don't claim an unrelated cluster).

    The moves are applied by ``face_name_relocate.relocate_albums_and_tags``
    (order-independent, so swapped or chained ids work); a SAVEPOINT
    around them rolls a partial failure back cleanly. Commits on success.
    """
    TAG_MIN_JACCARD = 0.1

    remap: dict[int, int] = {}
    claimed: set[int] = set()

    # Collect named albums: old_cid → name.
    # Indexed shadow-column lookup via the canonical helper.
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
    _match_named_clusters(named_clusters, old_cluster_photos, new_cluster_photos, remap, claimed)
    # Remap tag-only clusters (Jaccard threshold)
    tag_only_cids = tag_cids - set(named_clusters.keys())
    _match_tag_clusters(
        tag_only_cids, old_cluster_photos, new_cluster_photos, remap, claimed, TAG_MIN_JACCARD
    )

    if not remap:
        log.warning(
            "No cluster remapping found — %d named clusters orphaned",
            len(named_clusters),
        )
    else:
        log.info(
            "Remapping %d clusters (%d named, %d tags) to new IDs",
            len(remap),
            len(set(remap) & set(named_clusters)),
            len(set(remap) & tag_only_cids),
        )

    # All-or-nothing: a failed UPDATE rolls the whole remap back and returns
    # False. The recluster endpoint keeps its renumbering uncommitted until
    # here and rolls it back on False, so every name stays on its person;
    # the extraction's phase 7 has committed its clusters and logs it.
    from bpp.db.face_name_relocate import relocate_albums_and_tags

    conn.execute("SAVEPOINT remap_clusters")
    try:
        relocate_albums_and_tags(
            conn,
            remap,
            named_clusters,
            tag_cids,
            evidence_photos if evidence_photos is not None else old_cluster_photos,
            set(new_cluster_photos),
        )
        conn.execute("RELEASE remap_clusters")
    except Exception:
        conn.execute("ROLLBACK TO remap_clusters")
        conn.execute("RELEASE remap_clusters")
        log.warning("Cluster remapping failed — rolled back", exc_info=True)
        return False

    conn.commit()
    return True


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
    next_id_floor: int | None = None,
) -> list[int]:
    """Assign new faces to existing clusters or create new ones.

    ``next_id_floor`` is the highest cluster id the library already holds
    (new ids start above it), for callers whose ``assigned`` records do
    not cover every cluster.

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
    if next_id_floor is not None:
        max_existing = max(max_existing, next_id_floor)

    # Nearest centroid per new face in one chunked matrix product
    # (``_nearest_centroid``: squared norms + a GEMM, never an
    # (faces x centroids x dim) intermediate), instead of a Python loop
    # over the centroids for each face. Only the hard-negative distances
    # that matter are computed afterwards, one small dot each.
    from bpp.scoring.face_cluster import _nearest_centroid

    cids = list(centroids)
    cent = np.stack([centroids[c] for c in cids]) if cids else None
    n = len(unassigned)
    nearest = np.zeros(n, dtype=np.int64)
    nearest_dist = np.full(n, np.inf)
    if cent is not None and n:
        # The new faces stay in their stored dtype; _nearest_centroid
        # promotes one chunk at a time.
        rows = np.stack([u[3] for u in unassigned])
        nearest, nearest_dist = _nearest_centroid(rows, cent)
        del rows

    labels: list[int] = []
    leftover_indices: list[int] = []

    for i, (_fp, _pid, _fi, _emb, bw, _quality) in enumerate(unassigned):
        # Small faces produce unreliable embeddings — don't assign
        # to existing clusters, let them form their own groups.
        if bw < _MIN_ASSIGN_PX or cent is None:
            labels.append(CLUSTER_UNASSIGNED)
            leftover_indices.append(i)
            continue

        best_cid = cids[int(nearest[i])]
        if float(nearest_dist[i]) < threshold:
            # Hard negative check: defer to leftover clustering if
            # the best cluster has a known confusable partner that
            # this face is also close to.
            skip = False
            neg_partners = hard_negatives.get(best_cid, set())
            if neg_partners:
                ambiguity_radius = threshold * 1.5
                for neg_cid in neg_partners:
                    if neg_cid in centroids:
                        neg_dist = float(np.linalg.norm(_emb - centroids[neg_cid]))
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
            if sl == CLUSTER_UNASSIGNED:
                continue  # a malformed row stays unassigned, never a new person
            if sl not in sub_remap:
                while next_id in used_ids:
                    next_id += 1
                sub_remap[sl] = next_id
                used_ids.add(next_id)
                next_id += 1

        for j, idx in enumerate(leftover_indices):
            if sub_labels[j] != CLUSTER_UNASSIGNED:
                labels[idx] = sub_remap[sub_labels[j]]

    return labels
