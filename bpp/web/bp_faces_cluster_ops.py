"""Cluster operations on face clusters — merge / dismiss / split /
restore / recluster.

Extracted from ``bpp.web.bp_faces_manage`` during the v0.1 cleanup. Owns
its own Flask blueprint and is registered explicitly by ``app.py``
alongside ``bp_faces_manage``. (Earlier we used a side-effect-import
pattern; it has since been converted to explicit registration.)
"""

from __future__ import annotations

from typing import Any

import numpy as np
from flask import Blueprint, Response, jsonify, request

from bpp.constants import (
    ACTIVE_PHOTO_SQL,
    CLUSTER_DISMISSED,
)
from bpp.db.albums import (
    list_albums as db_list_albums,
)
from bpp.db.face_cluster_ops import (
    cleanup_person_albums as _cleanup_person_albums,
)
from bpp.db.face_cluster_ops import (
    sync_face_counts_for_photos as _sync_face_counts_for_photos,
)
from bpp.db.face_feedback import (
    store_face_feedback,
    store_hard_negative,
)
from bpp.db.smart_albums import get_affected_album_types, refresh_smart_albums
from bpp.errors import ValidationError
from bpp.scoring.face_threshold import face_threshold
from bpp.utils.logging import get_logger
from bpp.web.face_merge_core import assert_merge_within_cap, perform_face_merge
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx, with_face_lock

bp = Blueprint("faces_cluster_ops", __name__)

log = get_logger(__name__)

#: Budget for one block of the face x centroid distance product.
NEAREST_BLOCK_BYTES = 64 * 1024 * 1024

_ACTIVE = ACTIVE_PHOTO_SQL


@bp.post("/api/v1/faces/merge")
@requires_local_app
@with_face_lock
def api_faces_merge() -> tuple[Response, int]:
    """Merge one or more face clusters into a primary cluster.

    Records pre-merge centroid distances as feedback for the adaptive
    threshold, clears any hard-negative pair between merged clusters,
    cascades the cluster_id change through photo_person_tags, prunes
    obsolete person albums, and refreshes person/group/unsorted smart
    albums."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    primary_id = data.get("primary_cluster_id")
    merge_ids = data.get("merge_cluster_ids", [])
    if primary_id is None or not merge_ids:
        raise ValidationError("Need primary_cluster_id and merge_cluster_ids")
    all_ids = [primary_id, *merge_ids]
    if not all(isinstance(cid, int) and cid >= 0 for cid in all_ids):
        raise ValidationError("Cluster IDs must be non-negative integers")

    conn = ctx.get_conn()
    ids_to_merge = [cid for cid in merge_ids if cid != primary_id]
    if not ids_to_merge:
        log.info("Face merge no-op: primary=%s, no other clusters to merge", primary_id)
        albums = db_list_albums(conn)
        return jsonify({"status": "merged", "albums": albums}), 200

    log.info(
        "Merging %d face cluster(s) into primary=%s: %s",
        len(ids_to_merge),
        primary_id,
        ids_to_merge,
    )

    # Cap check before the np.stack centroid work in the core: refuse with a
    # structured 503 so a merge of very large groups on a huge library shows
    # a banner instead of an OOM-kill mid-request (mirrors the restore-
    # reassignment guard later in this file).
    from bpp.db.face_queries import FaceEmbeddingsTooLarge

    try:
        assert_merge_within_cap(conn, all_ids)
    except FaceEmbeddingsTooLarge as e:
        log.warning("Refusing face merge: %s", e)
        return jsonify(
            {
                "error": str(e),
                "code": "face_embeddings_too_large",
                "count": e.count,
                "cap": e.cap,
            }
        ), 503

    merge_result = perform_face_merge(conn, primary_id, ids_to_merge)

    result = {"status": "merged", "albums": merge_result["albums"]}
    if "warning" in merge_result:
        result["warning"] = merge_result["warning"]
    return jsonify(result), 200


@bp.post("/api/v1/faces/dismiss")
@requires_local_app
@with_face_lock
def api_faces_dismiss() -> tuple[Response, int]:
    """Mark one or more face clusters as dismissed (the "Ignored"
    bucket). Accepts ``cluster_ids`` (list) or single ``cluster_id``,
    drops their photo_person_tags rows, syncs face_count on affected
    photos, removes their person albums, and refreshes smart albums."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    cluster_ids = data.get("cluster_ids")
    if cluster_ids is None:
        cid = data.get("cluster_id")
        if cid is None:
            raise ValidationError("cluster_id or cluster_ids required")
        cluster_ids = [cid]
    if not all(isinstance(cid, int) and cid >= 0 for cid in cluster_ids):
        raise ValidationError("Cluster IDs must be non-negative integers")

    conn = ctx.get_conn()
    placeholders = ", ".join(["?"] * len(cluster_ids))
    # Capture affected photo_ids before cluster_id changes
    affected_photos = [
        r["photo_id"]
        for r in conn.execute(
            f"SELECT DISTINCT photo_id FROM face_embeddings WHERE cluster_id IN ({placeholders})",
            cluster_ids,
        ).fetchall()
    ]
    conn.execute(
        f"UPDATE face_embeddings SET cluster_id={CLUSTER_DISMISSED}"
        f" WHERE cluster_id IN ({placeholders})",
        cluster_ids,
    )
    # Remove person tags for dismissed clusters
    conn.execute(
        f"DELETE FROM photo_person_tags WHERE cluster_id IN ({placeholders})",
        cluster_ids,
    )
    conn.commit()

    # Update face_count on affected photos
    _sync_face_counts_for_photos(conn, affected_photos)

    _cleanup_person_albums(conn, cluster_ids)
    refresh_smart_albums(conn, kinds=get_affected_album_types("face_tag"))

    albums = db_list_albums(conn)
    return jsonify({"status": "dismissed", "count": len(cluster_ids), "albums": albums}), 200


