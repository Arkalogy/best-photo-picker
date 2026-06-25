"""``/api/v1/faces/recluster`` endpoint — re-run face clustering
with a new threshold over the existing embeddings.

Extracted from :mod:`bpp.web.bp_faces_cluster_ops` as part of the
500-LOC cap enforcement. Recluster is a categorically different
operation from the other cluster-mutation endpoints: those (merge,
dismiss, split, restore) mutate individual cluster assignments based
on user clicks, while recluster re-runs the entire bottom-up
clustering algorithm at a new distance threshold. Putting it in its
own module makes that boundary obvious.

The endpoint attaches to the same ``faces_cluster_ops`` blueprint so
it shows up under the same URL prefix and registration call site as
the rest of the cluster ops — there's no second blueprint to wire up.
"""

from __future__ import annotations

import numpy as np
from flask import Response, jsonify, request

from bpp.constants import CLUSTER_DISMISSED
from bpp.db.smart_albums import get_affected_album_types, refresh_smart_albums
from bpp.errors import ConflictError, ValidationError
from bpp.scoring.face_cluster import cluster_faces
from bpp.utils.logging import get_logger
from bpp.web.bp_faces_cluster_ops import bp
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx, with_face_lock

log = get_logger(__name__)


@bp.post("/api/v1/faces/recluster")
@requires_local_app
@with_face_lock
def api_faces_recluster() -> tuple[Response, int]:
    """Re-run face clustering with a new threshold (no re-extraction needed).

    Preserves named clusters by snapshotting old cluster→photo mappings,
    re-clustering, then remapping names to the new cluster IDs.
    """
    from bpp.web.face_worker import _remap_names_and_tags, _snapshot_cluster_photos

    ctx = get_ctx()
    if ctx.face_worker.is_alive:
        raise ConflictError(
            "Cannot recluster while face extraction is running",
            blocker="face_extraction",
        )
    data = request.get_json(silent=True) or {}
    threshold = data.get("threshold")
    if threshold is None:
        raise ValidationError("threshold required", field="threshold")
    try:
        threshold = float(threshold)
    except (TypeError, ValueError) as e:
        raise ValidationError(
            "threshold must be a number",
            field="threshold",
            value=threshold,
        ) from e
    if not 0.3 <= threshold <= 1.2:
        raise ValidationError(
            "threshold must be between 0.3 and 1.2",
            field="threshold",
            value=threshold,
            min=0.3,
            max=1.2,
        )

    conn = ctx.get_conn()

    # Snapshot old cluster→photo mappings BEFORE re-clustering
    old_cluster_photos = _snapshot_cluster_photos(conn)

    # Load all non-dismissed embeddings (ORDER BY ensures deterministic cluster IDs)
    rows = conn.execute(
        "SELECT fe.id, fe.photo_id, fe.face_index, fe.embedding "
        f"FROM face_embeddings fe WHERE fe.cluster_id != {CLUSTER_DISMISSED} "
        "ORDER BY fe.photo_id, fe.face_index"
    ).fetchall()

    if not rows:
        return jsonify({"status": "reclustered", "clusters": 0}), 200

    fe_ids = [r[0] for r in rows]
    embeddings = [np.frombuffer(r[3], dtype=np.float32) for r in rows]

    labels = cluster_faces(embeddings, threshold=threshold)

    conn.executemany(
        "UPDATE face_embeddings SET cluster_id=? WHERE id=?",
        zip(labels, fe_ids, strict=True),
    )
    conn.commit()

    # Remap named albums and person tags from old cluster IDs to new ones
    new_cluster_photos = _snapshot_cluster_photos(conn)
    _remap_names_and_tags(conn, old_cluster_photos, new_cluster_photos)

    # Remove orphaned person tags (clusters that vanished entirely)
    conn.execute(
        "DELETE FROM photo_person_tags WHERE cluster_id NOT IN "
        "(SELECT DISTINCT cluster_id FROM face_embeddings WHERE cluster_id >= 0)"
    )
    conn.commit()

    # Update config so future extractions use this threshold
    ctx.config["face_cluster_threshold"] = threshold

    # Refresh smart albums for the new clusters
    refresh_smart_albums(conn, kinds=get_affected_album_types("face_tag"))

    n_clusters = len(set(labels))
    return jsonify({"status": "reclustered", "clusters": n_clusters}), 200
