"""Final-sweep performance regressions: the recycle-bin and hidden
actions take more ids than SQLite's variable limit, the global hash
pass keeps the old answer behind its band index and is far from
quadratic, clearing the library refuses a straggling thread, and the
dedupe cache no longer snapshots every item's keys."""

from __future__ import annotations

import random
import time
from types import SimpleNamespace


def _many_photos(conn, n):
    from bpp.db.photos import upsert_photo

    for i in range(n):
        upsert_photo(conn, {"filepath": f"/m/{i}.jpg", "file_size": 1, "file_mtime": 1})
    conn.execute("UPDATE photos SET missing = 0")
    conn.commit()
    return [r[0] for r in conn.execute("SELECT id FROM photos").fetchall()]


def test_bulk_lifecycle_actions_take_more_ids_than_sqlite_allows(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.photos_lifecycle import (
        hide_photos,
        permanent_delete_photos,
        restore_photos,
        soft_delete_photos,
        unhide_photos,
    )

    conn = init_db(str(tmp_path / "t.db"))
    ids = _many_photos(conn, 33_000)
    assert hide_photos(conn, ids) == 33_000
    assert unhide_photos(conn, ids) == 33_000
    assert soft_delete_photos(conn, ids) == 33_000
    assert restore_photos(conn, ids) == 33_000
    assert soft_delete_photos(conn, ids) == 33_000
    assert len(permanent_delete_photos(conn, ids)) == 33_000


def test_the_band_index_keeps_the_old_answer_and_is_not_quadratic():
    from bpp.dedupe.cluster import _band_edges, _global_dedup
    from bpp.dedupe.phash import dual_hash_distance, hamming_distance

    assert _band_edges(8) == [
        (0, 7),
        (7, 14),
        (14, 21),
        (21, 28),
        (28, 36),
        (36, 43),
        (43, 50),
        (50, 57),
        (57, 64),
    ]
    assert _band_edges(40) == []
    rnd = random.Random(5)
    base = [rnd.getrandbits(64) for _ in range(300)]
    items = []
    for i in range(3000):
        ph = base[i % 300] ^ sum(1 << rnd.randrange(64) for _ in range(rnd.randrange(0, 12)))
        ah = (base[i % 300] ^ 0xABCD) if rnd.random() < 0.6 else None
        items.append(
            {
                "filepath": f"/p{i}.jpg",
                "phash": ph,
                "ahash": ah,
                "aggregate_score": rnd.random(),
                "date_month": "2024-01",
            }
        )
    got = [r["filepath"] for r in _global_dedup(items, 8)]
    reps = []
    for it in sorted(items, key=lambda d: -d["aggregate_score"]):
        if it["filepath"] == got[0] or not reps:
            pass
        matched = False
        for rep in reps:
            if it["ahash"] is not None and rep["ahash"] is not None:
                d = dual_hash_distance(it["phash"], it["ahash"], rep["phash"], rep["ahash"])
            else:
                d = hamming_distance(it["phash"], rep["phash"])
            if d <= 8:
                matched = True
                break
        if not matched:
            reps.append(it)
    assert got == [r["filepath"] for r in reps]

    big = [
        {
            "filepath": f"/b{i}.jpg",
            "phash": rnd.getrandbits(64),
            "ahash": None,
            "aggregate_score": rnd.random(),
            "date_month": "2024-01",
        }
        for i in range(40_000)
    ]
    # CPU time, not wall-clock: a loaded machine must not fail this. The
    # quadratic pass this guards against takes minutes on 40k hashes.
    t0 = time.process_time()
    _global_dedup(big, 8)
    assert time.process_time() - t0 < 20.0, "the global pass still compares every representative"


def test_clearing_the_library_refuses_a_straggling_thread(tmp_path, monkeypatch):
    from bpp.web.app import create_app

    lib = tmp_path / "lib"
    (lib / "photos").mkdir(parents=True)
    app = create_app(workdir=str(lib / "data"), library_path=str(lib))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    import threading

    stop = threading.Event()
    t = threading.Thread(target=stop.wait, daemon=True)
    t.start()
    ctx.analysis_store.phase5_thread = t
    store = ctx.analysis_store
    real_join = type(store).join_threads
    monkeypatch.setattr(store, "join_threads", lambda timeout: real_join(store, 0.2))
    try:
        r = app.test_client().delete("/api/v1/library", json={"confirmation": "delete"})
    finally:
        stop.set()
    assert r.status_code == 409, r.get_data(as_text=True)
    assert not ctx.analysis_store.phash_cancel.is_set(), "the next backfill would start cancelled"
    t.join(timeout=5)
    # A thread that did stop is forgotten by the next join; a live one is kept.
    ctx.analysis_store.join_threads(timeout=1.0)
    assert ctx.analysis_store.phase5_thread is None


def test_the_dedupe_cache_remembers_only_the_added_keys(monkeypatch):
    from bpp.web import recompute as rc

    rc._dedupe_cache_clear()
    items = [
        {"filepath": f"/p{i}.jpg", "phash": i, "ahash": i, "aggregate_score": 0.5} for i in range(3)
    ]

    def annotating(items, config, **kw):
        for it in items:
            it["cluster_size"] = 1
        items[0]["odd"] = True
        return list(items), "hash"

    monkeypatch.setattr(rc, "dedupe_candidates", annotating)
    opts = SimpleNamespace(clip_embeddings=None, skip_dedupe=False, clip_threshold=None)
    rc._dedupe_cached([dict(i) for i in items], {}, opts)
    outcome = rc._DEDUPE_CACHE["entry"][1][0]
    assert outcome[0] == ("/p0.jpg", {"cluster_size": 1, "odd": True})
    assert outcome[1] == ("/p1.jpg", {"cluster_size": 1})