@bp.post("/api/v1/faces/split")
@requires_local_app
@with_face_lock
def api_faces_split() -> tuple[Response, int]:
    """Split selected faces out of their cluster into a new one.

    Accepts JSON: {face_ids: [int, ...]}
    Moves all listed face_ids to a fresh cluster_id and records a hard
    negative between the old and new clusters so they won't be re-merged
    by incremental assignment.
    """
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    face_ids = data.get("face_ids")
    if not face_ids or not isinstance(face_ids, list):
        raise ValidationError(
            "face_ids (non-empty list) required",
            field="face_ids",
        )
    if not all(isinstance(fid, int) for fid in face_ids):
        raise ValidationError("face_ids must be integers", field="face_ids")

    conn = ctx.get_conn()

    # Find the current cluster of the first face (all should be same cluster)
    placeholders = ",".join(["?"] * len(face_ids))
    rows = conn.execute(
        f"SELECT DISTINCT cluster_id FROM face_embeddings WHERE id IN ({placeholders})",
        face_ids,
    ).fetchall()
    old_cids = {r[0] for r in rows}

    # A new cluster id above every id in use, tag-only people's included.
    from bpp.db.face_cluster_ids import next_cluster_id

    new_cid = next_cluster_id(conn)

    # Move selected faces to new cluster
    conn.execute(
        f"UPDATE face_embeddings SET cluster_id=? WHERE id IN ({placeholders})",
        [new_cid, *face_ids],
    )
    conn.commit()

    # Record hard negatives between new cluster and each old cluster

    for old_cid in old_cids:
        if old_cid >= 0:
            store_hard_negative(conn, old_cid, new_cid)

    # Record feedback: "different" signal
    try:
        for old_cid in old_cids:
            if old_cid >= 0:
                store_face_feedback(
                    conn,
                    "reassign_out",
                    cluster_id_a=old_cid,
                    distance=0.0,
                )
    except Exception:
        log.warning("Failed to record split feedback", exc_info=True)

    refresh_smart_albums(conn, kinds=get_affected_album_types("face_tag"))
    albums = db_list_albums(conn)
    return jsonify(
        {
            "status": "split",
            "new_cluster_id": new_cid,
            "count": len(face_ids),
            "albums": albums,
        }
    ), 200


