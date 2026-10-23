"""Clustering a large face set must not build an N x N linkage.

scipy's average linkage holds the condensed distance matrix: 8 bytes per
pair, so 30k faces need 3.6 GB. Above a cap the set is clustered in
blocks: one block by linkage, the rest assigned to those clusters by
centroid distance, and whatever is left clustered in the next block.
"""

from __future__ import annotations

import numpy as np
import pytest


def _six_people(rng, per_person=7, dim=16):
    centers = rng.normal(size=(6, dim)) * 10
    embs, truth = [], []
    for ci, c in enumerate(centers):
        for _ in range(per_person):
            embs.append((c + rng.normal(size=dim) * 0.05).astype(np.float32))
            truth.append(ci)
    return embs, truth


def _same_partition(labels, truth):
    seen: dict[int, int] = {}
    for lab, t in zip(labels, truth, strict=True):
        assert seen.setdefault(lab, t) == t, "one cluster holds two people"
    assert len(seen) == len(set(truth)), "one person split into several clusters"


def test_large_sets_are_clustered_in_bounded_blocks(monkeypatch):
    from bpp.scoring import face_cluster as fc

    embs, truth = _six_people(np.random.default_rng(0))
    monkeypatch.setattr(fc, "LINKAGE_MAX_FACES", 10)
    sizes: list[int] = []
    real = fc._linkage_labels

    def spy(matrix, threshold):
        sizes.append(len(matrix))
        return real(matrix, threshold)

    monkeypatch.setattr(fc, "_linkage_labels", spy)
    labels = fc.cluster_faces(embs, threshold=0.6)
    assert sizes, "linkage still does the clustering"
    assert max(sizes) <= 10, f"a linkage ran over the cap: {sizes}"
    assert len(labels) == len(embs)
    _same_partition(labels, truth)


def test_small_sets_still_use_one_linkage(monkeypatch):
    from bpp.scoring import face_cluster as fc

    embs, truth = _six_people(np.random.default_rng(1))
    sizes: list[int] = []
    real = fc._linkage_labels
    monkeypatch.setattr(fc, "_linkage_labels", lambda m, t: sizes.append(len(m)) or real(m, t))
    labels = fc.cluster_faces(embs, threshold=0.6)
    assert sizes == [len(embs)]
    _same_partition(labels, truth)


def test_cap_is_sized_for_a_few_hundred_megabytes():
    from bpp.scoring.face_cluster import LINKAGE_MAX_FACES

    pairs = LINKAGE_MAX_FACES * (LINKAGE_MAX_FACES - 1) // 2
    assert pairs * 8 <= 400 * 1024 * 1024


def test_tail_faces_join_by_mean_distance_without_a_dense_cluster_matrix():
    from bpp.scoring import face_cluster as fc

    rng = np.random.default_rng(5)
    members = rng.normal(size=(50, 8))
    labels = rng.integers(0, 7, size=50)
    block = rng.normal(size=(30, 8))
    best, dist = fc._nearest_cluster_by_mean(block, np.arange(30), members, labels, 7)
    for i in range(30):
        means = [np.linalg.norm(members[labels == k] - block[i], axis=1).mean() for k in range(7)]
        assert best[i] == int(np.argmin(means)), f"row {i}: nearest cluster"
        assert dist[i] == pytest.approx(min(means)), f"row {i}: mean distance"


def test_mean_distance_chunk_is_bounded_by_members_and_clusters():
    from bpp.scoring import face_cluster as fc

    budget = 64 * 1024 * 1024
    for n_members, n_clusters in ((8000, 5000), (8000, 10), (100, 100)):
        rows = fc._rows_per_chunk(n_members, n_clusters)
        assert rows >= 1, (n_members, n_clusters)
        assert rows * max(n_members, n_clusters) * 8 <= budget, (n_members, n_clusters, rows)
