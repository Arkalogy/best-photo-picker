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

from bpp.constants import CLUSTER_UNASSIGNED
from bpp.scoring.face_threshold import face_threshold
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
    SET cluster_id`` for newly-assigned faces; with ``post_cluster_dedup``,
    two faces of one photo in one cluster are parted or, when their boxes
    overlap, the double detection is dropped (``face_same_photo``).

    The threshold is the active embedder's (``face_threshold``); feedback
    may only make it stricter. Hard-negative pairs gate sibling/lookalike
    merges.

    ``assign_new_faces_fn`` is injected so the phase file doesn't pull
    the clustering implementation at module-load time.
    """
    if not all_records:
        return 0

    all_records.sort(key=lambda r: (r[1], r[2]))
    config_threshold = face_threshold(conn)

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
    # The run arrives in chunks of photos; this chunk's records alone would
    # not know the clusters earlier chunks (or earlier runs) made, so new
    # faces would restart at id 0 and merge different people. Every
    # existing cluster is represented by its best few faces library-wide
    # (the chunk's own assigned rows are among them, so they are not
    # counted a second time), and the next free id comes from the table.
    floor: int | None = None
    if unassigned:
        dim = len(unassigned[0][3])
        assigned = _library_cluster_samples(conn, dim)
        floor = _highest_cluster_id(conn)

    if unassigned:
        new_labels = assign_new_faces_fn(
            assigned,
            unassigned,
            threshold,
            hard_negatives=hard_neg_map if hard_neg_map else None,
            next_id_floor=floor,
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

    if post_cluster_dedup:
        from bpp.db.face_same_photo import part_same_photo_faces

        part_same_photo_faces(conn, {photo_id for _fp, photo_id, _fi, _emb in all_records})

    row = conn.execute(
        "SELECT COUNT(DISTINCT cluster_id) FROM face_embeddings WHERE cluster_id >= 0"
    ).fetchone()
    return row[0] if row else 0


#: Best faces per existing cluster that new faces are compared with (mean
#: distance, as Regroup's linkage). Measured on labelled photos: 3 groups
#: as well as using every member, at a fraction of the reads.
CLUSTER_SAMPLE_FACES = 3


def _library_cluster_samples(
    conn: sqlite3.Connection, dim: int
) -> list[tuple[str, int, int, np.ndarray, int, int, float | None]]:
    """``assigned`` records for every assigned cluster in the library: its
    ``CLUSTER_SAMPLE_FACES`` best faces, ordered by cluster. Rows of
    another embedding size are left out before the ranking, damaged ones
    after. The caller reserves ids from the table, so a cluster with no
    usable sample is still never reused. Memory: a few blobs per cluster,
    read once."""
    # Rank on the narrow columns only, then join back for the picked rows'
    # blobs: the sort never carries every embedding, and one statement
    # reads one snapshot.
    rows = conn.execute(
        "SELECT fe.cluster_id, fe.embedding, fe.quality FROM ("
        "  SELECT id, ROW_NUMBER() OVER (PARTITION BY cluster_id"
        "                                ORDER BY quality DESC, id) AS rn"
        "  FROM face_embeddings WHERE cluster_id >= 0 AND length(embedding) = ?"
        ") r JOIN face_embeddings fe ON fe.id = r.id WHERE r.rn <= ?",
        (dim * 4, CLUSTER_SAMPLE_FACES),
    ).fetchall()
    samples = []
    for cid, blob, quality in rows:
        emb = np.frombuffer(blob, dtype=np.float32)
        if not np.isfinite(emb).all():
            continue  # a damaged row must not skew its cluster's distances
        samples.append(("", -1, -1, emb, int(cid), 0, quality))
    samples.sort(key=lambda rec: rec[4])
    return samples


from bpp.db.face_cluster_ids import highest_cluster_id as _highest_cluster_id  # noqa: E402