@bp.post("/api/v1/faces/restore")
@requires_local_app
@with_face_lock
def api_faces_restore() -> tuple[Response, int]:
    """Restore dismissed face embeddings back to unassigned.

    Accepts:
    - ``face_ids``: list of specific face_embedding IDs to restore
    - ``all: true``: restore all dismissed faces
    Restored faces get ``CLUSTER_UNASSIGNED`` (-1) so the next recluster
    picks them up.
    """

    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    restore_all = data.get("all", False)
    face_ids = data.get("face_ids")

    if not restore_all and not face_ids:
        raise ValidationError("face_ids or all required")
    if face_ids is not None and (
        not isinstance(face_ids, list) or not all(isinstance(fid, int) for fid in face_ids)
    ):
        raise ValidationError(
            "face_ids must be a list of integers",
            field="face_ids",
        )

    conn = ctx.get_conn()
    count = _restore_to_unassigned(conn, restore_all, face_ids)

    if count > 0:
        # Incremental assignment: assign each restored face to its nearest
        # existing cluster. Never global-recluster — that destroys named
        # cluster mappings.
        from bpp.db.face_queries import FaceEmbeddingsTooLarge

        try:
            _assign_unassigned_to_nearest(ctx, conn)
        except FaceEmbeddingsTooLarge as e:
            log.warning("Refusing face restore reassignment: %s", e)
            return jsonify(
                {
                    "error": str(e),
                    "code": "face_embeddings_too_large",
                    "count": e.count,
                    "cap": e.cap,
                    "restored": count,
                }
            ), 503
        refresh_smart_albums(conn, kinds=get_affected_album_types("face_tag"))

    albums = db_list_albums(conn)
    return jsonify({"status": "restored", "count": count, "albums": albums}), 200


def _restore_to_unassigned(conn: Any, restore_all: bool, face_ids: list[int]) -> int:
    """Move the dismissed faces (all, or the given ids) to
    ``CLUSTER_UNASSIGNED``; returns how many rows changed."""
    from bpp.constants import CLUSTER_UNASSIGNED

    if restore_all:
        cursor = conn.execute(
            f"UPDATE face_embeddings SET cluster_id = {CLUSTER_UNASSIGNED}"
            f" WHERE cluster_id = {CLUSTER_DISMISSED}"
        )
    else:
        placeholders = ",".join(["?"] * len(face_ids))
        cursor = conn.execute(
            f"UPDATE face_embeddings SET cluster_id = {CLUSTER_UNASSIGNED}"
            f" WHERE id IN ({placeholders})"
            f" AND cluster_id = {CLUSTER_DISMISSED}",
            face_ids,
        )
    count = cursor.rowcount
    conn.commit()
    return count


def _assign_unassigned_to_nearest(ctx: Any, conn: Any) -> None:
    """Assign every unassigned face to its nearest existing cluster (or a
    new one). Raises ``FaceEmbeddingsTooLarge`` above the load cap."""
    from bpp.constants import CLUSTER_UNASSIGNED
    from bpp.db.face_embedding_safety import decode_rows
    from bpp.db.face_feedback import get_hard_negatives
    from bpp.db.face_queries import assert_face_load_cap

    # Cap check before the np.stack work: the centroid + reassignment
    # pass loads every active face into memory. Refuse early on huge
    # libraries with a structured 503 so the user sees a banner
    # instead of an OOM-kill mid-request.
    active_count = conn.execute(
        f"SELECT COUNT(*) FROM face_embeddings WHERE cluster_id != {CLUSTER_DISMISSED}"
    ).fetchone()[0]
    assert_face_load_cap(conn, active_count)

    # Build hard-negative bidirectional map (user-separated pairs)
    neg_pairs = get_hard_negatives(conn)
    hard_neg_map: dict[int, set[int]] = {}
    for pair in neg_pairs:
        a, b = pair["cluster_id_a"], pair["cluster_id_b"]
        hard_neg_map.setdefault(a, set()).add(b)
        hard_neg_map.setdefault(b, set()).add(a)

    # Compute centroids for all existing (non-dismissed, non-unassigned) clusters
    existing_rows = conn.execute(
        "SELECT cluster_id, embedding FROM face_embeddings WHERE cluster_id >= 0"
    ).fetchall()
    cluster_embs: dict[int, list[np.ndarray]] = {}
    for cid, emb in decode_rows(
        conn,
        ((r["cluster_id"], r["embedding"]) for r in existing_rows),
        where="bp_faces_cluster_ops.centroids",
    ):
        cluster_embs.setdefault(cid, []).append(emb)
    centroids = {cid: np.mean(embs, axis=0) for cid, embs in cluster_embs.items()}
    if not centroids:
        return

    threshold = face_threshold(conn)
    # Assign each unassigned face to nearest cluster or create new
    unassigned = conn.execute(
        f"SELECT id, embedding FROM face_embeddings WHERE cluster_id = {CLUSTER_UNASSIGNED}"
    ).fetchall()
    assigned_count = 0
    new_count = 0
    usable = decode_rows(
        conn,
        ((r["id"], r["embedding"]) for r in unassigned),
        where="bp_faces_cluster_ops.unassigned",
    )
    usable_ids = {i for i, _ in usable}
    unassigned = [r for r in unassigned if r["id"] in usable_ids]
    if unassigned:
        updates, assigned_count, new_count = _nearest_cluster_updates(
            [r["id"] for r in unassigned],
            np.stack([emb for _, emb in usable]),
            centroids,
            hard_neg_map,
            threshold,
        )
        conn.executemany(
            "UPDATE face_embeddings SET cluster_id = ? WHERE id = ?",
            updates,
        )
    conn.commit()
    log.info(
        "Incremental restore: %d faces → %d assigned to existing, %d new clusters",
        len(unassigned),
        assigned_count,
        new_count,
    )


