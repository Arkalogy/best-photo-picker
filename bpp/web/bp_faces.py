"""Faces blueprint: face extraction, clusters, avatars, face crops."""

from __future__ import annotations

import json
import os
import sqlite3

from flask import Blueprint, Response, jsonify, request

from bpp.constants import (
    ACTIVE_PHOTO_SQL,
    CLUSTER_DISMISSED,
)
from bpp.db.face_embedding_safety import decode_embedding
from bpp.db.settings import delete_setting, set_setting
from bpp.errors import ValidationError
from bpp.utils.json_utils import safe_json_loads
from bpp.utils.logging import get_logger
from bpp.web.face_worker import (
    load_face_clusters,
)
from bpp.web.review_meta import attach_photo_meta, photo_meta_by_filepaths
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx, with_face_lock

log = get_logger(__name__)

bp = Blueprint("faces", __name__)

_ACTIVE = ACTIVE_PHOTO_SQL

# Upper bound on rows pulled into RAM for the sampled face grids
# (dismissed faces, cluster/avatar picker). The endpoints render a sampled
# `limit` (default 80); the true count is reported via COUNT(*). Realistic
# clusters/ignored sets are well under this, so behavior is unchanged — it
# only caps a pathological pull (e.g. the main subject's thousands of faces).
_FACE_SCAN_CAP = 3000


# --- Face recognition routes ---


