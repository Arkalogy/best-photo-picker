"""Faces management blueprint: merge, dismiss, recluster, tag, reassign, dedup."""

from __future__ import annotations

import numpy as np
from flask import Blueprint, Response, jsonify, request

from bpp.constants import (
    ACTIVE_PHOTO_SQL,
    CLUSTER_DISMISSED,
)
from bpp.db.albums import (
    list_albums as db_list_albums,
)
from bpp.db.face_feedback import (
    store_face_feedback,
    store_hard_negative,
)
from bpp.db.photos import get_photo_id_by_path
from bpp.db.smart_albums import get_affected_album_types, refresh_smart_albums
from bpp.errors import BppError, NotFoundError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx, with_face_lock

log = get_logger(__name__)

bp = Blueprint("faces_manage", __name__)

_ACTIVE = ACTIVE_PHOTO_SQL


# Implementation lives in bpp.db.face_cluster_ops. The blueprint
# consumes those helpers under their leading-underscore aliases.
from bpp.db.face_cluster_ops import (  # noqa: E402
    sync_face_count as _sync_face_count,
)
from bpp.db.face_cluster_ops import (  # noqa: E402, F401
    sync_face_counts_for_photos as _sync_face_counts_for_photos,
)


@bp.post("/api/v1/faces/tag")
@requires_local_app
@with_face_lock
def api_tag_person() -> tuple[Response, int]:
    """Manually tag a photo with a person (cluster)."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    path_hash = data.get("path_hash")
    cluster_id = data.get("cluster_id")
    if not path_hash or cluster_id is None:
        raise ValidationError("path_hash and cluster_id required")
    if not isinstance(cluster_id, int) or cluster_id < 0:
        raise ValidationError(
            "cluster_id must be a non-negative integer",
            field="cluster_id",
            value=cluster_id,
        )

    if ctx.thumbs is None:
        raise NotFoundError("No thumbnails loaded")
    filepath = ctx.thumbs.get_filepath(path_hash)
    if not filepath:
        raise NotFoundError("Unknown image", path_hash=path_hash)

    conn = ctx.get_conn()
    photo_id = get_photo_id_by_path(conn, filepath)
    if photo_id is None:
        raise NotFoundError("Photo not in database", filepath=filepath)

    try:
        conn.execute(
            "INSERT OR IGNORE INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
            (photo_id, cluster_id),
        )
        conn.commit()
    except Exception as e:
        raise BppError(
            "Database error",
            user_message="Database error",
            diagnostic_message=f"photo_person_tags insert failed: {e!s}",
            photo_id=photo_id,
            cluster_id=cluster_id,
        ) from e

    return jsonify({"status": "tagged"}), 200


@bp.delete("/api/v1/faces/tag")
@requires_local_app
@with_face_lock
def api_untag_person() -> tuple[Response, int]:
    """Remove a manual person tag from a photo."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    path_hash = data.get("path_hash")
    cluster_id = data.get("cluster_id")
    if not path_hash or cluster_id is None:
        raise ValidationError("path_hash and cluster_id required")

    if ctx.thumbs is None:
        raise NotFoundError("No thumbnails loaded")
    filepath = ctx.thumbs.get_filepath(path_hash)
    if not filepath:
        raise NotFoundError("Unknown image", path_hash=path_hash)

    conn = ctx.get_conn()
    photo_id = get_photo_id_by_path(conn, filepath)
    if photo_id is None:
        raise NotFoundError("Photo not in database", filepath=filepath)

    conn.execute(
        "DELETE FROM photo_person_tags WHERE photo_id=? AND cluster_id=?",
        (photo_id, cluster_id),
    )
    conn.commit()
    return jsonify({"status": "untagged"}), 200