def _nearest_cluster_updates(
    face_ids: list[int],
    embs_mat: np.ndarray,
    centroids: dict[int, np.ndarray],
    hard_neg_map: dict[int, set[int]],
    threshold: float,
) -> tuple[list[tuple[int, int]], int, int]:
    """``(cluster_id, face_id)`` updates for the faces, with how many
    joined an existing cluster and how many opened a new one.

    Vectorizes the N-face x M-cluster nearest-centroid scan: one (N, M)
    distance matrix instead of N*M np.linalg.norm calls.
    """
    ambiguity_radius = threshold * 1.5
    max_cid = max(centroids.keys())
    cluster_ids = list(centroids.keys())
    centroids_mat = np.stack([centroids[c] for c in cluster_ids])
    cid_to_col = {c: i for i, c in enumerate(cluster_ids)}
    # ||a-b||^2 = ||a||^2 + ||b||^2 - 2 a·b; clip to 0 before sqrt
    # to absorb tiny negative values from float roundoff. The faces go
    # through in blocks so the distance product stays within
    # NEAREST_BLOCK_BYTES (an N x M matrix at 100k x 10k would be 4 GB).
    b2 = (centroids_mat * centroids_mat).sum(axis=1)
    n_faces = len(face_ids)
    best_cols = np.empty(n_faces, dtype=np.int64)
    best_dists = np.empty(n_faces, dtype=np.float64)
    # The hard-negative check needs, per face, only the distances to the
    # partners of its own best cluster: gathered per block as they are
    # computed, never as another N x M matrix.
    neg_dists: dict[int, dict[int, float]] = {}
    block = max(1, NEAREST_BLOCK_BYTES // max(1, len(cluster_ids) * 8))
    for start in range(0, n_faces, block):
        chunk = embs_mat[start : start + block]
        a2 = (chunk * chunk).sum(axis=1)
        dist2 = a2[:, None] + b2[None, :] - 2.0 * (chunk @ centroids_mat.T)
        np.maximum(dist2, 0, out=dist2)
        np.sqrt(dist2, out=dist2)
        cols = dist2.argmin(axis=1)
        best_cols[start : start + len(chunk)] = cols
        best_dists[start : start + len(chunk)] = dist2[np.arange(len(chunk)), cols]
        for r, col in enumerate(cols):
            partners = hard_neg_map.get(cluster_ids[int(col)])
            if partners:
                neg_dists[start + r] = {
                    p: float(dist2[r, cid_to_col[p]]) for p in partners if p in cid_to_col
                }
        del dist2

    updates: list[tuple[int, int]] = []
    assigned_count = 0
    new_count = 0
    for i, face_id in enumerate(face_ids):
        best_cid = cluster_ids[int(best_cols[i])]
        best_dist = float(best_dists[i])
        assigned = False
        if best_dist <= threshold:
            # Hard-negative check: if the best cluster has a known
            # confusable partner and this face is close to both,
            # create a new cluster instead of guessing wrong.
            skip = False
            for neg_dist in neg_dists.get(i, {}).values():
                if neg_dist < ambiguity_radius:
                    skip = True
                    break
            if not skip:
                updates.append((best_cid, face_id))
                assigned_count += 1
                assigned = True
        if not assigned:
            max_cid += 1
            updates.append((max_cid, face_id))
            new_count += 1
    return updates, assigned_count, new_count


# /api/v1/faces/recluster is registered by bpp.web.bp_faces_recluster
# (same blueprint, separate module for the 500-LOC cap).
# Importing the module is what runs the @bp.post decorator.
from bpp.web import bp_faces_recluster  # noqa: E402, F401
