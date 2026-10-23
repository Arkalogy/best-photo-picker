"""The steps of ``GET /api/v1/faces/review`` (bp_faces): which clusters
count as reviewed, cluster centroids, and the suggested match for an
unreviewed cluster. Split out for the 500-LOC cap."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

import numpy as np

from bpp.db.face_embedding_safety import decode_rows
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_PERSON_N_RE = re.compile(r"^Person \d+$")


def named_cluster_names(conn: sqlite3.Connection) -> dict[int, str]:
    """cluster_id -> user-chosen name, for every cluster whose
    smart_person album isn't still called "Person N".

    Indexed lookup via get_smart_person_cluster_name_map (uses the v36
    smart_person_cluster_id shadow column).
    """
    named_cids: dict[int, str] = {}
    try:
        from bpp.db.albums import get_smart_person_cluster_name_map

        for cid, name in get_smart_person_cluster_name_map(conn).items():
            if not _PERSON_N_RE.match(name):
                named_cids[cid] = name
    except Exception:
        # Don't fail the whole face-review endpoint on a named-cluster
        # query glitch — fall through with empty named_cids so the
        # response is still useful. But surface the failure so the
        # operator sees it — never swallow silently.
        log.warning(
            "Failed to load named smart_person albums for face review",
            exc_info=True,
        )
    return named_cids


def cluster_centroids(
    conn: sqlite3.Connection, cids: list[int], *, where: str
) -> dict[int, np.ndarray]:
    """Mean embedding per cluster, from the DB rows.

    Routes through decode_rows so a corrupt row can't crash the
    People-review screen the way the Jun-2 incident crashed
    /faces/clusters. Bad rows are skipped; the centroid is computed from
    whatever remains.
    """
    if not cids:
        return {}
    placeholders = ",".join(["?"] * len(cids))
    emb_rows = conn.execute(
        f"SELECT cluster_id, embedding FROM face_embeddings WHERE cluster_id IN ({placeholders})",
        cids,
    ).fetchall()
    cluster_embs: dict[int, list[np.ndarray]] = {}
    for cid, emb in decode_rows(
        conn, ((r["cluster_id"], r["embedding"]) for r in emb_rows), where=where
    ):
        cluster_embs.setdefault(cid, []).append(emb)
    return {cid: np.mean(embs, axis=0) for cid, embs in cluster_embs.items()}


def suggested_match(
    centroid: np.ndarray | None,
    named_centroids: dict[int, np.ndarray],
    named_cids: dict[int, str],
    clusters_by_cid: dict[int, dict[str, Any]],
    ctx: Any,
    meta_fps: list[str],
) -> dict[str, Any] | None:
    """The nearest named cluster to ``centroid`` with a confidence, or
    None. Appends the match's representative filepath to ``meta_fps``
    for the batched metadata lookup."""
    if centroid is None or not named_centroids:
        return None
    best_cid, best_dist = None, float("inf")
    for ncid, ncentroid in named_centroids.items():
        dist = float(np.linalg.norm(centroid - ncentroid))
        if dist < best_dist:
            best_dist = dist
            best_cid = ncid
    if best_cid is None:
        return None
    # Convert distance to confidence %
    # Map distance to confidence: 0 dist = 100%, max_dist (2.0) = 0%
    max_dist = 2.0
    confidence = max(0, min(100, int((1 - best_dist / max_dist) * 100)))
    # Find representative for the suggested person (O(1) lookup)
    match_rep = None
    mc = clusters_by_cid.get(best_cid)
    if mc is not None:
        mr = mc["representative"]
        if ctx.thumbs:
            mr["thumb_hash"] = ctx.thumbs.get_hash(mr["filepath"])
        match_rep = {
            "thumb_hash": mr.get("thumb_hash", ""),
            "face_index": mr["face_index"],
            "filepath": mr.get("filepath", ""),
        }
        meta_fps.append(mr.get("filepath", ""))
    return {
        "cluster_id": best_cid,
        "name": named_cids[best_cid],
        "distance": round(best_dist, 3),
        "confidence": confidence,
        "representative": match_rep,
    }
