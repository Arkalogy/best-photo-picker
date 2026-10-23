"""Resuming an interrupted face extraction must not block startup.

The resume can take minutes on a large library. It used to run inline
in ``startup()``, so the server (and every library switch, which holds
the switch lock) sat silent for that long with no way to cancel. It now
runs on its own thread, shows in /api/v1/health, and is cancelled and
joined with the other store threads.
"""

from __future__ import annotations

import os
import threading
import time

import pytest


def _app_with_a_pending_run(tmp_path, monkeypatch, recovery):
    """An app whose journal holds one pending face-extraction run, with
    the startup hook re-run after seeding so the resume thread exists."""
    from bpp.web import face_extraction_journal as journal
    from bpp.web.app import create_app
    from bpp.web.state_lifecycle import _start_face_recovery

    monkeypatch.setattr("bpp.web.face_worker.recover_pending_face_extractions", recovery)
    app = create_app(library_path=str(tmp_path / "lib"))
    ctx = app.extensions["bpp"]
    with app.app_context():
        journal.start_run(ctx.get_conn())
    _start_face_recovery(ctx)
    return app


def test_startup_returns_while_recovery_runs_and_health_shows_it(tmp_path, monkeypatch):
    release = threading.Event()
    started = threading.Event()
    seen: dict = {}

    def slow_recovery(conn, *, ctx=None, cancel_event=None):
        seen["cancel_event"] = cancel_event
        seen["ctx"] = ctx
        started.set()
        release.wait(10)
        return 0

    t0 = time.monotonic()
    app = _app_with_a_pending_run(tmp_path, monkeypatch, slow_recovery)
    assert time.monotonic() - t0 < 5, "startup waited for the recovery"
    ctx = app.extensions["bpp"]
    store = ctx.analysis_store
    assert started.wait(5)
    assert store.face_recovery_running is True
    assert seen["cancel_event"] is store.face_recovery_cancel
    assert seen["ctx"] is ctx, "off a request the thread must hand the app state over"
    app.config["TESTING"] = True
    with app.test_client() as c:
        health = c.get("/api/v1/health").get_json()
    assert health["checks"]["face_recovery"] == {"failed": False, "in_flight": True}
    # A user-started extraction must not run on top of the resume.
    with app.test_client() as c:
        r = c.post("/api/v1/faces/retry")
    assert r.status_code == 409
    assert "resuming" in r.get_json()["error"]
    release.set()
    store.face_recovery_thread.join(timeout=5)
    assert store.face_recovery_running is False


def test_join_threads_cancels_and_waits_for_the_recovery(tmp_path, monkeypatch):
    def recovery(conn, *, ctx=None, cancel_event=None):
        cancel_event.wait(10)  # stops as soon as it is told to
        return 0

    app = _app_with_a_pending_run(tmp_path, monkeypatch, recovery)
    store = app.extensions["bpp"].analysis_store
    t = store.face_recovery_thread
    assert t is not None and t.is_alive()
    store.join_threads(timeout=5)
    assert not t.is_alive()
    assert store.face_recovery_thread is None


def test_a_crashed_recovery_is_degraded_health_not_silence(tmp_path, monkeypatch):
    def broken(conn, *, ctx=None, cancel_event=None):
        raise RuntimeError("journal unreadable")

    app = _app_with_a_pending_run(tmp_path, monkeypatch, broken)
    store = app.extensions["bpp"].analysis_store
    store.face_recovery_thread.join(timeout=5)
    assert store.face_recovery_failed is True
    app.config["TESTING"] = True
    with app.test_client() as c:
        health = c.get("/api/v1/health").get_json()
    assert health["checks"]["face_recovery"]["failed"] is True
    assert health["status"] == "degraded"