@bp.get("/api/v1/faces/clusters")
def api_faces_clusters() -> tuple[Response, int]:
    """Return all face clusters with their representative thumbnails.

    Honors per-cluster avatar overrides stored in settings (clears
    stale ones automatically), attaches thumb_hash to each
    representative, and reports the dismissed-face count so the UI
    can show an "Ignored" section."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    clusters = load_face_clusters(conn)

    # Load avatar overrides from settings
    avatar_keys = [f"person_avatar_{c['cluster_id']}" for c in clusters]
    avatar_overrides: dict[int, dict] = {}
    if avatar_keys:
        placeholders = ",".join(["?"] * len(avatar_keys))
        override_rows = conn.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            avatar_keys,
        ).fetchall()
        for row in override_rows:
            try:
                cid = int(row["key"].removeprefix("person_avatar_"))
                avatar_overrides[cid] = safe_json_loads(row["value"], {})
            except (ValueError, json.JSONDecodeError):
                # Malformed settings row (non-numeric suffix, corrupted
                # value). User would otherwise see their avatar silently
                # revert to auto-pick — log so support can diagnose.
                log.warning(
                    "Skipping malformed avatar override: key=%r value=%r",
                    row["key"],
                    (row["value"][:200] if row["value"] else None),
                )

    # Batch-validate all avatar overrides in one query instead of one
    # per cluster. Collect (filepath, face_index) pairs that exist on
    # disk, then check DB membership in a single IN-style join.
    live_override_keys: set[tuple[str, int]] = set()
    if avatar_overrides:
        candidates = [
            (ov.get("filepath", ""), ov.get("face_index", 0))
            for ov in avatar_overrides.values()
            if os.path.exists(ov.get("filepath", ""))
        ]
        if candidates:
            ph = ",".join(["(?,?)"] * len(candidates))
            flat = [v for pair in candidates for v in pair]
            rows = conn.execute(
                f"SELECT p.filepath, fe.face_index "
                f"FROM face_embeddings fe JOIN photos p ON p.id=fe.photo_id "
                f"WHERE (p.filepath, fe.face_index) IN ({ph})",
                flat,
            ).fetchall()
            live_override_keys = {(r["filepath"], r["face_index"]) for r in rows}

    for cluster in clusters:
        rep = cluster["representative"]
        cid = cluster["cluster_id"]

        # Check for manual avatar override (skip if filepath or face_index is stale)
        override = avatar_overrides.get(cid)
        if override and os.path.exists(override.get("filepath", "")):
            ov_fp = override["filepath"]
            ov_fi = override.get("face_index", 0)
            if (ov_fp, ov_fi) in live_override_keys:
                rep["filepath"] = ov_fp
                rep["face_index"] = ov_fi
            else:
                # Stale override — clear it so it doesn't keep failing.
                # Protection D: best-effort lazy cleanup write inside a
                # GET handler. If the write fails (disk I/O error, etc.)
                # the read MUST still return its data — letting a stale
                # cosmetic-cleanup failure 500 the People panel is the
                # Jun-2 incident. Log + continue; the override stays
                # stale until the next read or an explicit retry.
                try:
                    delete_setting(conn, f"person_avatar_{cid}")
                    log.info("Cleared stale avatar override for cluster %d", cid)
                except (sqlite3.Error, OSError):
                    # Narrow on purpose: Protection D was added for
                    # disk / DB write failures during the cosmetic
                    # cleanup, not for arbitrary bugs. A blanket
                    # ``except Exception`` would also swallow a
                    # NameError introduced by a future refactor and
                    # keep the People panel serving while the bug
                    # hides in the log. Catch the two error families
                    # this write actually emits; let everything else
                    # surface.
                    log.warning(
                        "Lazy cleanup of stale avatar override for cluster %d "
                        "failed; serving stale data and continuing",
                        cid,
                        exc_info=True,
                    )

        if ctx.thumbs:
            rep["thumb_hash"] = ctx.thumbs.get_hash(rep["filepath"])
        else:
            rep["thumb_hash"] = ""

    # Count dismissed faces so the UI can show an "Ignored" section
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM face_embeddings WHERE cluster_id = {CLUSTER_DISMISSED}"
        ).fetchone()
        dismissed_count = row[0] if row else 0
    except Exception:
        log.warning("Failed to count dismissed face clusters", exc_info=True)
        dismissed_count = 0

    return jsonify({"clusters": clusters, "dismissed_count": dismissed_count}), 200


@bp.get("/api/v1/faces/review")
def api_faces_review() -> tuple[Response, int]:
    """Return unreviewed clusters with suggested matches.

    A cluster is "reviewed" if a smart_person album exists for it with a
    user-chosen name (not the default "Person N" pattern).

    For each unreviewed cluster, computes distance to all named clusters
    and returns the best match (if within suggestion threshold).
    """
    import re

    import numpy as np

    ctx = get_ctx()
    conn = ctx.get_conn()
    clusters = load_face_clusters(conn)
    if not clusters:
        return jsonify({"unreviewed": [], "total": 0, "reviewed": 0}), 200

    # Find which cluster_ids have a named smart_person album.
    # P5b: indexed lookup via get_smart_person_cluster_name_map (uses
    # the v36 smart_person_cluster_id shadow column).
    named_cids: dict[int, str] = {}  # cid -> name
    person_n_re = re.compile(r"^Person \d+$")
    try:
        from bpp.db.albums import get_smart_person_cluster_name_map

        for cid, name in get_smart_person_cluster_name_map(conn).items():
            if not person_n_re.match(name):
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

    total = len(clusters)
    unreviewed = [c for c in clusters if c["cluster_id"] not in named_cids]
    reviewed = total - len(unreviewed)

    # Compute centroids for named clusters from DB embeddings
    named_centroids: dict[int, np.ndarray] = {}
    if named_cids:
        placeholders = ",".join(["?"] * len(named_cids))
        emb_rows = conn.execute(
            f"SELECT cluster_id, embedding FROM face_embeddings "
            f"WHERE cluster_id IN ({placeholders})",
            list(named_cids.keys()),
        ).fetchall()
        # Protection A: route through decode_embedding so a corrupt
        # row can't crash the People-review screen the way the
        # Jun-2 incident crashed /faces/clusters. Bad rows are
        # skipped; the centroid is computed from whatever remains.
        cluster_embs: dict[int, list[np.ndarray]] = {}
        for r in emb_rows:
            cid = r["cluster_id"]
            emb = decode_embedding(r["embedding"], where="bp_faces.review.named_centroids")
            if emb is None:
                continue
            cluster_embs.setdefault(cid, []).append(emb)
        for cid, embs in cluster_embs.items():
            named_centroids[cid] = np.mean(embs, axis=0)

    # Compute centroids for unreviewed clusters
    unreviewed_cids = [c["cluster_id"] for c in unreviewed]
    unreviewed_centroids: dict[int, np.ndarray] = {}
    if unreviewed_cids:
        placeholders = ",".join(["?"] * len(unreviewed_cids))
        emb_rows = conn.execute(
            f"SELECT cluster_id, embedding FROM face_embeddings "
            f"WHERE cluster_id IN ({placeholders})",
            unreviewed_cids,
        ).fetchall()
        # Protection A: same defense as the named-centroids loop above.
        # A single bad row used to crash this whole endpoint; now bad
        # rows are skipped and the centroid is built from what's left.
        cluster_embs2: dict[int, list[np.ndarray]] = {}
        for r in emb_rows:
            cid = r["cluster_id"]
            emb = decode_embedding(r["embedding"], where="bp_faces.review.unreviewed_centroids")
            if emb is None:
                continue
            cluster_embs2.setdefault(cid, []).append(emb)
        for cid, embs in cluster_embs2.items():
            unreviewed_centroids[cid] = np.mean(embs, axis=0)

    # Add thumb hashes + suggested match for each unreviewed cluster.
    # Each crop the user judges ("is this Leo?") carries its source photo's
    # filename / timestamp / score so a tiny face crop is decidable in
    # context — collect every referenced filepath, look up the metadata in
    # one batched query, then attach.
    meta_fps: list[str] = []
    # O(1) cluster lookup for the suggested-match representative below —
    # avoids an O(unreviewed * clusters) linear scan per request.
    clusters_by_cid = {c["cluster_id"]: c for c in clusters}
    for c in unreviewed:
        rep = c["representative"]
        cid = c["cluster_id"]
        if ctx.thumbs:
            rep["thumb_hash"] = ctx.thumbs.get_hash(rep["filepath"])
        meta_fps.append(rep.get("filepath", ""))
        sample_fps = c.get("filepaths", [])[:12]
        # samples: hash + per-photo meta so each thumbnail is self-describing
        c["samples"] = [
            {"hash": ctx.thumbs.get_hash(fp) if ctx.thumbs else "", "filepath": fp}
            for fp in sample_fps
        ]
        meta_fps.extend(sample_fps)

        # Find best match among named clusters
        c["suggested_match"] = None
        centroid = unreviewed_centroids.get(cid)
        if centroid is not None and named_centroids:
            best_cid, best_dist = None, float("inf")
            for ncid, ncentroid in named_centroids.items():
                dist = float(np.linalg.norm(centroid - ncentroid))
                if dist < best_dist:
                    best_dist = dist
                    best_cid = ncid
            if best_cid is not None:
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
                c["suggested_match"] = {
                    "cluster_id": best_cid,
                    "name": named_cids[best_cid],
                    "distance": round(best_dist, 3),
                    "confidence": confidence,
                    "representative": match_rep,
                }

    # Batched metadata lookup, then attach to every representative + sample.
    meta = photo_meta_by_filepaths(conn, meta_fps)
    for c in unreviewed:
        attach_photo_meta(c["representative"], meta)
        for s in c["samples"]:
            info = meta.get(s.get("filepath", ""))
            if info:
                s["filename"] = info["filename"]
                s["date"] = info["date"]
                s["score"] = info["score"]
        sm = c.get("suggested_match")
        if sm and sm.get("representative"):
            attach_photo_meta(sm["representative"], meta)

    return (
        jsonify({"unreviewed": unreviewed, "total": total, "reviewed": reviewed}),
        200,
    )


@bp.get("/api/v1/faces/dismissed")
def api_faces_dismissed() -> tuple[Response, int]:
    """Return dismissed faces with thumbnails for the Ignored section."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    limit = request.args.get("limit", 80, type=int)

    # The grid only renders a sampled `limit`; report the true count
    # separately and bound the scan so a library with thousands of ignored
    # faces doesn't pull every row + its JOINed metadata into RAM. For any
    # realistic library (<= cap) behavior is identical; beyond it the sample
    # is drawn from the highest-quality rows.
    total = conn.execute(
        "SELECT COUNT(*) FROM face_embeddings fe JOIN photos p ON p.id = fe.photo_id "
        f"WHERE fe.cluster_id = {CLUSTER_DISMISSED} AND p.{_ACTIVE}"
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT fe.id, fe.face_index, p.filepath, fe.quality, "
        "p.original_filename, p.date, p.aggregate_score "
        "FROM face_embeddings fe "
        "JOIN photos p ON p.id = fe.photo_id "
        f"WHERE fe.cluster_id = {CLUSTER_DISMISSED} AND p.{_ACTIVE} "
        "ORDER BY COALESCE(fe.quality, 0) DESC, p.filepath, fe.face_index "
        "LIMIT ?",
        (_FACE_SCAN_CAP,),
    ).fetchall()

    faces = []
    seen: set[tuple[str, int]] = set()
    for r in rows:
        fp, fi = r["filepath"], r["face_index"]
        key = (fp, fi)
        if key in seen:
            continue
        seen.add(key)
        th = ctx.thumbs.get_hash(fp) if ctx.thumbs else ""
        faces.append(
            {
                "face_id": r["id"],
                "face_index": fi,
                "thumb_hash": th,
                "quality": round(r["quality"], 3) if r["quality"] else None,
                # Source-photo context so the full-photo preview can caption
                # the crop (filename · timestamp · score).
                "filename": r["original_filename"] or fp.rsplit("/", 1)[-1],
                "date": r["date"],
                "score": r["aggregate_score"],
            }
        )

    if limit > 0 and len(faces) > limit:
        step = len(faces) / limit
        faces = [faces[int(i * step)] for i in range(limit)]

    return jsonify({"faces": faces, "total": total}), 200


