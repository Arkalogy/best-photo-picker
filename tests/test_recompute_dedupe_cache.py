"""Recompute reuses the dedupe result while its inputs are unchanged: a
k or seed change must not redo the O(N²) pass."""

from __future__ import annotations

import numpy as np


def _opts(items, **kw):
    from bpp.web.recompute import RecomputeOptions

    config = {
        "default_selection_k": 50,
        "selection_similarity_threshold": 0.0,
        "max_per_day": 3,
        "min_per_month": 1,
        "max_per_month": 0,
        "clip_similarity_threshold": 0.92,
        "sensitive_in_picks": "allow",
        "sensitive_nudity_threshold": 0.5,
    }
    config.update(kw.pop("config", {}))
    return RecomputeOptions(analysis=items, config=config, **kw)


def _items(n=6):
    return [
        {
            "filepath": f"/p{i}.jpg",
            "id": i,
            "date": f"2024-01-0{i + 1}T10:00:00",
            "date_day": f"2024-01-0{i + 1}",
            "date_month": "2024-01",
            "aggregate_score": 0.5 + i / 100,
            "phash": 1000 + i * 977,
            "ahash": 2000 + i * 991,
        }
        for i in range(n)
    ]


def test_k_changes_reuse_the_dedupe_result(monkeypatch):
    from bpp.web import recompute as rc

    rc._dedupe_cache_clear()
    calls: list = []
    real = rc.dedupe_candidates
    monkeypatch.setattr(rc, "dedupe_candidates", lambda *a, **k: calls.append(1) or real(*a, **k))
    items = _items()
    rc.recompute(_opts(items, k=2))
    rc.recompute(_opts(items, k=3))
    assert calls == [1]


def test_changed_scores_or_members_redo_the_dedupe(monkeypatch):
    from bpp.web import recompute as rc

    rc._dedupe_cache_clear()
    calls: list = []
    real = rc.dedupe_candidates
    monkeypatch.setattr(rc, "dedupe_candidates", lambda *a, **k: calls.append(1) or real(*a, **k))
    items = _items()
    rc.recompute(_opts(items, k=2))
    items[0]["aggregate_score"] = 0.99
    rc.recompute(_opts(items, k=2))
    rc.recompute(_opts(items[:-1], k=2))
    emb = {i: np.ones(4, np.float32) for i in range(6)}
    rc.recompute(_opts(items[:-1], k=2, clip_embeddings=emb))
    assert calls == [1, 1, 1, 1]


def test_a_cache_hit_replays_every_field_the_pass_added(monkeypatch):
    """The hash strategy fills phash/ahash on rows without them and a
    plugin strategy may annotate; a hit must hand the next call the
    same dicts the first call got."""
    from bpp.web import recompute as rc

    rc.dedupe_cache_clear()
    real = rc.dedupe_candidates

    def annotating(items, config, **kw):
        cands, mode = real(items, config, **kw)
        for c in cands:
            c.setdefault("strategy_note", "kept")
        return cands, mode

    monkeypatch.setattr(rc, "dedupe_candidates", annotating)
    items = _items()
    for it in items:
        it.pop("phash", None)
    first = rc.recompute(_opts([dict(i) for i in items]))
    second = rc.recompute(_opts([dict(i) for i in items], config={"default_selection_k": 7}))
    assert all(p.get("strategy_note") == "kept" for p in second["photos"])
    assert [p["filepath"] for p in first["photos"]] == [p["filepath"] for p in second["photos"]]


def test_a_cache_hit_still_carries_the_duplicate_cluster_fields(monkeypatch):
    from bpp.web import recompute as rc

    rc._dedupe_cache_clear()
    items = _items()
    same = np.ones(8, np.float32) / np.sqrt(8)
    emb = {i: np.eye(8, dtype=np.float32)[i] for i in range(6)}
    emb[0], emb[1] = same, same.copy()  # a near-duplicate pair
    first = rc.recompute(_opts(items, k=3, clip_embeddings=emb))
    second = rc.recompute(_opts(items, k=4, clip_embeddings=emb))
    rep = next(p for p in first["photos"] if p.get("cluster_size", 1) > 1)
    again = next(p for p in second["photos"] if p["filepath"] == rep["filepath"])
    assert again["cluster_size"] == rep["cluster_size"]
    assert [s["filepath"] for s in again.get("similar_photos", [])] == [
        s["filepath"] for s in rep.get("similar_photos", [])
    ]


def test_a_date_change_or_a_new_clip_dict_redoes_the_dedupe(monkeypatch):
    from bpp.web import recompute as rc

    rc._dedupe_cache_clear()
    calls: list = []
    real = rc.dedupe_candidates
    monkeypatch.setattr(rc, "dedupe_candidates", lambda *a, **k: calls.append(1) or real(*a, **k))
    items = _items()
    rc.recompute(_opts(items, k=2))
    items[2]["date"] = "2024-03-01T10:00:00"
    rc.recompute(_opts(items, k=2))
    emb = {i: np.ones(4, np.float32) for i in range(6)}
    rc.recompute(_opts(items, k=2, clip_embeddings=emb))
    rc.recompute(_opts(items, k=2, clip_embeddings=dict(emb)))  # same content, new dict
    assert calls == [1, 1, 1, 1]


def test_an_ahash_change_redoes_the_dedupe():
    """The pass measures distance on both hashes; a re-analysis that
    changes only the average hash must not replay the old result."""
    from bpp.web import recompute as rc

    items = [
        {
            "filepath": f"/p{i}.jpg",
            "id": i,
            "date": "2026-01-01T10:00:00",
            "date_day": "2026-01-01",
            "date_month": "2026-01",
            "aggregate_score": 0.5 + i / 10,
            "phash": i * ((1 << 64) - 1),
            "ahash": i * ((1 << 64) - 1),
        }
        for i in range(2)
    ]
    opts = _opts(
        items,
        k=2,
        config={
            "hash_distance_threshold": 1,
            "global_hash_distance_threshold": 0,
        },
    )
    rc.dedupe_cache_clear()
    assert rc.recompute(opts)["stats"]["after_dedupe"] == 2
    items[1]["ahash"] = 0
    cached = rc.recompute(opts)
    rc.dedupe_cache_clear()
    fresh = rc.recompute(opts)
    assert fresh["stats"]["after_dedupe"] == 1
    assert cached["selected_paths"] == fresh["selected_paths"]
