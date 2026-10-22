"""Regressions for the whole-tree review (Codex lanes + own passes):
scoring on a base install, CLIP preprocessing sizes, cluster label
alignment, analysis cache during the face phase, one migration per
process, clear-library refusals, failed and successful library switches,
and the desktop shell's single-supervisor restart."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_face_detection_degrades_without_mediapipe(monkeypatch):
    """No ``faces`` extra: the detector is None and the import must not
    be reached (it would raise for every photo)."""
    from bpp.scoring import face_mediapipe

    monkeypatch.setitem(sys.modules, "mediapipe", None)
    monkeypatch.setattr(face_mediapipe, "_get_mediapipe_detector", lambda: None)
    assert face_mediapipe._mediapipe_detect(np.zeros((64, 64, 3), np.uint8)) == []


@pytest.mark.parametrize("size", [(2448, 3264), (3264, 2448), (223, 500), (1000, 1000)])
def test_clip_preprocess_always_yields_a_full_crop(size):
    from bpp.scoring.clip_embed import _preprocess

    h, w = size
    out = _preprocess(np.zeros((h, w, 3), np.uint8))
    assert out.shape == (1, 3, 224, 224), size


def test_cluster_labels_keep_their_positions_when_a_row_is_malformed():
    from bpp.constants import CLUSTER_UNASSIGNED
    from bpp.scoring.face_cluster import cluster_faces, pick_representative

    rng = np.random.default_rng(1)
    a = rng.normal(size=128).astype(np.float32)
    rows = [a, a + 0.001, np.zeros(256, np.float32), a + 0.002]
    labels = cluster_faces(rows, threshold=0.6)
    assert len(labels) == len(rows)
    assert labels[2] == CLUSTER_UNASSIGNED and labels[0] == labels[1] == labels[3]
    # The representative index points into the caller's list, past the
    # malformed row.
    idx = pick_representative([np.zeros(256, np.float32), a, a + 0.5, a + 0.001])
    assert idx in (1, 3)


def test_downstream_phases_drop_the_cache_instead_of_publishing_raw_rows():
    """Raw scoring dicts carry no ids; publishing them broke recompute for
    the whole faces + CLIP window. The worker invalidates instead."""
    from types import SimpleNamespace

    from bpp.web.analyze_worker import AnalyzeWorker

    calls: list[str] = []
    ctx = SimpleNamespace(
        lock=threading.Lock(),
        state={"analysis": "stale"},
        invalidate_analysis=lambda: calls.append("x"),
    )
    worker = AnalyzeWorker()
    worker.ctx = ctx
    worker._cancelled = threading.Event()
    worker._cancelled.set()  # skip the phases themselves
    worker._emit = lambda *_a, **_k: None
    counts: dict = {}
    worker._downstream_phases(None, [{"filepath": "/x.jpg"}], {}, counts)
    assert calls == ["x"] and ctx.state["analysis"] == "stale"


def test_a_database_is_initialised_once_per_process_across_threads(tmp_path, monkeypatch):
    from bpp.db import connection

    db = str(tmp_path / "t.db")
    seen: list[str] = []
    real = connection.create_tables

    def counting(conn):
        seen.append(threading.current_thread().name)
        return real(conn)

    monkeypatch.setattr(connection, "create_tables", counting)
    errors: list[BaseException] = []

    def worker():
        try:
            connection.init_db(db)
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, name=f"t{i}") for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(seen) == 1, seen


def test_clear_library_is_refused_while_any_worker_runs(tmp_path, monkeypatch):
    from bpp.web.app import create_app

    lib = tmp_path / "lib"
    (lib / "photos").mkdir(parents=True)
    app = create_app(workdir=str(lib / "data"), library_path=str(lib))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    face = ctx.workers["face"] if "face" in ctx.workers else next(iter(ctx.workers.values()))
    monkeypatch.setattr(type(face), "is_alive", property(lambda self: self is face))
    resp = app.test_client().delete("/api/v1/library", json={"confirmation": "delete"})
    assert resp.status_code == 409, resp.get_json()
    assert "running" in resp.get_json()["error"]


def test_a_refused_switch_restores_the_previous_library(tmp_path, monkeypatch):
    from bpp.web import state_init
    from bpp.web.app import create_app

    a = tmp_path / "A"
    b = tmp_path / "B"
    (a / "photos").mkdir(parents=True)
    (b / "photos").mkdir(parents=True)
    app = create_app(workdir=str(a / "data"), library_path=str(a))
    ctx = app.extensions["bpp"]
    real = state_init._acquire_serving_lock

    def refuse_b(wd):
        if wd == str(b / "data"):
            raise RuntimeError("Another bpp server appears to be running")
        return real(wd)

    monkeypatch.setattr(state_init, "_acquire_serving_lock", refuse_b)
    with pytest.raises(RuntimeError):
        ctx.switch_library(str(b))
    assert ctx.state["library_path"] == str(a)
    assert ctx.paths.library_path == str(a)
    with app.app_context():
        (row,) = ctx.get_conn().execute("PRAGMA database_list").fetchall()[:1]
    assert str(a / "data") in row[2]


def test_a_switch_forgets_the_old_librarys_face_map_and_edit_ids(tmp_path):
    from bpp.web.app import create_app

    a = tmp_path / "A"
    b = tmp_path / "B"
    (a / "photos").mkdir(parents=True)
    (b / "photos").mkdir(parents=True)
    app = create_app(workdir=str(a / "data"), library_path=str(a))
    ctx = app.extensions["bpp"]
    ctx.caches.face_cluster_map._map = {str(a / "photos" / "p.jpg"): [7]}
    ctx.caches.enhanced_ids.edited = {1}
    ctx.caches.enhanced_ids.auto_enhanced = {1}
    ctx.switch_library(str(b))
    assert ctx.caches.face_cluster_map.peek() is None
    assert not ctx.caches.enhanced_ids.both_loaded()


def test_restart_server_is_the_only_spawner_after_an_intentional_stop():
    """Restart Server bumps the spawn generation before it kills the
    child; a Terminated handler from an older generation stands down."""
    src = "\n".join(
        p.read_text() for p in sorted((ROOT / "desktop" / "src-tauri" / "src").glob("*.rs"))
    )
    assert "pub(crate) generation: Arc<AtomicU64>" in src
    restart = src.split("fn restart_server(")[1].split("\n}\n")[0]
    assert "generation.fetch_add(1" in restart
    assert restart.index("generation.fetch_add(1") < restart.index("child.kill()")
    terminated = src.split("CommandEvent::Terminated(payload) =>")[1].split("spawn_sidecar(")[0]
    assert "generation.load(Ordering::SeqCst) != my_generation" in terminated
    assert "break;" in terminated
    assert "intentional_stop" not in src


def test_a_malformed_face_row_never_becomes_a_new_person():
    from bpp.constants import CLUSTER_UNASSIGNED
    from bpp.db.face_identity_remap import _assign_new_faces

    rng = np.random.default_rng(3)
    a = rng.normal(size=128).astype(np.float32)
    unassigned = [
        ("/p1.jpg", 1, 0, a, 200, 0.9),
        ("/p2.jpg", 2, 0, a + 0.001, 200, 0.9),
        ("/p3.jpg", 3, 0, np.zeros(256, np.float32), 200, 0.9),
    ]
    labels = _assign_new_faces([], unassigned, threshold=0.6)
    assert labels[2] == CLUSTER_UNASSIGNED
    assert labels[0] == labels[1] and labels[0] != CLUSTER_UNASSIGNED


def test_clear_library_re_arms_the_hash_backfill(tmp_path):
    from bpp.web.app import create_app

    lib = tmp_path / "lib"
    (lib / "photos").mkdir(parents=True)
    app = create_app(workdir=str(lib / "data"), library_path=str(lib))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    resp = app.test_client().delete("/api/v1/library", json={"confirmation": "delete"})
    assert resp.status_code == 200
    assert not ctx.analysis_store.phash_cancel.is_set(), "the next backfill would cancel at 0/N"


def test_a_database_recreated_at_a_known_path_gets_its_tables_again(tmp_path):
    from bpp.db import connection

    db = str(tmp_path / "t.db")
    conn = connection.init_db(db)
    assert conn.execute("SELECT count(*) FROM photos").fetchone()[0] == 0
    connection.close_all_connections()
    os.remove(db)
    conn = connection.init_db(db)
    assert conn.execute("SELECT count(*) FROM photos").fetchone()[0] == 0


def test_a_failed_export_write_leaves_no_part_file(tmp_path, monkeypatch):
    from PIL import Image

    from bpp.output import export_modes

    src = tmp_path / "p.jpg"
    Image.new("RGB", (40, 30), "red").save(src, "JPEG")
    out = tmp_path / "out"
    out.mkdir()
    real_replace = export_modes._replace_into

    def disk_full(tmp, dest):
        raise OSError(28, "No space left on device", dest)

    monkeypatch.setattr(export_modes, "_replace_into", disk_full)
    with pytest.raises(OSError):
        export_modes._process_image(str(src), str(out / "e.jpg"), "jpeg", None, 80)
    with pytest.raises(OSError):
        export_modes._export_copy(str(src), str(out / "c.jpg"))
    assert [n for n in os.listdir(out) if ".part" in n] == []
    monkeypatch.setattr(export_modes, "_replace_into", real_replace)


def test_a_refused_switch_releases_the_lock_it_took(tmp_path, monkeypatch):
    from bpp.utils.serving_lock import read_lock_pid_if_alive
    from bpp.web import state_init_phases
    from bpp.web.app import create_app

    a = tmp_path / "A"
    b = tmp_path / "B"
    (a / "photos").mkdir(parents=True)
    (b / "photos").mkdir(parents=True)
    app = create_app(workdir=str(a / "data"), library_path=str(a))
    ctx = app.extensions["bpp"]
    real = state_init_phases.backup_or_refuse_corrupt

    def refuse_b(db_p, wd):
        if wd == str(b / "data"):
            raise RuntimeError("Database appears to be corrupt")
        return real(db_p, wd)

    monkeypatch.setattr("bpp.web.state_init._backup_or_refuse_corrupt", refuse_b)
    with pytest.raises(RuntimeError):
        ctx.switch_library(str(b))
    assert read_lock_pid_if_alive(str(b / "data")) is None, "B still looks served by us"
    assert ctx.state["library_path"] == str(a)
