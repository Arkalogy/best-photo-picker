"""Cluster face embeddings by identity using agglomerative clustering."""

from __future__ import annotations

from collections import Counter

import numpy as np

from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _filter_to_majority_shape(embeddings: list[np.ndarray], *, where: str) -> list[np.ndarray]:
    """Defense-in-depth before ``np.stack``.

    Protection A's read-boundary helper (``decode_embedding``) is the
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


def cluster_faces(
    embeddings: list[np.ndarray],
    threshold: float = 0.6,
) -> list[int]:
    """Assign cluster IDs to a list of face embeddings.

    Uses agglomerative clustering with average linkage and a Euclidean
    distance threshold. 0.6 is the standard face_recognition threshold
    for same-person matching.

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

    from scipy.cluster.hierarchy import fcluster, linkage

    matrix = np.stack(embeddings)
    linkage_matrix = linkage(matrix, method="average", metric="euclidean")
    labels = fcluster(linkage_matrix, t=threshold, criterion="distance")
    # fcluster returns 1-based labels; convert to 0-based
    return [int(v) - 1 for v in labels]


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
