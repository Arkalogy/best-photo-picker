"""A face extraction resuming after a restart owns the face tables.

It runs on its own thread, so analyze, import and every face-mutating
endpoint must refuse (409) while it runs, and it holds the face lock so
a mutation already in flight finishes before the resume touches rows.
"""

from __future__ import annotations

import threading

import pytest
from PIL import Image


def _app_with_slow_recovery(tmp_path, monkeypatch):
    from bpp.web import face_extraction_journal as journal
    from bpp.web.app import create_app
    from bpp.web.state_lifecycle import _start_face_recovery

    release = threading.Event()
    started = threading.Event()

    def slow_recovery(conn, *, ctx=None, cancel_event=None):
        started.set()
        release.wait(10)
        return 0

    monkeypatch.setattr("bpp.web.face_worker.recover_pending_face_extractions", slow_recovery)
    lib = str(tmp_path / "lib")
    app = create_app(library_path=lib)
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    with app.app_context():
        journal.start_run(ctx.get_conn())
    _start_face_recovery(ctx)
    assert started.wait(5)
    return app, ctx, release


def _finish(ctx, release):
    release.set()
    ctx.analysis_store.face_recovery_thread.join(timeout=5)


def test_analyze_and_import_refuse_while_the_resume_runs(tmp_path, monkeypatch):
    app, ctx, release = _app_with_slow_recovery(tmp_path, monkeypatch)
    try:
        src = tmp_path / "src"
        src.mkdir()
        Image.new("RGB", (8, 8)).save(src / "a.jpg", "JPEG")
        with app.test_client() as c:
            r = c.post("/api/v1/analyze", json={"input_dir": str(src)})
            assert r.status_code == 409, r.get_data(as_text=True)
            assert "resuming" in r.get_json()["error"]
            r = c.post("/api/v1/import", json={"source_dir": str(src)})
            assert r.status_code == 409, r.get_data(as_text=True)
            assert "resuming" in r.get_json()["error"]
    finally:
        _finish(ctx, release)


def test_face_mutations_refuse_while_the_resume_runs(tmp_path, monkeypatch):
    app, ctx, release = _app_with_slow_recovery(tmp_path, monkeypatch)
    try:
        with app.test_client() as c:
            for path in ("/api/v1/faces/merge", "/api/v1/faces/dismiss", "/api/v1/faces/recluster"):
                r = c.post(path, json={})
                assert r.status_code == 409, (path, r.get_data(as_text=True))
                assert "resuming" in r.get_json()["error"], path
    finally:
        _finish(ctx, release)


def test_the_resume_holds_the_face_lock_while_it_runs(tmp_path, monkeypatch):
    _app, ctx, release = _app_with_slow_recovery(tmp_path, monkeypatch)
    try:
        assert ctx.face_op_lock.acquire(blocking=False) is False, "lock free during the resume"
    finally:
        _finish(ctx, release)
    assert ctx.face_op_lock.acquire(blocking=False) is True
    ctx.face_op_lock.release()


def test_the_resume_waits_for_a_mutation_already_holding_the_lock(tmp_path, monkeypatch):
    import time

    from bpp.web import face_extraction_journal as journal
    from bpp.web.app import create_app
    from bpp.web.state_lifecycle import _start_face_recovery

    started = threading.Event()
    monkeypatch.setattr(
        "bpp.web.face_worker.recover_pending_face_extractions",
        lambda conn, *, ctx=None, cancel_event=None: started.set() or 0,
    )
    app = create_app(library_path=str(tmp_path / "lib"))
    ctx = app.extensions["bpp"]
    with app.app_context():
        journal.start_run(ctx.get_conn())
    ctx.face_op_lock.acquire()
    try:
        _start_face_recovery(ctx)
        time.sleep(0.3)
        assert not started.is_set(), "the resume touched rows while a mutation held the lock"
    finally:
        ctx.face_op_lock.release()
    assert started.wait(5)
    ctx.analysis_store.face_recovery_thread.join(5)


def test_the_resume_defers_to_a_running_face_worker(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from bpp.web import face_extraction_journal as journal
    from bpp.web.app import create_app
    from bpp.web.state_lifecycle import _start_face_recovery

    monkeypatch.setattr(
        "bpp.web.face_worker.recover_pending_face_extractions",
        lambda *a, **k: pytest.fail("must not resume beside a running face worker"),
    )
    app = create_app(library_path=str(tmp_path / "lib"))
    ctx = app.extensions["bpp"]
    with app.app_context():
        journal.start_run(ctx.get_conn())
    monkeypatch.setattr(
        type(ctx), "face_worker", property(lambda self: SimpleNamespace(is_alive=True))
    )
    _start_face_recovery(ctx)
    assert ctx.analysis_store.face_recovery_thread is None
    assert ctx.analysis_store.face_recovery_running is False


def test_a_resume_that_waited_across_a_library_switch_does_not_run(tmp_path, monkeypatch):
    from bpp.web import face_extraction_journal as journal
    from bpp.web.app import create_app
    from bpp.web.state_lifecycle import _start_face_recovery

    monkeypatch.setattr(
        "bpp.web.face_worker.recover_pending_face_extractions",
        lambda *a, **k: pytest.fail("ran against the next library"),
    )
    app = create_app(library_path=str(tmp_path / "lib"))
    ctx = app.extensions["bpp"]
    with app.app_context():
        journal.start_run(ctx.get_conn())
    ctx.face_op_lock.acquire()
    try:
        _start_face_recovery(ctx)
        t = ctx.analysis_store.face_recovery_thread
        # the switch: join gave up, the store moved on to another library
        ctx.analysis_store.face_recovery_thread = None
        ctx.analysis_store.bump_generation_and_reset_phash()
    finally:
        ctx.face_op_lock.release()
    t.join(5)
    assert not t.is_alive()


def test_a_timed_out_lock_during_the_resume_names_the_resume(tmp_path, monkeypatch):
    from bpp.errors import ConflictError
    from bpp.web import face_lock

    monkeypatch.setattr(face_lock, "FACE_LOCK_TIMEOUT_S", 0.1)
    app, ctx, release = _app_with_slow_recovery(tmp_path, monkeypatch)
    try:

        @face_lock.with_face_lock
        def mutate():
            return "ran"

        with app.app_context(), pytest.raises(ConflictError, match="resuming"):
            mutate()
    finally:
        _finish(ctx, release)
