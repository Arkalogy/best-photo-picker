"""Pass 2 of the semantic dedupe scores candidates in blocks (one matrix
product per block) and must give exactly the sequential result."""

from __future__ import annotations

import os

import numpy as np


def _reference(items, fp_to_emb, threshold):
    sorted_items = sorted(items, key=lambda x: -x.get("aggregate_score", 0))
    reps: list[dict] = []
    first = next(iter(fp_to_emb.values()), None)
    if first is None or not sorted_items:
        return list(sorted_items)
    buf = np.empty((len(sorted_items), first.shape[0]), dtype=first.dtype)
    row_to_rep: list[int] = []
    n = 0
    for item in sorted_items:
        emb = fp_to_emb.get(item["filepath"])
        if emb is None:
            reps.append(item)
            continue
        match, sim = None, 0.0
        if n:
            sims = buf[:n] @ emb
            best = int(np.argmax(sims))
            if float(sims[best]) >= threshold:
                match, sim = row_to_rep[best], float(sims[best])
        if match is not None:
            rep = reps[match]
            rep.setdefault("similar_photos", []).append(
                {
                    "filepath": item["filepath"],
                    "similarity": round(sim, 3),
                    "filename": os.path.basename(item["filepath"]),
                }
            )
            rep["cluster_size"] = rep.get("cluster_size", 1) + 1
            continue
        reps.append(item)
        buf[n] = emb
        row_to_rep.append(len(reps) - 1)
        n += 1
    return reps


def _shape(reps):
    return [
        (
            r["filepath"],
            r.get("cluster_size", 1),
            [(s["filepath"], s["similarity"]) for s in r.get("similar_photos", [])],
        )
        for r in reps
    ]


def test_blocked_pass_two_equals_the_sequential_pass():
    from bpp.dedupe import semantic as sem

    rng = np.random.default_rng(7)
    dim, n_scenes = 32, 60
    scenes = rng.normal(size=(n_scenes, dim))
    items, fp_to_emb = [], {}
    for i in range(700):
        fp = f"/p{i}.jpg"
        items.append({"filepath": fp, "aggregate_score": float(rng.random())})
        if i % 11 == 0:
            continue  # no embedding
        v = scenes[i % n_scenes] + rng.normal(size=dim) * (0.02 if i % 4 else 2.0)
        fp_to_emb[fp] = (v / np.linalg.norm(v)).astype(np.float32)

    import copy

    want = _shape(_reference(copy.deepcopy(items), fp_to_emb, 0.93))
    got = _shape(sem._global_semantic_dedup(copy.deepcopy(items), fp_to_emb, 0.93))
    assert len(got) < 700, "the data has duplicates to merge"
    assert got == want
