"""Faces review blueprint: adaptive threshold + ambiguous-pair review.

A "same" verdict MERGES the pair (via the shared merge core in
``bpp.web.face_merge_core``) and returns an undo snapshot; the undo
endpoint reverses the merge from that snapshot. "Different" records a
hard negative only. The other cluster operations (explicit merge /
dismiss / split / recluster / tag / reassign) live in
bp_faces_cluster_ops / bp_faces_manage.

Endpoints:
  GET  /api/faces/threshold                   — adaptive threshold + metadata
  GET  /api/faces/review-pairs/count          — cheap count of ambiguous pairs
  GET  /api/faces/review-pairs/next           — enriched list of pairs to review
  POST /api/faces/review-pairs/verdict        — same (merges) / different verdict
  POST /api/faces/review-pairs/verdict/undo   — reverse the last verdict
  GET  /api/faces/feedback/stats              — feedback corpus + recluster nudge
"""

from __future__ import annotations

from typing import Any

import numpy as np
from flask import Blueprint, Response, jsonify, request

from bpp.constants import FACE_CLUSTER_THRESHOLD_FALLBACK
from bpp.db.face_feedback import (
    compute_adaptive_face_threshold,
    count_ambiguous_pairs,
    find_ambiguous_pairs,
    store_hard_negative,
    undo_hard_negative,
    undo_last_pair_feedback,
)
from bpp.errors import NotFoundError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.review_meta import attach_photo_meta, photo_meta_by_filepaths
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx, with_face_lock

log = get_logger(__name__)

bp = Blueprint("faces_review", __name__)


