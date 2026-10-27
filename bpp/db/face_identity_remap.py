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
extracted embedding joins an existing cluster (mean distance to its
faces, as Regroup's linkage + hard-negative gate) or seeds a new one (leftover clustering pass).

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
    from bpp.db.face_name_placement import _person_album_rows
    from bpp.db.face_name_relocate import _move_tags
    from bpp.db.people_placement import Person, apply, decide

    albums = {name: (aid, cid) for aid, name, cid in _person_album_rows(conn)}
    # Every labelled identity competes for clusters (an identity with no
    # album still claims its cluster); only those with an album move.
    people = [
        Person(key=n, name=n, album_id=albums[n][0] if n in albums else None, evidence=ids)
        for n, ids in labelled.items()
    ]
    best = decide(people, by_cluster)
    moving = {n: cid for n, cid in best.items() if n in albums and albums[n][1] != cid}
    for n, cid in moving.items():
        log.info("Identity reconstruct: '%s' cluster %s → %d", n, albums[n][1], cid)
    # A default "Person N" album on a destination gives way; another named
    # person's album does not. Stashed tags of an unplaced person come back.
    outcome = apply(
        conn,
        [p for p in people if p.key in moving],
        moving,
        displace_named=False,
        keep_unplaced=False,
        restore_stash=True,
    )
    # The manual tags a person had on the cluster the album leaves go with
    # it (a renumbering the names missed left them there); tags already on
    # the destination are the user's and are kept.
    _move_tags(
        conn,
        [(albums[n][1], cid) for n, cid in outcome.placed.items() if albums[n][1] >= 0],
        set(),
        clear_targets=False,
    )
    remapped = len(outcome.placed)
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

    The people are placed by ``face_name_relocate.renumber_people`` (the
    shared decide-and-apply step); a SAVEPOINT around it rolls a partial
    failure back cleanly. Commits on success.
    """
    # All-or-nothing: a failed UPDATE rolls the whole remap back and returns
    # False. The recluster endpoint keeps its renumbering uncommitted until
    # here and rolls it back on False, so every name stays on its person;
    # the extraction's phase 7 has committed its clusters and logs it.
    from bpp.db.face_name_relocate import renumber_people

    conn.execute("SAVEPOINT remap_clusters")
    try:
        renumber_people(
            conn,
            old_cluster_photos,
            new_cluster_photos,
            evidence_photos if evidence_photos is not None else old_cluster_photos,
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

    Each unassigned face joins the existing cluster whose ``assigned``
    faces are nearest on average, when that mean distance is under the
    threshold: the rule average linkage (Regroup) uses to attach a face
    to a cluster, so analysis and Regroup group alike. A distance to the
    cluster's mean face would be looser (a face can sit near the mean
    while far from every member) and merges different people. The faces
    left over are clustered among themselves so genuinely new people
    form groups, and each new group gets a fresh ID.

    Quality gating: small faces (< ``_MIN_ASSIGN_PX``) skip the matching
    and go straight to leftover clustering, preventing noisy embeddings
    from contaminating established clusters.

    Hard negative gating: if the best-match cluster has a hard negative
    partner that the face is also close to (mean distance within
    ``threshold * 1.5``), skip assignment to avoid sibling/lookalike
    confusion.

    Returns list of cluster IDs, same length as ``unassigned``.
    """
    from bpp.scoring.face_cluster import _nearest_cluster_by_mean, cluster_faces

    if hard_negatives is None:
        hard_negatives = {}

    # Each existing cluster's faces, as rows of one matrix labelled 0..k-1
    cids = sorted({rec[4] for rec in assigned})
    position = {cid: i for i, cid in enumerate(cids)}
    members = np.stack([rec[3] for rec in assigned]) if assigned else None
    member_labels = np.array([position[rec[4]] for rec in assigned], dtype=np.int64)
    # Row ranges per cluster from one sort (only the hard-negative check reads them)
    by_label = np.argsort(member_labels, kind="stable")
    bounds = np.searchsorted(member_labels[by_label], np.arange(len(cids) + 1))

    max_existing = max(cids) if cids else -1
    if next_id_floor is not None:
        max_existing = max(max_existing, next_id_floor)

    # Nearest cluster by mean distance for every new face, a bounded
    # chunk of rows at a time (the same helper Regroup's blocks use).
    n = len(unassigned)
    nearest = np.zeros(n, dtype=np.int64)
    nearest_dist = np.full(n, np.inf)
    if members is not None and n:
        rows = np.stack([u[3] for u in unassigned])
        nearest, nearest_dist = _nearest_cluster_by_mean(
            rows, np.arange(n), members, member_labels, len(cids)
        )
        del rows

    def _mean_distance(emb: np.ndarray, cid: int) -> float:
        i = position[cid]
        own = members[by_label[bounds[i] : bounds[i + 1]]].astype(np.float64)
        return float(np.linalg.norm(own - emb.astype(np.float64), axis=1).mean())

    labels: list[int] = []
    leftover_indices: list[int] = []

    for i, (_fp, _pid, _fi, _emb, bw, _quality) in enumerate(unassigned):
        # Small faces produce unreliable embeddings — don't assign
        # to existing clusters, let them form their own groups.
        if bw < _MIN_ASSIGN_PX or members is None:
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
                    if neg_cid in position:
                        neg_dist = _mean_distance(_emb, neg_cid)
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
        used_ids = set(cids)
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
