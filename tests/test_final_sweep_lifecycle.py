"""Final-sweep lifecycle regressions: a serving lock is removed only by
its owner, worker exits don't restart the schema-init epoch, a library
switch rebinds the tab, queued recovery batches merge, a backfill keeps
the cancel token it was spawned with, clearing the library keeps the
health callbacks valid, and both phase-5 causes survive."""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import pytest


def test_only_the_owner_removes_a_serving_lock(tmp_path):
    from bpp.utils.serving_lock import clear_lock, lock_path, write_lock

    wd = str(tmp_path)
    write_lock(wd)
    clear_lock(wd)
    assert not os.path.exists(lock_path(wd))
    with open(lock_path(wd), "w", encoding="utf-8") as f:
        f.write(str(os.getpid() + 1))  # another server's lock
    clear_lock(wd)
    assert os.path.exists(lock_path(wd)), "another server's lock was taken away"
    with open(lock_path(wd), "w", encoding="utf-8") as f:
        f.write("")
    clear_lock(wd)
    assert not os.path.exists(lock_path(wd)), "our own (unreadable) lock stays removable"


def test_a_worker_closing_its_handles_does_not_restart_the_init_epoch():
    from bpp.db import connection

    before = connection._INIT_EPOCH
    connection.close_all_connections()
    assert before == connection._INIT_EPOCH
    connection.close_all_connections(new_epoch=True)
    assert before + 1 == connection._INIT_EPOCH


def test_a_switch_answers_with_the_new_library_id(tmp_path):
    from bpp.web.app import create_app

    a, b = tmp_path / "a", tmp_path / "b"
    for lib in (a, b):
        (lib / "photos").mkdir(parents=True)
    app = create_app(workdir=str(a / "data"), library_path=str(a))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    old = ctx.library_id
    with app.test_client() as c:
        body = c.post("/api/v1/libraries/switch", json={"path": str(b)}).get_json()
    assert body["library_id"] == ctx.library_id != old


def test_queued_recovery_batches_merge(tmp_path):
    from bpp.web import derived_recovery as dr

    running = threading.Event()
    t = threading.Thread(target=running.wait, daemon=True)
    t.start()
    store = SimpleNamespace(compute_thread=t, recovery_rerun=None)
    ctx = SimpleNamespace(analysis_store=store)
    try:
        dr.precompute_phashes(ctx, [{"filepath": "/a.jpg"}, {"filepath": "/b.jpg", "v": 1}])
        dr.precompute_phashes(ctx, [{"filepath": "/b.jpg", "v": 2}, {"filepath": "/c.jpg"}])
    finally:
        running.set()
    assert sorted(i["filepath"] for i in store.recovery_rerun) == ["/a.jpg", "/b.jpg", "/c.jpg"]
    assert next(i for i in store.recovery_rerun if i["filepath"] == "/b.jpg")["v"] == 2


def test_a_backfill_keeps_the_cancel_token_it_was_spawned_with(tmp_path, monkeypatch):
    from bpp.web import derived_recovery as dr
    from bpp.web.analysis_store import AnalysisStore

    store = AnalysisStore()
    spawned = store.phash_cancel
    spawned.set()  # join_threads asked this run to stop …
    store.phash_cancel = threading.Event()  # … and a switch handed out a fresh token
    monkeypatch.setattr(dr, "compute_hashes_from_file", lambda fp: (1, 1))
    ctx = SimpleNamespace(analysis_store=store)
    assert dr._backfill_hashes(ctx, None, ["/x.jpg", "/y.jpg"], spawned) is None, "kept hashing"


def test_clearing_the_library_keeps_the_health_callback_valid():
    from bpp.web.analysis_store import AnalysisStore
    from bpp.web.state_startup_steps import make_scan_refused_callback

    store = AnalysisStore()
    ctx = SimpleNamespace(analysis_store=store)
    cb = make_scan_refused_callback(ctx)
    store.rearm_phash()  # what Clear Library does now
    cb((3, 10))
    assert store.missing_scan_refused == (3, 10)
    assert not store.phash_cancel.is_set()


def test_both_phase_5_failures_are_in_the_traceback(monkeypatch, tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo
    from bpp.web.state_init_phases import backfill_dup_clusters_and_refresh

    conn = init_db(str(tmp_path / "t.db"))
    upsert_photo(conn, {"filepath": "/a.jpg", "file_size": 1, "file_mtime": 1, "phash": 5})
    conn.execute("UPDATE photos SET missing = 0")
    conn.commit()
    monkeypatch.setattr(
        "bpp.db.dedupe.assign_near_duplicate_clusters",
        lambda c: (_ for _ in ()).throw(RuntimeError("clusters died")),
    )
    monkeypatch.setattr(
        "bpp.db.smart_albums.refresh_smart_albums",
        lambda c: (_ for _ in ()).throw(RuntimeError("refresh died")),
    )
    with pytest.raises(RuntimeError, match="refresh died") as info:
        backfill_dup_clusters_and_refresh(conn, force_refresh=True)
    assert "clusters died" in str(info.value.__cause__)


def test_the_backoff_respawn_stands_down_after_a_restart():
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "desktop/src-tauri/src/sidecar.rs").read_text()
    after_sleep = src.split("std::thread::sleep(delay);")[1].split("spawn_sidecar(")[0]
    assert "generation.load(Ordering::SeqCst) != my_generation" in after_sleep