@bp.get("/api/v1/faces/threshold")
def api_faces_threshold() -> tuple[Response, int]:
    """Return the current adaptive face clustering threshold and metadata."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    config_threshold = ctx.config.get("face_cluster_threshold", FACE_CLUSTER_THRESHOLD_FALLBACK)
    threshold, info = compute_adaptive_face_threshold(conn, default=config_threshold)
    return jsonify(
        {
            "threshold": threshold,
            "config_threshold": config_threshold,
            **info,
        }
    ), 200


@bp.get("/api/v1/faces/review-pairs/count")
@with_face_lock
def api_faces_review_pairs_count() -> tuple[Response, int]:
    """Return just the count of reviewable ambiguous pairs.

    Cheap UI-gating endpoint — lets the client enable/disable the
    "Review pairs" button without fetching and enriching full metadata.
    """
    ctx = get_ctx()
    conn = ctx.get_conn()
    return jsonify({"count": count_ambiguous_pairs(conn)}), 200


@bp.get("/api/v1/faces/review-pairs/next")
@with_face_lock
def api_faces_review_pairs_next() -> tuple[Response, int]:
    """Return ambiguous cluster pairs for the "Same person?" review flow.

    Pairs are ranked by centroid distance ascending (most-likely-same
    first). Each pair is enriched with cluster metadata (name, face
    count) and a representative face (thumb_hash + face_index) for
    rendering crops.
    """
    from bpp.web.face_worker import load_face_clusters

    ctx = get_ctx()
    conn = ctx.get_conn()

    limit_arg = request.args.get("limit", "20")
    try:
        limit = max(1, min(100, int(limit_arg)))
    except ValueError:
        limit = 20

    config_threshold = ctx.config.get("face_cluster_threshold", FACE_CLUSTER_THRESHOLD_FALLBACK)
    threshold, _ = compute_adaptive_face_threshold(conn, default=config_threshold)

    # Fetch all pairs to know the true total, then slice for the response
    all_pairs = find_ambiguous_pairs(conn)
    total = len(all_pairs)
    raw_pairs = all_pairs[:limit]
    if not raw_pairs:
        return jsonify({"threshold": threshold, "total": total, "pairs": []}), 200

    # Build cluster metadata lookup (name from smart_person album, rep face)
    clusters = load_face_clusters(conn)
    by_cid: dict[int, dict[str, Any]] = {c["cluster_id"]: c for c in clusters}

    # Map cluster_id → user name (from smart_person albums).
    # P5b: indexed shadow-column lookup.
    from bpp.db.albums import get_smart_person_cluster_name_map

    names_by_cid = get_smart_person_cluster_name_map(conn)

    def _cluster_info(cid: int) -> dict[str, Any] | None:
        c = by_cid.get(cid)
        if not c:
            return None
        rep = c["representative"]
        thumb_hash = ctx.thumbs.get_hash(rep["filepath"]) if ctx.thumbs else ""
        return {
            "id": cid,
            # +1 matches the auto person-album naming everywhere else
            # (smart_album_people.py) — "Person 78" is cluster 77 on
            # every surface.
            "name": names_by_cid.get(cid, f"Person {cid + 1}"),
            "face_count": c["face_count"],
            "photo_count": c["photo_count"],
            "representative": {
                "thumb_hash": thumb_hash,
                "face_index": rep["face_index"],
                "filepath": rep.get("filepath", ""),
            },
        }

    pairs: list[dict[str, Any]] = []
    for p in raw_pairs:
        a = _cluster_info(p["cluster_a"])
        b = _cluster_info(p["cluster_b"])
        if a is None or b is None:
            # Cluster was deleted or has no active faces — skip silently
            continue
        pairs.append(
            {
                "cluster_a": a,
                "cluster_b": b,
                "distance": round(p["distance"], 4),
            }
        )

    # Attach each representative's source-photo metadata (filename /
    # timestamp / score) so the user can judge a tight crop in context.
    meta_fps = [
        side["representative"]["filepath"]
        for pr in pairs
        for side in (pr["cluster_a"], pr["cluster_b"])
    ]
    meta = photo_meta_by_filepaths(conn, meta_fps)
    for pr in pairs:
        attach_photo_meta(pr["cluster_a"]["representative"], meta)
        attach_photo_meta(pr["cluster_b"]["representative"], meta)

    return jsonify({"threshold": threshold, "total": total, "pairs": pairs}), 200


@bp.post("/api/v1/faces/review-pairs/verdict")
@requires_local_app
@with_face_lock
def api_faces_review_pairs_verdict() -> tuple[Response, int]:
    """Record the user's verdict on an ambiguous cluster pair.

    Body: ``{"cluster_a": int, "cluster_b": int, "verdict": "same" | "different"}``

    - "same"      → MERGES the pair (named/larger cluster wins as primary),
                    records merge feedback for the adaptive threshold, and
                    returns an ``undo`` snapshot the client round-trips to
                    /verdict/undo to reverse the merge.
    - "different" → ``store_hard_negative(a, b)``
                    (removes the pair from future /review-pairs/next results)

    Distance is recomputed server-side from live centroids — client-supplied
    distance is ignored for integrity.
    """
    data = request.get_json(silent=True) or {}
    cluster_a = data.get("cluster_a")
    cluster_b = data.get("cluster_b")
    verdict = data.get("verdict")

    if not isinstance(cluster_a, int) or not isinstance(cluster_b, int):
        raise ValidationError("cluster_a and cluster_b must be integers")
    if cluster_a < 0 or cluster_b < 0 or cluster_a == cluster_b:
        raise ValidationError(
            "cluster IDs must be non-negative and distinct",
            cluster_a=cluster_a,
            cluster_b=cluster_b,
        )
    if verdict not in ("same", "different"):
        raise ValidationError(
            "verdict must be 'same' or 'different'",
            field="verdict",
            value=verdict,
        )

    ctx = get_ctx()
    conn = ctx.get_conn()

    # Load embeddings for both clusters — both must exist with at least one face
    rows = conn.execute(
        "SELECT cluster_id, embedding FROM face_embeddings WHERE cluster_id IN (?, ?)",
        (cluster_a, cluster_b),
    ).fetchall()
    by_cid: dict[int, list[np.ndarray]] = {}
    for r in rows:
        cid = r["cluster_id"]
        by_cid.setdefault(int(cid), []).append(np.frombuffer(r["embedding"], dtype=np.float32))
    if cluster_a not in by_cid or cluster_b not in by_cid:
        raise NotFoundError(
            "cluster no longer exists",
            cluster_a=cluster_a,
            cluster_b=cluster_b,
        )

    # Recompute centroid distance for the feedback record
    centroid_a = np.mean(by_cid[cluster_a], axis=0)
    centroid_b = np.mean(by_cid[cluster_b], axis=0)
    distance = float(np.linalg.norm(centroid_a - centroid_b))

    if verdict == "same":
        # "Same person" MERGES the clusters. The record-only v1 behavior
        # confused users: they answered "same" and still saw two copies of
        # the person. The merge core also records the feedback row and
        # clears any stale hard negative between the pair.
        from bpp.db.albums import get_smart_person_cluster_name_map
        from bpp.db.face_queries import FaceEmbeddingsTooLarge
        from bpp.web.face_merge_core import assert_merge_within_cap, perform_face_merge

        names = get_smart_person_cluster_name_map(conn)
        # Primary = the named cluster when exactly one is named (a name is
        # user investment); otherwise the one with more faces; tie → lower id.
        a_named, b_named = cluster_a in names, cluster_b in names
        if a_named != b_named:
            primary = cluster_a if a_named else cluster_b
        elif len(by_cid[cluster_a]) != len(by_cid[cluster_b]):
            primary = cluster_a if len(by_cid[cluster_a]) > len(by_cid[cluster_b]) else cluster_b
        else:
            primary = min(cluster_a, cluster_b)
        absorbed = cluster_b if primary == cluster_a else cluster_a

        try:
            assert_merge_within_cap(conn, [primary, absorbed])
        except FaceEmbeddingsTooLarge as e:
            log.warning("Refusing verdict merge: %s", e)
            return jsonify(
                {
                    "error": str(e),
                    "code": "face_embeddings_too_large",
                    "count": e.count,
                    "cap": e.cap,
                }
            ), 503

        undo_state = _capture_merge_undo_state(conn, primary, absorbed, names.get(absorbed))
        result = perform_face_merge(conn, primary, [absorbed])
        log.info("Review-pairs verdict 'same': merged cluster %d into %d", absorbed, primary)
        payload: dict[str, Any] = {
            "status": "recorded",
            "verdict": "same",
            "merged": True,
            "primary_cluster_id": primary,
            "absorbed_cluster_id": absorbed,
            "distance": round(distance, 4),
            "albums": result["albums"],
            "undo": undo_state,
        }
        if "warning" in result:
            payload["warning"] = result["warning"]
        return jsonify(payload), 200

    store_hard_negative(conn, cluster_a, cluster_b)
    return jsonify(
        {
            "status": "recorded",
            "verdict": verdict,
            "distance": round(distance, 4),
        }
    ), 200


def _capture_merge_undo_state(
    conn: Any, primary: int, absorbed: int, absorbed_name: str | None
) -> dict[str, Any]:
    """Snapshot everything needed to reverse a verdict merge.

    Round-tripped through the client (the toast's Undo sends it back) so
    the server stays stateless. Faces carry their pre-merge identity —
    the merge's identity propagation fills NULLs, which a plain
    cluster_id flip wouldn't reverse."""
    from bpp.db.dialect import dialect

    has_identity = "identity" in dialect.column_names(conn, "face_embeddings")
    if has_identity:
        face_rows = conn.execute(
            "SELECT id, identity FROM face_embeddings WHERE cluster_id=?", (absorbed,)
        ).fetchall()
        faces = [[int(r[0]), r[1]] for r in face_rows]
    else:
        face_rows = conn.execute(
            "SELECT id FROM face_embeddings WHERE cluster_id=?", (absorbed,)
        ).fetchall()
        faces = [[int(r[0]), None] for r in face_rows]
    absorbed_tags = [
        int(r[0])
        for r in conn.execute(
            "SELECT photo_id FROM photo_person_tags WHERE cluster_id=?", (absorbed,)
        ).fetchall()
    ]
    primary_tags = [
        int(r[0])
        for r in conn.execute(
            "SELECT photo_id FROM photo_person_tags WHERE cluster_id=?", (primary,)
        ).fetchall()
    ]
    return {
        "primary_cluster_id": primary,
        "absorbed_cluster_id": absorbed,
        "absorbed_name": absorbed_name,
        "faces": faces,
        "absorbed_tagged_photo_ids": absorbed_tags,
        "primary_tagged_photo_ids": primary_tags,
    }


@bp.post("/api/v1/faces/review-pairs/verdict/undo")
@requires_local_app
@with_face_lock
def api_faces_review_pairs_verdict_undo() -> tuple[Response, int]:
    """Undo the most recent verdict on a cluster pair (the toast's Undo).

    Body mirrors the verdict call: ``{"cluster_a", "cluster_b", "verdict"}``,
    plus the ``undo`` snapshot the verdict response returned for "same".
    - "same"      → deletes the merge-feedback row AND reverses the merge
                    (faces + identities + person tags + album name restored
                    from the snapshot)
    - "different" → decrements the hard-negative (deletes at zero)
    """
    data = request.get_json(silent=True) or {}
    cluster_a = data.get("cluster_a")
    cluster_b = data.get("cluster_b")
    verdict = data.get("verdict")
    if not isinstance(cluster_a, int) or not isinstance(cluster_b, int):
        raise ValidationError("cluster_a and cluster_b must be integers")
    if verdict not in ("same", "different"):
        raise ValidationError("verdict must be 'same' or 'different'", field="verdict")
    ctx = get_ctx()
    conn = ctx.get_conn()
    if verdict == "same":
        undone = undo_last_pair_feedback(conn, cluster_a, cluster_b)
    else:
        undone = undo_hard_negative(conn, cluster_a, cluster_b)
    if not undone:
        raise NotFoundError(
            "No recorded verdict to undo for this pair",
            cluster_a=cluster_a,
            cluster_b=cluster_b,
        )
    log.info("Review-pairs undo: %s verdict on (%d, %d)", verdict, cluster_a, cluster_b)

    result: dict[str, Any] = {"undone": True}
    if verdict == "same" and isinstance(data.get("undo"), dict):
        result["albums"] = _restore_merged_cluster(conn, data["undo"])
    return jsonify(result), 200


def _restore_merged_cluster(conn: Any, undo: dict[str, Any]) -> list[dict[str, Any]]:
    """Reverse a verdict merge from its snapshot. Returns refreshed albums."""
    from bpp.db.albums import list_albums as db_list_albums
    from bpp.db.dialect import dialect
    from bpp.db.smart_albums import get_affected_album_types, refresh_smart_albums

    primary = undo.get("primary_cluster_id")
    absorbed = undo.get("absorbed_cluster_id")
    faces = undo.get("faces") or []
    if not isinstance(primary, int) or not isinstance(absorbed, int):
        raise ValidationError("undo snapshot missing primary/absorbed cluster ids")
    if not all(isinstance(f, list) and len(f) == 2 and isinstance(f[0], int) for f in faces):
        raise ValidationError("undo snapshot faces must be [id, identity] pairs")
    absorbed_tags = undo.get("absorbed_tagged_photo_ids") or []
    primary_tags = undo.get("primary_tagged_photo_ids") or []
    if not all(isinstance(p, int) for p in [*absorbed_tags, *primary_tags]):
        raise ValidationError("undo snapshot tag photo ids must be integers")
    absorbed_name = undo.get("absorbed_name")

    # Move the snapshot's faces back, restoring pre-merge identity. The
    # cluster_id guard skips faces the user reassigned since the merge.
    has_identity = "identity" in dialect.column_names(conn, "face_embeddings")
    if has_identity:
        conn.executemany(
            "UPDATE face_embeddings SET cluster_id=?, identity=? WHERE id=? AND cluster_id=?",
            [(absorbed, identity, fid, primary) for fid, identity in faces],
        )
    else:
        conn.executemany(
            "UPDATE face_embeddings SET cluster_id=? WHERE id=? AND cluster_id=?",
            [(absorbed, fid, primary) for fid, _identity in faces],
        )

    # Person tags: the merge remapped absorbed→primary and dropped dupes.
    # Re-create the absorbed rows; drop primary rows that only exist
    # because of the remap (photo wasn't tagged primary pre-merge).
    conn.executemany(
        "INSERT OR IGNORE INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
        [(pid, absorbed) for pid in absorbed_tags],
    )
    remapped_only = sorted(set(absorbed_tags) - set(primary_tags))
    if remapped_only:
        placeholders = ",".join("?" for _ in remapped_only)
        conn.execute(
            f"DELETE FROM photo_person_tags WHERE cluster_id=? AND photo_id IN ({placeholders})",
            [primary, *remapped_only],
        )
    # Re-create the absorbed cluster's person album with its user-given
    # name INSIDE the same transaction as the face/tag restore — a crash
    # between "faces restored" and "name restored" would otherwise let
    # the next refresh rename the cluster back to "Person N" and silently
    # lose the name. The refresh below preserves existing names for
    # user-renameable types and only syncs membership.
    if isinstance(absorbed_name, str) and absorbed_name:
        from bpp.db.albums import create_album

        existing = conn.execute(
            "SELECT id FROM albums WHERE album_type='smart_person' AND smart_person_cluster_id=?",
            (absorbed,),
        ).fetchone()
        if existing:
            conn.execute("UPDATE albums SET name=? WHERE id=?", (absorbed_name, existing[0]))
        else:
            create_album(
                conn,
                absorbed_name,
                album_type="smart_person",
                rule={"cluster_id": absorbed},
            )
    conn.commit()

    refresh_smart_albums(conn, kinds=get_affected_album_types("face_tag"))
    log.info(
        "Review-pairs undo: restored cluster %d (%d faces) out of %d",
        absorbed,
        len(faces),
        primary,
    )
    return db_list_albums(conn)


@bp.get("/api/v1/faces/feedback/stats")
def api_faces_feedback_stats() -> tuple[Response, int]:
    """Return feedback statistics and whether re-clustering is recommended."""
    from bpp.db.face_feedback import get_face_feedback, should_suggest_recluster

    ctx = get_ctx()
    conn = ctx.get_conn()
    config_threshold = ctx.config.get("face_cluster_threshold", FACE_CLUSTER_THRESHOLD_FALLBACK)
    threshold, info = compute_adaptive_face_threshold(conn, default=config_threshold)
    feedback = get_face_feedback(conn)
    nudge = should_suggest_recluster(conn, current_threshold=config_threshold)
    return jsonify(
        {
            "threshold": threshold,
            "config_threshold": config_threshold,
            "nudge_recluster": nudge,
            "corrections": len(feedback),
            **info,
        }
    ), 200
