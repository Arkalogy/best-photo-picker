"""Incremental face assignment compares each new face against every
centroid in one vector op. The result must match the plain per-centroid
loop it replaces."""

from __future__ import annotations

import numpy as np

from bpp.constants import CLUSTER_UNASSIGNED


def _reference(assigned, unassigned, threshold, hard_negatives, min_px, bbox_quality):
    from bpp.scoring.face_cluster import cluster_faces

    cluster_embs: dict[int, list] = {}
    for _fp, _pid, _fi, emb, cid, bw, quality in assigned:
        q = quality if quality is not None else bbox_quality(bw)
        cluster_embs.setdefault(cid, []).append((emb, q))
    centroids = {}
    for cid, pairs in cluster_embs.items():
        embs = np.stack([e for e, _ in pairs])
        w = np.array([max(q, 0.1) for _, q in pairs])
        centroids[cid] = np.average(embs, axis=0, weights=w / w.sum())
    max_existing = max(centroids) if centroids else -1
    labels, leftover = [], []
    for i, (_fp, _pid, _fi, emb, bw, _q) in enumerate(unassigned):
        if bw < min_px or not centroids:
            labels.append(CLUSTER_UNASSIGNED)
            leftover.append(i)
            continue
        best_cid, best = CLUSTER_UNASSIGNED, float("inf")
        for cid, c in centroids.items():
            d = float(np.linalg.norm(emb - c))
            if d < best:
                best, best_cid = d, cid
        if best < threshold:
            skip = False
            for neg in hard_negatives.get(best_cid, set()):
                if (
                    neg in centroids
                    and float(np.linalg.norm(emb - centroids[neg])) < threshold * 1.5
                ):
                    skip = True
                    break
            if skip:
                labels.append(CLUSTER_UNASSIGNED)
                leftover.append(i)
            else:
                labels.append(best_cid)
        else:
            labels.append(CLUSTER_UNASSIGNED)
            leftover.append(i)
    if leftover:
        embs = [unassigned[i][3] for i in leftover]
        sub = [0] if len(embs) == 1 else cluster_faces(embs, threshold=threshold)
        next_id, used, remap = max_existing + 1, set(centroids), {}
        for sl in sub:
            if sl not in remap:
                while next_id in used:
                    next_id += 1
                remap[sl] = next_id
                used.add(next_id)
                next_id += 1
        for j, idx in enumerate(leftover):
            labels[idx] = remap[sub[j]]
    return labels


def test_vectorized_assignment_matches_the_loop():
    from bpp.db import face_identity_remap as fir

    rng = np.random.default_rng(3)
    dim, n_clusters = 32, 40
    centers = rng.normal(size=(n_clusters, dim)) * 4
    assigned = []
    for cid in range(n_clusters):
        for k in range(5):
            emb = (centers[cid] + rng.normal(size=dim) * 0.1).astype(np.float32)
            assigned.append((f"/a{cid}_{k}.jpg", cid * 10 + k, 0, emb, cid, 80, 0.3 + k / 10))
    unassigned = []
    for i in range(90):
        if i % 3 == 0:  # near a known person
            emb = centers[i % n_clusters] + rng.normal(size=dim) * 0.1
        elif i % 3 == 1:  # a new person, far away
            emb = rng.normal(size=dim) * 4 + 50
        else:  # too small to assign
            emb = centers[i % n_clusters] + rng.normal(size=dim) * 0.1
        bw = 20 if i % 3 == 2 else 80
        unassigned.append((f"/u{i}.jpg", 1000 + i, 0, emb.astype(np.float32), bw, None))
    hard = {0: {1}, 1: {0}, 5: {6}}

    got = fir._assign_new_faces(assigned, unassigned, 0.6, hard_negatives=hard)
    want = _reference(assigned, unassigned, 0.6, hard, fir._MIN_ASSIGN_PX, fir._bbox_quality)
    assert got == want
