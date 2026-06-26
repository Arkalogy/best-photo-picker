"""Regression guard for the startup perceptual-hash backfill.

This backfill (decode + hash every not-yet-hashed photo, on server start,
to power near-duplicate detection) once ran with ``min(8, cpu_count)``
worker threads AND emitted nothing to the UI. On a real ~6k-photo library
that pegged the machine — 254% CPU, load average 50+, climbing memory —
while looking completely idle to the user.

These tests pin the three properties of the fix so it can't regress:

1. the worker pool stays SMALL (the machine-pegging guard),
2. the backfill reports progress + flips ``phash_running`` off when done,
3. it honours the cancel signal (switch/shutdown must stop it promptly).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace


def test_phash_backfill_worker_cap_is_small():
    """THE never-again guard: a future change that bumps the pool back to
    'use all cores' re-introduces the startup hog. Keep it tiny."""
    from bpp.web.derived_recovery import _PHASH_BACKFILL_WORKERS

    assert 1 <= _PHASH_BACKFILL_WORKERS <= 2, (
        f"phash backfill pool must stay small (got {_PHASH_BACKFILL_WORKERS}); "
        "raising it re-introduces the 254%-CPU / load-50 startup hog on large "
        "libraries"
    )


def _make_ctx(data):
    from bpp.web.analysis_store import AnalysisStore

    return SimpleNamespace(
        analysis_store=AnalysisStore(),
        state={"workdir": "", "analysis": data},
        lock=threading.RLock(),
    )


def _patch_io(monkeypatch, *, hash_fn):
    """Stub the file I/O so no real images are decoded."""
    import bpp.db.smart_albums as sa
    import bpp.web.derived_recovery as si

    monkeypatch.setattr(si, "get_db", lambda _p: object())
    monkeypatch.setattr(si, "db_update_hashes", lambda *a, **k: None)
    monkeypatch.setattr(si, "compute_hashes_from_file", hash_fn)
    monkeypatch.setattr(sa, "refresh_smart_albums", lambda *a, **k: None)


def test_backfill_reports_progress_and_completes(monkeypatch):
    from bpp.web.derived_recovery import precompute_phashes

    data = [{"filepath": f"/x/{i}.jpg", "phash": None, "ahash": None} for i in range(20)]
    _patch_io(monkeypatch, hash_fn=lambda fp: (1, 2))
    ctx = _make_ctx(data)

    precompute_phashes(ctx, data)
    ctx.analysis_store.compute_thread.join(timeout=10)

    assert not ctx.analysis_store.compute_thread.is_alive()
    assert ctx.analysis_store.phash_total == 20
    assert ctx.analysis_store.phash_done == 20
    assert ctx.analysis_store.phash_running is False
    assert ctx.analysis_store.phash_ready.is_set()
    # hashes were written back onto the analysis items
    assert all(item["phash"] == 1 and item["ahash"] == 2 for item in data)


def test_backfill_honours_cancel(monkeypatch):
    """A cancel set before the loop processes anything stops the pass:
    nothing is marked done, and phash_ready is NOT set (so the next
    recompute won't treat half-hashed data as ready)."""
    from bpp.web.derived_recovery import precompute_phashes

    data = [{"filepath": f"/x/{i}.jpg", "phash": None, "ahash": None} for i in range(50)]
    _patch_io(monkeypatch, hash_fn=lambda fp: (1, 2))
    ctx = _make_ctx(data)
    ctx.analysis_store.phash_cancel.set()  # cancel before it starts working

    precompute_phashes(ctx, data)
    ctx.analysis_store.compute_thread.join(timeout=10)

    assert not ctx.analysis_store.compute_thread.is_alive()
    assert ctx.analysis_store.phash_running is False
    assert ctx.analysis_store.phash_done == 0
    assert not ctx.analysis_store.phash_ready.is_set()


def test_join_threads_signals_phash_cancel():
    """switch_library / shutdown must tell the backfill to stop, not just
    join-with-timeout and abandon it churning."""
    from bpp.web.analysis_store import AnalysisStore

    store = AnalysisStore()
    assert not store.phash_cancel.is_set()
    store.join_threads(timeout=0.1)
    assert store.phash_cancel.is_set()


def test_bump_generation_resets_phash_progress():
    from bpp.web.analysis_store import AnalysisStore

    store = AnalysisStore()
    store.phash_cancel.set()
    store.phash_running = True
    store.phash_done = 99
    store.phash_total = 100
    store.bump_generation_and_reset_phash()
    assert not store.phash_cancel.is_set()
    assert store.phash_running is False
    assert store.phash_done == 0
    assert store.phash_total == 0


def test_status_endpoint_exposes_phash_progress(tmp_path):
    """The backfill must be visible — /api/v1/status carries its progress
    so the UI can show it (it ran silently before)."""
    import os

    from bpp.web.app import create_app

    workdir = str(tmp_path / "wd")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    r = app.test_client().get("/api/v1/status")
    assert r.status_code == 200
    pp = r.get_json().get("phash_progress")
    assert pp is not None and set(pp) == {"running", "done", "total"}, pp


# ── S4 (2026-06-12): ordered derived-recovery pipeline ──


def test_recovery_pipeline_runs_steps_in_order(monkeypatch):
    """hash -> sidecar tag -> dup clusters -> Moments -> album refresh.

    The steps used to run as independent racing triggers; clustering
    could fire against NULL hashes and never re-run (the wipe
    incident). The tail must execute them in this exact order."""
    import bpp.db.dedupe as dedupe
    import bpp.db.live_photo as live_photo
    import bpp.db.moments as moments
    import bpp.db.smart_albums as sa
    import bpp.web.derived_recovery as si
    from bpp.web.derived_recovery import precompute_phashes

    calls: list[str] = []
    monkeypatch.setattr(si, "get_db", lambda _p: object())
    monkeypatch.setattr(si, "db_update_hashes", lambda *a, **k: None)
    monkeypatch.setattr(si, "compute_hashes_from_file", lambda fp: (1, 2))
    monkeypatch.setattr(
        live_photo,
        "detect_and_link_live_photo_sidecars",
        lambda *a, **k: calls.append("sidecar") or 0,
    )
    monkeypatch.setattr(
        dedupe, "assign_near_duplicate_clusters", lambda *a, **k: calls.append("dup") or 0
    )
    monkeypatch.setattr(
        moments, "assign_moment_clusters", lambda *a, **k: calls.append("moments") or 0
    )
    monkeypatch.setattr(sa, "refresh_smart_albums", lambda *a, **k: calls.append("refresh"))

    data = [{"filepath": f"/x/{i}.jpg", "phash": None, "ahash": None} for i in range(5)]
    ctx = _make_ctx(data)
    precompute_phashes(ctx, data)
    ctx.analysis_store.compute_thread.join(timeout=10)

    assert calls == ["sidecar", "dup", "moments", "refresh"], calls


def test_recovery_reentrancy_queues_one_rerun(monkeypatch):
    """A second trigger while a recovery runs queues ONE re-run that the
    running thread respawns at its tail — no racing second thread."""
    from bpp.web.derived_recovery import precompute_phashes

    gate = threading.Event()
    started = threading.Event()

    def slow_hash(fp):
        started.set()
        gate.wait(timeout=10)
        return (1, 2)

    _patch_io(monkeypatch, hash_fn=slow_hash)
    data1 = [{"filepath": "/x/a.jpg", "phash": None, "ahash": None}]
    data2 = [{"filepath": "/x/b.jpg", "phash": None, "ahash": None}]
    ctx = _make_ctx(data1)

    precompute_phashes(ctx, data1)
    assert started.wait(timeout=10), "first run never started"
    first_thread = ctx.analysis_store.compute_thread

    # Mimic the analyze worker: it updates ctx.state["analysis"] before
    # kicking the pipeline (the batch-apply writes hashes onto that list).
    ctx.state["analysis"] = data2
    precompute_phashes(ctx, data2)  # queued, not raced
    assert ctx.analysis_store.recovery_rerun is data2
    assert ctx.analysis_store.compute_thread is first_thread, "second thread raced"

    gate.set()
    first_thread.join(timeout=10)
    # The tail respawned for the queued data; wait for it to finish.
    for _ in range(100):
        t = ctx.analysis_store.compute_thread
        if t is not None and t is not first_thread and not t.is_alive():
            break
        threading.Event().wait(0.05)
    assert ctx.analysis_store.recovery_rerun is None
    assert data2[0]["phash"] == 1, "queued re-run never processed its data"