@bp.post("/api/v1/faces/avatar")
@requires_local_app
@with_face_lock
def api_set_avatar() -> tuple[Response, int]:
    """Set or clear a manual avatar override for a person cluster."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    cluster_id = data.get("cluster_id")
    if cluster_id is None:
        raise ValidationError("cluster_id required", field="cluster_id")

    conn = ctx.get_conn()
    key = f"person_avatar_{cluster_id}"

    filepath = data.get("filepath")
    face_index = data.get("face_index")
    if filepath is None or face_index is None:
        # Clear override — revert to auto-picked representative
        delete_setting(conn, key)
    else:
        value = json.dumps({"filepath": filepath, "face_index": face_index})
        set_setting(conn, key, value)
    return jsonify({"status": "ok"}), 200


@bp.get("/api/v1/faces/cluster/<int:cluster_id>")
def api_faces_cluster_detail(cluster_id: int) -> tuple[Response, int]:
    """Return face entries for a cluster (for avatar picker). Sampled if large."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    limit = request.args.get("limit", 80, type=int)

    # True count for the UI; bound the scan so a large person (e.g. the main
    # subject with thousands of faces) doesn't fetch every row to sample 80.
    total = conn.execute(
        f"SELECT COUNT(*) FROM face_embeddings fe JOIN photos p ON p.id = fe.photo_id "
        f"WHERE fe.cluster_id = ? AND p.{_ACTIVE}",
        (cluster_id,),
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT fe.id, fe.face_index, p.filepath, fe.quality "
        "FROM face_embeddings fe "
        "JOIN photos p ON p.id = fe.photo_id "
        f"WHERE fe.cluster_id = ? AND p.{_ACTIVE} "
        "ORDER BY COALESCE(fe.quality, 0) DESC, p.filepath, fe.face_index "
        "LIMIT ?",
        (cluster_id, _FACE_SCAN_CAP),
    ).fetchall()

    faces = []
    seen: set[tuple[str, int]] = set()
    for r in rows:
        fp = r["filepath"]
        fi = r["face_index"]
        key = (fp, fi)
        if key in seen:
            continue
        seen.add(key)
        q = r["quality"]
        qual = round(q, 3) if q is not None else None
        faces.append({"face_id": r["id"], "filepath": fp, "face_index": fi, "quality": qual})

    # Sample evenly if too many
    if limit > 0 and len(faces) > limit:
        step = len(faces) / limit
        faces = [faces[int(i * step)] for i in range(limit)]

    for f in faces:
        f["thumb_hash"] = ctx.thumbs.get_hash(f["filepath"]) if ctx.thumbs else ""

    return jsonify({"faces": faces, "total": total}), 200