@bp.post("/api/v1/faces/reassign")
@requires_local_app
@with_face_lock
def api_faces_reassign() -> tuple[Response, int]:
    """Reassign a specific face embedding to a different cluster (person)."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    face_id = data.get("face_id")
    cluster_id = data.get("cluster_id")
    if face_id is None or cluster_id is None:
        raise ValidationError("face_id and cluster_id required")
    if not isinstance(cluster_id, int) or (cluster_id < 0 and cluster_id != CLUSTER_DISMISSED):
        raise ValidationError(
            f"cluster_id must be >= 0 or {CLUSTER_DISMISSED} (not a face)",
            field="cluster_id",
            value=cluster_id,
        )

    conn = ctx.get_conn()
    row = conn.execute(
        "SELECT id, cluster_id FROM face_embeddings WHERE id=?", (face_id,)
    ).fetchone()
    if not row:
        raise NotFoundError("Face not found", face_id=face_id)

    old_cluster_id = row["cluster_id"]

    # Record feedback: distance from face to old and new cluster centroids
    try:
        face_row = conn.execute(
            "SELECT embedding FROM face_embeddings WHERE id=?", (face_id,)
        ).fetchone()
        if face_row and face_row["embedding"]:
            face_emb = np.frombuffer(face_row["embedding"], dtype=np.float32)
            # One round trip covers both centroids — was two separate
            # SELECTs that fetched roughly the same data shape from the
            # same table back-to-back. On heavy reassign sessions this
            # halves the per-action query count.
            target_cids = [c for c in (old_cluster_id, cluster_id) if c >= 0]
            rows_by_cid: dict[int, list[bytes]] = {c: [] for c in target_cids}
            if target_cids:
                # IN (?, ?) handles dup values (when old == new) without
                # double-counting because each row carries its own
                # cluster_id; the comprehensions below partition cleanly.
                placeholders = ",".join(["?"] * len(target_cids))
                fb_rows = conn.execute(
                    f"SELECT cluster_id, embedding FROM face_embeddings "
                    f"WHERE cluster_id IN ({placeholders}) AND id != ?",
                    (*target_cids, face_id),
                ).fetchall()
                for r in fb_rows:
                    if r["cluster_id"] in rows_by_cid:
                        rows_by_cid[r["cluster_id"]].append(r["embedding"])

            # "different" signal: face didn't belong in old cluster
            if old_cluster_id >= 0 and rows_by_cid.get(old_cluster_id):
                old_centroid = np.stack(
                    [np.frombuffer(b, dtype=np.float32) for b in rows_by_cid[old_cluster_id]]
                ).mean(axis=0)
                dist_old = float(np.linalg.norm(face_emb - old_centroid))
                store_face_feedback(
                    conn,
                    "reassign_out",
                    cluster_id_a=old_cluster_id,
                    distance=dist_old,
                )
            # "same" signal: face belongs in new cluster
            if cluster_id >= 0 and rows_by_cid.get(cluster_id):
                new_centroid = np.stack(
                    [np.frombuffer(b, dtype=np.float32) for b in rows_by_cid[cluster_id]]
                ).mean(axis=0)
                dist_new = float(np.linalg.norm(face_emb - new_centroid))
                store_face_feedback(
                    conn,
                    "reassign_in",
                    cluster_id_a=cluster_id,
                    distance=dist_new,
                )
            # Hard negative: old and new clusters are different people
            if old_cluster_id >= 0 and cluster_id >= 0 and old_cluster_id != cluster_id:
                store_hard_negative(conn, old_cluster_id, cluster_id)
    except Exception:
        log.warning("Failed to record reassign feedback", exc_info=True)

    conn.execute("UPDATE face_embeddings SET cluster_id=? WHERE id=?", (cluster_id, face_id))
    conn.commit()

    # Propagate: find similar faces and absorb them
    # NOTE: propagation removed — same cascading-merge risk as the merge endpoint.
    # Reassign must only move the single face requested.

    # Update face_count on affected photo(s) so smart albums reflect the change
    _sync_face_count(conn, face_id)

    refresh_smart_albums(conn, kinds=get_affected_album_types("face_tag"))
    albums = db_list_albums(conn)
    return jsonify({"status": "reassigned", "albums": albums}), 200


# Feedback / adaptive threshold routes live in bpp.web.bp_faces_review.


@bp.delete("/api/v1/faces/purge")
@requires_local_app
@with_face_lock
def api_faces_purge() -> tuple[Response, int]:
    """Permanently delete dismissed face detections from the database."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    conn = ctx.get_conn()

    face_ids = data.get("face_ids")
    purge_all = data.get("all", False)

    if purge_all:
        row = conn.execute(
            f"SELECT COUNT(*) FROM face_embeddings WHERE cluster_id = {CLUSTER_DISMISSED}"
        ).fetchone()
        count = row[0] if row else 0
        if count == 0:
            return jsonify({"deleted": 0}), 200
        conn.execute(f"DELETE FROM face_embeddings WHERE cluster_id = {CLUSTER_DISMISSED}")
        conn.commit()
        return jsonify({"deleted": count}), 200

    if not face_ids or not isinstance(face_ids, list):
        raise ValidationError("Provide face_ids or all:true", field="face_ids")

    placeholders = ",".join("?" for _ in face_ids)
    cur = conn.execute(
        f"DELETE FROM face_embeddings"
        f" WHERE id IN ({placeholders})"
        f" AND cluster_id = {CLUSTER_DISMISSED}",
        face_ids,
    )
    deleted = cur.rowcount
    conn.commit()
    return jsonify({"deleted": deleted}), 200


# Cluster-operation endpoints (merge / dismiss / split / restore /
# recluster) live in bpp.web.bp_faces_cluster_ops as their own Flask
# blueprint. ``app.py`` registers it explicitly — no side-effect import
# here.
