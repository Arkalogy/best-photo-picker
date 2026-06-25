"""Phase 6 of the face-extraction pipeline — clustering pass.

Extracted from :mod:`bpp.web.face_extraction_phases` as part of the
500-LOC cap enforcement. Phase 6 runs after phase 5 has stabilized the
``face_embeddings`` table: it computes the adaptive cluster threshold
from per-library feedback, fetches hard-negative pairs, splits the
records into already-assigned vs. unassigned, runs the injected
``assign_new_faces_fn`` on the unassigned set, and persists the new
``cluster_id`` values.

face_extraction_phases re-exports :func:`cluster_faces` so existing
callers keep working via the original module path.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

import numpy as np

from bpp.constants import CLUSTER_UNASSIGNED, FACE_CLUSTER_THRESHOLD_FALLBACK
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def cluster_faces(
    conn: sqlite3.Connection,
    all_records: list[tuple[str, int, int, np.ndarray]],
    config: dict[str, Any],
    *,
    assign_new_faces_fn: Callable[..., list[int]],
    post_cluster_dedup: bool,
) -> int:
    """Phase 6: adaptive threshold + assign new faces + count clusters.

    Returns ``n_clusters``. Side effects: ``UPDATE face_embeddings
    SET cluster_id`` for newly-assigned faces; optional dedup DELETE.

    Adaptive threshold falls back to ``FACE_CLUSTER_THRESHOLD_FALLBACK``
    when no feedback data is available. Hard-negative pairs gate
    sibling/lookalike merges.

    ``assign_new_faces_fn`` is injected so the phase file doesn't pull
    the clustering implementation at module-load time.
    """
    if not all_records:
        return 0

    all_records.sort(key=lambda r: (r[1], r[2]))
    config_threshold = config.get("face_cluster_threshold", FACE_CLUSTER_THRESHOLD_FALLBACK)

    from bpp.db.face_feedback import (
        compute_adaptive_face_threshold,
        get_hard_negatives,
    )

    adaptive_threshold, adapt_info = compute_adaptive_face_threshold(conn, default=config_threshold)
    threshold = adaptive_threshold if adapt_info["source"] != "default" else config_threshold
    if adapt_info["source"] != "default":
        log.info(
            "Using adaptive face threshold %.3f (confidence %.0f%%, %s)",
            threshold,
            adapt_info["confidence"] * 100,
            adapt_info["source"],
        )

    neg_pairs = get_hard_negatives(conn)
    hard_neg_map: dict[int, set[int]] = {}
    for pair in neg_pairs:
        a, b = pair["cluster_id_a"], pair["cluster_id_b"]
        hard_neg_map.setdefault(a, set()).add(b)
        hard_neg_map.setdefault(b, set()).add(a)

    fe_rows = conn.execute(
        "SELECT photo_id, face_index, cluster_id, bbox_w, quality FROM face_embeddings"
    ).fetchall()
    fe_lookup: dict[tuple[int, int], tuple[int, int, int | None]] = {
        (r[0], r[1]): (r[2], r[3], r[4]) for r in fe_rows
    }

    assigned: list[tuple[str, int, int, np.ndarray, int, int, int | None]] = []
    unassigned: list[tuple[str, int, int, np.ndarray, int, int | None]] = []
    for fp, photo_id, fi, emb in all_records:
        info = fe_lookup.get((photo_id, fi))
        cid = info[0] if info else CLUSTER_UNASSIGNED
        bbox_w = info[1] if info else 0
        quality = info[2] if info else None
        if cid >= 0:
            assigned.append((fp, photo_id, fi, emb, cid, bbox_w, quality))
        else:
            unassigned.append((fp, photo_id, fi, emb, bbox_w, quality))

    if unassigned:
        new_labels = assign_new_faces_fn(
            assigned,
            unassigned,
            threshold,
            hard_negatives=hard_neg_map if hard_neg_map else None,
        )
        conn.executemany(
            "UPDATE face_embeddings SET cluster_id=? WHERE photo_id=? AND face_index=?",
            (
                (cid, photo_id, fi)
                for (_fp, photo_id, fi, _emb, _bw, _q), cid in zip(
                    unassigned, new_labels, strict=True
                )
            ),
        )
        conn.commit()
        log.info(
            "Incremental clustering: %d new faces assigned",
            len(unassigned),
        )

    row = conn.execute(
        "SELECT COUNT(DISTINCT cluster_id) FROM face_embeddings WHERE cluster_id >= 0"
    ).fetchone()
    n_clusters = row[0] if row else 0

    if post_cluster_dedup:
        dupes = conn.execute(
            "DELETE FROM face_embeddings "
            "WHERE cluster_id >= 0 "
            "  AND id NOT IN ("
            "    SELECT MIN(id) FROM face_embeddings "
            "    WHERE cluster_id >= 0 "
            "    GROUP BY photo_id, cluster_id"
            "  )"
        )
        if dupes.rowcount:
            log.info(
                "Removed %d duplicate face embeddings after clustering",
                dupes.rowcount,
            )
            conn.commit()

    return n_clusters
