"""Cluster face embeddings by identity using agglomerative clustering."""

from __future__ import annotations

from collections import Counter

import numpy as np

from bpp.scoring.install_hold import import_gated
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _filter_to_majority_shape(embeddings: list[np.ndarray], *, where: str) -> list[np.ndarray]:
    """Defense-in-depth before ``np.stack``.

    The read-boundary helper (``decode_embedding``) is the
    first line; this is the safety net for any caller that bypasses
    it. If embeddings have mixed shapes (the Jun-2 incident: 128-d
    SFace + 256-d corruption mixed in one list), drop the minority
    and log the count. ``np.stack`` would otherwise raise
    ``ValueError: all input arrays must have the same shape`` and
    the caller would 500.
    """
    if not embeddings:
        return embeddings
    shapes = Counter(e.shape for e in embeddings)
    if len(shapes) == 1:
        return embeddings
    majority, _ = shapes.most_common(1)[0]
    dropped = len(embeddings) - shapes[majority]
    log.warning(
        "%s: %d embedding(s) had non-majority shape (kept shape=%s, "
        "dropped %d row(s) with shapes=%s) — likely corruption that "
        "slipped past decode_embedding",
        where,
        dropped,
        majority,
        dropped,
        dict((s, n) for s, n in shapes.items() if s != majority),
    )
    return [e for e in embeddings if e.shape == majority]


#: Largest face set handed to scipy's linkage in one go. Average linkage
#: holds the condensed distance matrix (8 bytes per pair): 8000 faces is
#: ~256 MB, 30000 faces would be 3.6 GB. Larger sets are clustered in
#: blocks (see ``_blocked_labels``).
LINKAGE_MAX_FACES = 8000


def _linkage_labels(matrix: np.ndarray, threshold: float) -> np.ndarray:
    """0-based cluster labels by average linkage with a distance cut."""
    hierarchy = import_gated("scipy.cluster.hierarchy")  # not mid-install
    linkage_matrix = hierarchy.linkage(matrix, method="average", metric="euclidean")
    return hierarchy.fcluster(linkage_matrix, t=threshold, criterion="distance") - 1


def _f64(a: np.ndarray) -> np.ndarray:
    """One chunk promoted to float64 for the distance math; the full
    matrices stay float32 (a 500k x 512 float64 copy would be 2 GB)."""
    return np.asarray(a, dtype=np.float64)


def _nearest_centroid(block: np.ndarray, centroids: np.ndarray, chunk: int | None = None):
    """Index of the nearest centroid and its distance for each row of
    *block*, computed a chunk of rows at a time so the distance matrix
    (and the float64 copy of the rows) stays small."""
    cent = _f64(centroids)
    # The float64 copy of a chunk is rows x dim: with few centroids the
    # distance matrix is tiny but the copy is not, so the width counts.
    chunk = chunk or _rows_per_chunk(max(len(cent), cent.shape[1]), len(cent))
    best = np.empty(len(block), dtype=np.int64)
    dist = np.empty(len(block), dtype=np.float64)
    c_sq = np.einsum("ij,ij->i", cent, cent)
    for start in range(0, len(block), chunk):
        rows = _f64(block[start : start + chunk])
        d2 = rows @ cent.T
        d2 *= -2.0
        d2 += np.einsum("ij,ij->i", rows, rows)[:, None]
        d2 += c_sq[None, :]
        np.maximum(d2, 0.0, out=d2)
        idx = np.argmin(d2, axis=1)
        best[start : start + chunk] = idx
        dist[start : start + chunk] = np.sqrt(d2[np.arange(len(rows)), idx])
    return best, dist


_CHUNK_BUDGET_BYTES = 64 * 1024 * 1024