def test_a_thread_left_over_from_a_switch_does_not_touch_the_new_flags(tmp_path, monkeypatch):
    """join_threads gives up after a timeout; a recovery still running then
    must not write its outcome over the next library's flags."""
    release = threading.Event()

    def stuck(conn, *, ctx=None, cancel_event=None):
        release.wait(10)
        raise RuntimeError("late failure from the old library")

    app = _app_with_a_pending_run(tmp_path, monkeypatch, stuck)
    store = app.extensions["bpp"].analysis_store
    old = store.face_recovery_thread
    store.join_threads(timeout=0.2)  # too short: the old thread survives
    assert store.face_recovery_thread is old, "a live thread stays known until the switch"
    store.bump_generation_and_reset_phash()  # the switch disowns it
    assert store.face_recovery_thread is None
    store.face_recovery_running = True  # the next library's recovery is on
    store.face_recovery_failed = False
    release.set()
    old.join(timeout=5)
    assert store.face_recovery_running is True
    assert store.face_recovery_failed is False


def _pending_run_with_one_face(tmp_path):
    """An app whose library has one photo with a face and one pending
    face-extraction journal row."""

    from PIL import Image

    from bpp.db.photos import upsert_photo
    from bpp.web import face_extraction_journal as journal
    from bpp.web.app import create_app

    lib = str(tmp_path / "lib")
    os.makedirs(os.path.join(lib, "photos"))
    app = create_app(library_path=lib)
    ctx = app.extensions["bpp"]
    photo = os.path.join(lib, "photos", "a.jpg")
    Image.new("RGB", (8, 8), "gray").save(photo, "JPEG")
    with app.app_context():
        conn = ctx.get_conn()
        upsert_photo(
            conn,
            {
                "filepath": photo,
                "date": "2024-01-01T00:00:00",
                "blur_raw": 100.0,
                "blur_score": 0.5,
                "exposure_score": 0.5,
                "face_score": 0.5,
                "composition_score": 0.5,
                "aggregate_score": 0.5,
                "face_count": 1,
            },
        )
        conn.commit()
        ctx.invalidate_analysis()
        assert any((a.get("face_count") or 0) > 0 for a in ctx.load_analysis_if_needed())
        run_id = journal.start_run(conn)
    return app, ctx, run_id


def test_a_cancelled_resume_is_not_counted_and_stays_pending(tmp_path, monkeypatch):
    from bpp.web import face_extraction_journal as journal
    from bpp.web.face_recovery import recover_pending_face_extractions

    app, ctx, run_id = _pending_run_with_one_face(tmp_path)
    cancel = threading.Event()
    ran = []

    def interrupted(conn, with_faces, photo_map, *a, cancel_event=None, **kw):
        ran.append(len(with_faces))
        cancel_event.set()  # a switch or quit arrives mid-run
        return (0, 0)

    monkeypatch.setattr("bpp.web.face_worker.extract_and_cluster_faces", interrupted)
    with app.app_context():
        conn = ctx.get_conn()
        assert recover_pending_face_extractions(conn, ctx=ctx, cancel_event=cancel) == 0
        assert ran == [1], "the resume must have started"
        assert [r["run_id"] for r in journal.pending_runs(conn)] == [run_id]
        assert journal.get_retry_count(conn, run_id) == 0, "a cancel is not a failed attempt"


def test_a_cancel_before_the_run_costs_no_retry(tmp_path, monkeypatch):
    from bpp.web import face_extraction_journal as journal
    from bpp.web.face_recovery import recover_pending_face_extractions

    app, ctx, run_id = _pending_run_with_one_face(tmp_path)
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(
        "bpp.web.face_worker.extract_and_cluster_faces",
        lambda *a, **kw: pytest.fail("must not run after cancel"),
    )
    with app.app_context():
        conn = ctx.get_conn()
        assert recover_pending_face_extractions(conn, ctx=ctx, cancel_event=cancel) == 0
        assert journal.get_retry_count(conn, run_id) == 0
        assert [r["run_id"] for r in journal.pending_runs(conn)] == [run_id]


def test_nothing_pending_means_no_thread_and_no_in_flight_flag(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "bpp.web.face_worker.recover_pending_face_extractions",
        lambda conn, *, ctx=None, cancel_event=None: calls.append(1) or 0,
    )
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "lib"))
    store = app.extensions["bpp"].analysis_store
    assert store.face_recovery_thread is None
    assert store.face_recovery_running is False
    assert calls == []
