"""Face cluster feedback — adaptive threshold learning from user corrections.

Records merge/reassign signals and computes an optimal clustering threshold.
Also tracks hard negative pairs (clusters the user has explicitly separated)
to prevent sibling/lookalike confusion.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from bpp.constants import FACE_CLUSTER_THRESHOLD_FALLBACK
from bpp.db.face_embedding_safety import decode_embedding

if TYPE_CHECKING:
    import numpy as np

_VALID_ACTIONS = ("merge", "reassign_in", "reassign_out")

# Confidence: full weight after this many feedback entries
_FULL_CONFIDENCE_N = 20

# Nudge: suggest re-cluster when learned threshold differs by this much
_NUDGE_THRESHOLD_DELTA = 0.03

# Nudge: require at least this many feedback entries
_NUDGE_MIN_FEEDBACK = 5

# Nudge: require at least this confidence level
_NUDGE_MIN_CONFIDENCE = 0.5


def store_face_feedback(
    conn: sqlite3.Connection,
    action: str,
    *,
    cluster_id_a: int,
    cluster_id_b: int | None = None,
    distance: float,
) -> None:
    """Record a user correction as a feedback signal.

    Actions:
        merge — user merged clusters A and B (same person). Distance = centroid distance.
        reassign_in — user moved a face INTO cluster A. Distance = face-to-centroid.
        reassign_out — user moved a face OUT OF cluster A. Distance = face-to-centroid.
    """
    if action not in _VALID_ACTIONS:
        raise ValueError(f"action must be one of {_VALID_ACTIONS}, got {action!r}")
    # Sanity-cap: SFace L2 distances for genuine same-person pairs are
    # physically bounded by ~sqrt(2) ≈ 1.41, but anything above 0.9 is
    # almost certainly from a bad embedding (e.g. zero-vector, test
    # fixture, or failed centroid computation). Silently drop it rather
    # than let it corrupt the adaptive threshold.
    if distance > 0.9:
        return
    conn.execute(
        "INSERT INTO face_cluster_feedback (action, cluster_id_a, cluster_id_b, distance)"
        " VALUES (?, ?, ?, ?)",
        (action, cluster_id_a, cluster_id_b, distance),
    )
    conn.commit()


def get_face_feedback(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all feedback records."""
    rows = conn.execute(
        "SELECT action, cluster_id_a, cluster_id_b, distance, created_at"
        " FROM face_cluster_feedback ORDER BY id"
    ).fetchall()
    return [
        {
            "action": r[0] if isinstance(r, tuple) else r["action"],
            "cluster_id_a": r[1] if isinstance(r, tuple) else r["cluster_id_a"],
            "cluster_id_b": r[2] if isinstance(r, tuple) else r["cluster_id_b"],
            "distance": r[3] if isinstance(r, tuple) else r["distance"],
            "created_at": r[4] if isinstance(r, tuple) else r["created_at"],
        }
        for r in rows
    ]


# ── Hard negatives ──


def store_hard_negative(conn: sqlite3.Connection, cluster_a: int, cluster_b: int) -> None:
    """Record that clusters A and B are different people.

    Pair ordering is normalized (min, max). Increments count on conflict.
    """
    a, b = min(cluster_a, cluster_b), max(cluster_a, cluster_b)
    conn.execute(
        "INSERT INTO face_hard_negatives (cluster_id_a, cluster_id_b, count)"
        " VALUES (?, ?, 1)"
        " ON CONFLICT(cluster_id_a, cluster_id_b)"
        " DO UPDATE SET count = count + 1, updated_at = datetime('now')",
        (a, b),
    )
    conn.commit()


def remove_hard_negative(conn: sqlite3.Connection, cluster_a: int, cluster_b: int) -> None:
    """Remove a hard negative pair (e.g. when user merges the clusters)."""
    a, b = min(cluster_a, cluster_b), max(cluster_a, cluster_b)
    conn.execute(
        "DELETE FROM face_hard_negatives WHERE cluster_id_a=? AND cluster_id_b=?",
        (a, b),
    )
    conn.commit()


def undo_last_pair_feedback(conn: sqlite3.Connection, cluster_a: int, cluster_b: int) -> bool:
    """Delete the most recent merge-feedback row for a cluster pair —
    the undo for a review-pairs "same" verdict. Returns True if a row
    was removed."""
    row = conn.execute(
        "SELECT id FROM face_cluster_feedback WHERE action='merge' "
        "AND ((cluster_id_a=? AND cluster_id_b=?) OR (cluster_id_a=? AND cluster_id_b=?)) "
        "ORDER BY id DESC LIMIT 1",
        (cluster_a, cluster_b, cluster_b, cluster_a),
    ).fetchone()
    if row is None:
        return False
    conn.execute("DELETE FROM face_cluster_feedback WHERE id=?", (row[0],))
    conn.commit()
    return True