def _rows_per_chunk(n_members: int, n_clusters: int) -> int:
    """Rows of a block scored per step so neither the (rows x members)
    distance matrix nor the (rows x clusters) mean matrix exceeds the
    chunk budget."""
    return max(1, _CHUNK_BUDGET_BYTES // (8 * max(n_members, n_clusters, 1)))


def _nearest_cluster_by_mean(
    matrix: np.ndarray,
    rows_idx: np.ndarray,
    members: np.ndarray,
    member_labels: np.ndarray,
    n_clusters: int,
) -> tuple[np.ndarray, np.ndarray]:
    """For each row ``matrix[rows_idx]``: the cluster whose members are
    nearest on average, and that mean distance — the average-linkage
    criterion for one face joining an existing cluster.

    Members are grouped by label once (at most one block of them, so
    their float64 copy is bounded); each chunk of rows is gathered and
    promoted to float64 on its own, so memory is the chunk budget, never
    the whole tail or (block x clusters) or (members x clusters).
    """
    order = np.argsort(member_labels, kind="stable")
    sorted_members = _f64(members[order])
    sorted_labels = member_labels[order]
    counts = np.bincount(sorted_labels, minlength=n_clusters)
    if np.any(counts == 0):
        empty = np.flatnonzero(counts == 0)[:10].tolist()
        raise ValueError(
            f"empty cluster(s) {empty} of {n_clusters} ({len(members)} members): "
            "every cluster must have a member"
        )
    starts = np.searchsorted(sorted_labels, np.arange(n_clusters))
    m_sq = np.einsum("ij,ij->i", sorted_members, sorted_members)
    best = np.empty(len(rows_idx), dtype=np.int64)
    dist = np.empty(len(rows_idx), dtype=np.float64)
    chunk = _rows_per_chunk(max(len(members), matrix.shape[1]), n_clusters)
    for s0 in range(0, len(rows_idx), chunk):
        rows = _f64(matrix[rows_idx[s0 : s0 + chunk]])
        # In place, so one (rows x members) matrix lives at a time.
        d2 = rows @ sorted_members.T
        d2 *= -2.0
        d2 += np.einsum("ij,ij->i", rows, rows)[:, None]
        d2 += m_sq[None, :]
        np.maximum(d2, 0.0, out=d2)
        np.sqrt(d2, out=d2)
        means = np.add.reduceat(d2, starts, axis=1) / counts
        idx = np.argmin(means, axis=1)
        best[s0 : s0 + chunk] = idx
        dist[s0 : s0 + chunk] = means[np.arange(len(rows)), idx]
    return best, dist


def _blocked_labels(matrix: np.ndarray, threshold: float, block_size: int) -> np.ndarray:
    """Cluster a set too large for one linkage.

    The first *block_size* unassigned faces are clustered by linkage;
    every other unassigned face whose mean distance to one of those
    clusters' members is under *threshold* joins it (the same criterion
    average linkage applies when it attaches a point to a cluster); the
    rest wait for the next block. Each block's clusters get fresh ids,
    so the result is a plain partition. The outcome depends on the input
    order, which callers keep stable (photo id, face index).
    """
    n = len(matrix)
    labels = np.full(n, -1, dtype=np.int64)
    remaining = np.arange(n)
    next_id = 0
    log.info("Clustering %d faces in blocks of %d (linkage memory cap)", n, block_size)
    while len(remaining):
        head, tail = remaining[:block_size], remaining[block_size:]
        if len(head) > 1:
            head_labels = _linkage_labels(matrix[head], threshold)
        else:
            head_labels = np.zeros(1, dtype=np.int64)
        labels[head] = head_labels + next_id
        n_new = int(head_labels.max()) + 1
        if len(tail):
            best, dist = _nearest_cluster_by_mean(matrix, tail, matrix[head], head_labels, n_new)
            joined = dist < threshold
            labels[tail[joined]] = best[joined] + next_id
            tail = tail[~joined]
        next_id += n_new
        remaining = tail
    return labels


def cluster_faces(
    embeddings: list[np.ndarray],
    threshold: float = 0.6,
) -> list[int]:
    """Assign cluster IDs to a list of face embeddings.

    Uses agglomerative clustering with average linkage and a Euclidean
    distance threshold. 0.6 is the standard face_recognition threshold
    for same-person matching. Sets larger than ``LINKAGE_MAX_FACES`` are
    clustered in blocks so memory stays bounded.

    Returns list of integer cluster IDs (0-based), same length as input.
    """
    if len(embeddings) == 0:
        return []
    if len(embeddings) == 1:
        return [0]

    embeddings = _filter_to_majority_shape(embeddings, where="cluster_faces")
    if not embeddings:
        return []
    if len(embeddings) == 1:
        return [0]

    matrix = np.stack(embeddings)
    if len(matrix) > LINKAGE_MAX_FACES:
        labels = _blocked_labels(matrix, threshold, LINKAGE_MAX_FACES)
    else:
        labels = _linkage_labels(matrix, threshold)
    return [int(v) for v in labels]


def pick_representative(
    embeddings: list[np.ndarray],
    *,
    qualities: list[float | None] | None = None,
    quality_weight: float = 0.3,
) -> int:
    """Return index of the best embedding for cluster thumbnail.

    Blends centroid distance (typicality) with face quality score.
    When *qualities* is provided, the combined score is::

        score = (1 - quality_weight) * typicality + quality_weight * quality

    where *typicality* = 1 - normalized_distance.  Higher is better.
    Entries with ``quality=None`` get the cluster median quality.
    """
    if not embeddings:
        return 0
    embeddings = _filter_to_majority_shape(embeddings, where="pick_representative")
    if not embeddings:
        return 0
    matrix = np.stack(embeddings)
    centroid = matrix.mean(axis=0)
    dists = np.linalg.norm(matrix - centroid, axis=1)

    # Typicality: invert distance so closer-to-centroid = higher score
    max_dist = dists.max()
    typicality = 1.0 - dists / max_dist if max_dist > 0 else np.ones(len(dists))

    if qualities is None or not any(q is not None for q in qualities):
        return int(np.argmin(dists))

    # Fill None entries with median of known qualities
    known = [q for q in qualities if q is not None]
    median_q = float(np.median(known)) if known else 0.5
    q_arr = np.array([q if q is not None else median_q for q in qualities])

    combined = (1.0 - quality_weight) * typicality + quality_weight * q_arr
    return int(np.argmax(combined))
