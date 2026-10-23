"""Face clustering edge cases (split from test_clustering_stability.py
for the 500-line cap): tiny, identical, noisy, boundary and degenerate
embeddings, and a stable representative."""

from __future__ import annotations

import numpy as np

from bpp.scoring.face_cluster import cluster_faces, pick_representative


class TestClusterEdgeCases:
    def test_two_embeddings(self):
        rng = np.random.RandomState(0)
        assert len(cluster_faces([rng.randn(128) for _ in range(2)], threshold=0.6)) == 2

    def test_all_identical_many(self):
        embs = [np.ones(128) * 0.5 for _ in range(100)]
        labels = cluster_faces(embs, threshold=0.6)
        assert len(set(labels)) == 1

    def test_very_noisy_embeddings(self):
        rng = np.random.RandomState(123)
        assert len(cluster_faces([rng.randn(128) * 10 for _ in range(50)], threshold=0.6)) == 50

    def test_near_threshold_boundary(self):
        e1, e2 = np.zeros(128), np.zeros(128)
        e2[0] = 0.55
        labels = cluster_faces([e1, e2], threshold=0.55)
        assert labels[0] == labels[1]

    def test_slightly_above_threshold(self):
        e1, e2 = np.zeros(128), np.zeros(128)
        e2[0] = 0.56
        labels = cluster_faces([e1, e2], threshold=0.55)
        assert labels[0] != labels[1]

    def test_zero_embeddings(self):
        labels = cluster_faces([np.zeros(128) for _ in range(5)], threshold=0.6)
        assert len(set(labels)) == 1

    def test_very_large_embeddings(self):
        rng = np.random.RandomState(0)
        assert len(cluster_faces([rng.randn(128) * 1e6 for _ in range(10)], threshold=1e7)) == 10

    def test_pick_representative_stable(self):
        rng = np.random.RandomState(42)
        embs = [rng.randn(128) for _ in range(20)]
        ref = pick_representative(embs)
        for _ in range(10):
            assert pick_representative(embs) == ref
