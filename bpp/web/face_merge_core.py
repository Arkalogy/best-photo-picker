"""Shared face-cluster merge core.

One implementation of "merge cluster(s) B into cluster A", used by:

  * ``POST /api/v1/faces/merge``                 (explicit merge UI)
  * ``POST /api/v1/faces/review-pairs/verdict``  ("Same person" verdict —
    since the pair-review fix, answering "same" actually merges; the
    record-only behavior left users staring at two copies of the person
    they just identified)

The core records pre-merge centroid distances as adaptive-threshold
feedback, clears any hard-negative between the clusters, cascades the
cluster_id change through ``photo_person_tags``, propagates identity,
prunes obsolete person albums, and refreshes the affected smart albums.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from bpp.db.albums import list_albums as db_list_albums
from bpp.db.face_cluster_ops import (
    cleanup_person_albums,
    propagate_identity_on_merge,
)
from bpp.db.face_feedback import remove_hard_negative, store_face_feedback
from bpp.db.face_queries import assert_face_load_cap
from bpp.db.smart_albums import get_affected_album_types, refresh_smart_albums
from bpp.errors import BppError
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def assert_merge_within_cap(conn: sqlite3.Connection, cluster_ids: list[int]) -> None:
    """Raise FaceEmbeddingsTooLarge before the np.stack centroid work.

    Merging stacks every face embedding in the involved clusters into
    memory; refuse early so a merge of very large groups on a huge
    library surfaces a structured error instead of an OOM-kill.
    """
    placeholders = ",".join("?" for _ in cluster_ids)
    count = conn.execute(
        f"SELECT COUNT(*) FROM face_embeddings WHERE cluster_id IN ({placeholders})",
        cluster_ids,
    ).fetchone()[0]
    assert_face_load_cap(conn, count)


def _record_merge_feedback(
    conn: sqlite3.Connection, primary_id: int, ids_to_merge: list[int]
) -> None:
    """Best-effort: pre-merge centroid distances feed the adaptive threshold."""
    import numpy as np

    primary_embs = conn.execute(
        "SELECT embedding FROM face_embeddings WHERE cluster_id=?",
        (primary_id,),
    ).fetchall()
    if not primary_embs:
        return
    primary_centroid = np.stack(
        [np.frombuffer(r["embedding"], dtype=np.float32) for r in primary_embs]
    ).mean(axis=0)
    # Batch-fetch embeddings for all to-merge clusters in one query.
    placeholders = ",".join("?" for _ in ids_to_merge)
    merge_rows = conn.execute(
        f"SELECT cluster_id, embedding FROM face_embeddings WHERE cluster_id IN ({placeholders})",
        list(ids_to_merge),
    ).fetchall()
    from collections import defaultdict

    embs_by_cid: dict[int, list[bytes]] = defaultdict(list)
    for r in merge_rows:
        embs_by_cid[r["cluster_id"]].append(r["embedding"])
    for cid in ids_to_merge:
        merge_embs = embs_by_cid.get(cid, [])
        if merge_embs:
            merge_centroid = np.stack(
                [np.frombuffer(emb, dtype=np.float32) for emb in merge_embs]
            ).mean(axis=0)
            dist = float(np.linalg.norm(primary_centroid - merge_centroid))
            store_face_feedback(
                conn,
                "merge",
                cluster_id_a=primary_id,
                cluster_id_b=cid,
                distance=dist,
            )
        # Merge overrides any hard negative between these clusters
        remove_hard_negative(conn, primary_id, cid)


def perform_face_merge(
    conn: sqlite3.Connection, primary_id: int, ids_to_merge: list[int]
) -> dict[str, Any]:
    """Merge ``ids_to_merge`` into ``primary_id``. Returns {"albums", "warning"?}.

    Caller is responsible for the cap check (``assert_merge_within_cap``)
    so it can map FaceEmbeddingsTooLarge to its own response shape.
    """
    try:
        _record_merge_feedback(conn, primary_id, ids_to_merge)
    except Exception:
        # Feedback is best-effort — the merge itself proceeds even when
        # the adaptive-threshold input drops. Log at ERROR so the loss
        # is visible in /api/logs.
        log.error(
            "Merge feedback dropped for primary=%s merging=%s — "
            "adaptive threshold may not learn from this merge",
            primary_id,
            ids_to_merge,
            exc_info=True,
        )

    try:
        placeholders = ", ".join(["?"] * len(ids_to_merge))
        conn.execute(
            f"UPDATE face_embeddings SET cluster_id=? WHERE cluster_id IN ({placeholders})",
            [primary_id, *ids_to_merge],
        )
        # Cascade to photo_person_tags: remap merged cluster IDs -> primary
        conn.execute(
            f"UPDATE OR IGNORE photo_person_tags SET cluster_id=? "
            f"WHERE cluster_id IN ({placeholders})",
            [primary_id, *ids_to_merge],
        )
        # Delete leftovers (if primary tag already existed for that photo)
        conn.execute(
            f"DELETE FROM photo_person_tags WHERE cluster_id IN ({placeholders})",
            ids_to_merge,
        )
        # Propagate identity label from primary cluster to merged faces
        propagate_identity_on_merge(conn, primary_id)
        conn.commit()
    except Exception as e:
        raise BppError(
            "Database error during merge",
            user_message="Database error during merge",
            diagnostic_message=f"face_embeddings merge UPDATE failed: {e!s}",
        ) from e

    cleanup_warning = None
    try:
        cleanup_person_albums(conn, ids_to_merge)
    except Exception:
        log.warning("Failed to clean up person albums after merge", exc_info=True)
        cleanup_warning = (
            "Merge succeeded but album cleanup failed — some person albums may be stale"
        )

    # NOTE: propagation removed — it caused cascading merges that collapsed
    # 90+ clusters into 4 mega-clusters during review. Merge must only move
    # the specific faces requested, nothing else.

    refresh_smart_albums(conn, kinds=get_affected_album_types("face_tag"))
    albums = db_list_albums(conn)

    result: dict[str, Any] = {"albums": albums}
    if cleanup_warning:
        result["warning"] = cleanup_warning
    return result