def undo_hard_negative(conn: sqlite3.Connection, cluster_a: int, cluster_b: int) -> bool:
    """Decrement a hard-negative pair (delete at zero) — the undo for a
    review-pairs "different" verdict. store_hard_negative increments a
    counter on repeat verdicts, so undo must decrement, not delete a
    pair the user has condemned multiple times. Returns True if found."""
    a, b = min(cluster_a, cluster_b), max(cluster_a, cluster_b)
    row = conn.execute(
        "SELECT count FROM face_hard_negatives WHERE cluster_id_a=? AND cluster_id_b=?",
        (a, b),
    ).fetchone()
    if row is None:
        return False
    if row[0] <= 1:
        conn.execute(
            "DELETE FROM face_hard_negatives WHERE cluster_id_a=? AND cluster_id_b=?",
            (a, b),
        )
    else:
        conn.execute(
            "UPDATE face_hard_negatives SET count = count - 1, updated_at = datetime('now') "
            "WHERE cluster_id_a=? AND cluster_id_b=?",
            (a, b),
        )
    conn.commit()
    return True


def get_hard_negatives(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all hard negative pairs."""
    rows = conn.execute(
        "SELECT cluster_id_a, cluster_id_b, count FROM face_hard_negatives"
    ).fetchall()
    return [
        {
            "cluster_id_a": r[0] if isinstance(r, tuple) else r["cluster_id_a"],
            "cluster_id_b": r[1] if isinstance(r, tuple) else r["cluster_id_b"],
            "count": r[2] if isinstance(r, tuple) else r["count"],
        }
        for r in rows
    ]


def get_hard_negatives_for_cluster(conn: sqlite3.Connection, cluster_id: int) -> list[int]:
    """Return cluster IDs that are hard negatives of the given cluster."""
    rows = conn.execute(
        "SELECT cluster_id_a, cluster_id_b FROM face_hard_negatives"
        " WHERE cluster_id_a=? OR cluster_id_b=?",
        (cluster_id, cluster_id),
    ).fetchall()
    result = []
    for r in rows:
        a = r[0] if isinstance(r, tuple) else r["cluster_id_a"]
        b = r[1] if isinstance(r, tuple) else r["cluster_id_b"]
        result.append(b if a == cluster_id else a)
    return result


# ── Adaptive threshold ──


def compute_adaptive_face_threshold(
    conn: sqlite3.Connection,
    default: float = FACE_CLUSTER_THRESHOLD_FALLBACK,
) -> tuple[float, dict[str, Any]]:
    """Compute optimal face clustering threshold from accumulated feedback.

    Returns (threshold, info) where info contains metadata for UI display.

    Algorithm:
    - merge + reassign_in distances → "same" signals (should be grouped)
    - reassign_out distances → "different" signals (should be separated)
    - Find boundary between same and different
    - Smooth toward default based on sample count (confidence)
    """
    feedback = get_face_feedback(conn)
    info: dict[str, Any] = {
        "feedback_count": len(feedback),
        "same_count": 0,
        "different_count": 0,
        "confidence": 0.0,
        "source": "default",
    }

    if not feedback:
        return default, info

    same_dists = [f["distance"] for f in feedback if f["action"] in ("merge", "reassign_in")]
    diff_dists = [f["distance"] for f in feedback if f["action"] == "reassign_out"]
    info["same_count"] = len(same_dists)
    info["different_count"] = len(diff_dists)

    margin = 0.02

    if same_dists and diff_dists:
        s_same_max = max(same_dists)
        s_diff_min = min(diff_dists)
        # Clean separation: midpoint. Overlap: bias toward including (higher threshold).
        computed = (s_same_max + s_diff_min) / 2 if s_same_max < s_diff_min else s_same_max + margin
        info["source"] = "learned"
    elif same_dists:
        # Only merges — threshold should be at least as high as the furthest merge
        computed = max(same_dists) + margin
        info["source"] = "learned (same only)"
    else:
        # Only splits — threshold should be below the closest split
        computed = min(diff_dists) - margin
        info["source"] = "learned (different only)"

    # Clamp to reasonable range for face embeddings
    computed = max(0.30, min(1.0, computed))

    # Smooth toward default based on sample count
    n = len(feedback)
    alpha = min(1.0, n / _FULL_CONFIDENCE_N)
    info["confidence"] = round(alpha, 2)

    threshold = alpha * computed + (1.0 - alpha) * default
    return round(threshold, 4), info


# ── Nudge ──


def should_suggest_recluster(
    conn: sqlite3.Connection,
    current_threshold: float,
    default: float = FACE_CLUSTER_THRESHOLD_FALLBACK,
) -> bool:
    """Return True if accumulated feedback suggests re-clustering would help."""
    threshold, info = compute_adaptive_face_threshold(conn, default=default)
    if info["feedback_count"] < _NUDGE_MIN_FEEDBACK:
        return False
    if info["confidence"] < _NUDGE_MIN_CONFIDENCE:
        return False
    return abs(threshold - current_threshold) > _NUDGE_THRESHOLD_DELTA


# ── Ambiguous pair finder (for /api/faces/review-pairs) ──

# Absolute ceiling for "worth reviewing" cluster-centroid distance.
# Anchored to face-embedding empirics: same person is usually < 0.55,
# clearly different is > 0.85. 0.75 is the "probably worth a human glance"
# upper bound. Anchored to an absolute value — not to the adaptive
# threshold — because the adaptive threshold can drift toward 1.0 once
# the user does many merges, which would starve this feature of pairs.
_AMBIGUOUS_MAX_DISTANCE = 0.75


def find_ambiguous_pairs(
    conn: sqlite3.Connection,
    *,
    max_distance: float = _AMBIGUOUS_MAX_DISTANCE,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Find the closest cluster-centroid pairs that are worth reviewing.

    Returns pairs with ``distance <= max_distance``, ordered ascending
    (closest-and-therefore-most-likely-same first). Excludes pairs the
    user already answered — either way: "different" (hard negatives) AND
    "same" (merge feedback). Without the latter, a same-person pair
    reappeared in every review run forever (the verdict teaches the
    threshold but doesn't merge, so the clusters stay distinct).
    Undoing a verdict deletes its row, which puts the pair back in
    rotation. Pair ordering is normalized ``(min(a, b), max(a, b))``.
    Pass ``limit`` to cap the result size.
    """
    import numpy as np

    rows = conn.execute(
        "SELECT cluster_id, embedding FROM face_embeddings WHERE cluster_id >= 0"
    ).fetchall()
    if not rows:
        return []

    cluster_embs: dict[int, list[np.ndarray]] = {}
    for r in rows:
        cid = r[0] if isinstance(r, tuple) else r["cluster_id"]
        emb_blob = r[1] if isinstance(r, tuple) else r["embedding"]
        # Protection A: skip corrupt BLOBs before np.mean down-stream.
        emb = decode_embedding(emb_blob, where="face_feedback.cluster_centroids")
        if emb is None:
            continue
        cluster_embs.setdefault(int(cid), []).append(emb)

    if len(cluster_embs) < 2:
        return []

    centroids = {cid: np.mean(embs, axis=0) for cid, embs in cluster_embs.items()}
    cids = sorted(centroids.keys())

    # Pre-load every already-answered pair into a set for O(1) lookup:
    # "different" verdicts (hard negatives) and "same" verdicts (merge
    # feedback) both settle the question — neither should be re-asked.
    answered_rows = conn.execute(
        "SELECT cluster_id_a, cluster_id_b FROM face_hard_negatives "
        "UNION "
        "SELECT cluster_id_a, cluster_id_b FROM face_cluster_feedback "
        "WHERE action = 'merge' AND cluster_id_a IS NOT NULL AND cluster_id_b IS NOT NULL"
    ).fetchall()
    answered: set[tuple[int, int]] = set()
    for r in answered_rows:
        a = r[0] if isinstance(r, tuple) else r["cluster_id_a"]
        b = r[1] if isinstance(r, tuple) else r["cluster_id_b"]
        answered.add((min(int(a), int(b)), max(int(a), int(b))))

    # Pair scan — O(N²) on cluster count. Typical libraries have < ~300
    # clusters (N² = 90k comparisons), well under a millisecond of numpy work.
    pairs: list[tuple[float, int, int]] = []
    for i, a in enumerate(cids):
        for b in cids[i + 1 :]:
            if (a, b) in answered:
                continue
            d = float(np.linalg.norm(centroids[a] - centroids[b]))
            if d <= max_distance:
                pairs.append((d, a, b))

    pairs.sort()  # ascending by distance
    if limit is not None:
        pairs = pairs[:limit]
    return [{"cluster_a": a, "cluster_b": b, "distance": d} for d, a, b in pairs]


def count_ambiguous_pairs(
    conn: sqlite3.Connection,
    *,
    max_distance: float = _AMBIGUOUS_MAX_DISTANCE,
) -> int:
    """Return the number of reviewable ambiguous pairs (no list built).

    Useful for UI gating (enable/disable a Review pairs button) without
    paying the cost of assembling full pair metadata.
    """
    return len(find_ambiguous_pairs(conn, max_distance=max_distance))
