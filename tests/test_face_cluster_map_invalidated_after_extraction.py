"""Every path that writes face clusters off a request thread invalidates
the filepath → cluster map through the app state it was handed; the
request-bound lookup finds nothing on a worker thread."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from tests.test_face_recovery_thread import _pending_run_with_one_face


def test_the_resume_invalidates_the_cluster_map(tmp_path, monkeypatch):
    from bpp.web.face_recovery import recover_pending_face_extractions

    app, ctx, _run_id = _pending_run_with_one_face(tmp_path)
    monkeypatch.setattr("bpp.web.face_worker.extract_and_cluster_faces", lambda *a, **k: (1, 1))
    calls: list = []
    monkeypatch.setattr(ctx, "invalidate_face_cluster_map", lambda: calls.append(1))
    with app.app_context():
        assert recover_pending_face_extractions(ctx.get_conn(), ctx=ctx, cancel_event=None) == 1
    assert calls == [1]


def test_the_analyze_worker_invalidates_after_its_face_phase(monkeypatch):
    from bpp.web import analyze_worker as aw

    calls: list = []
    ctx = SimpleNamespace(
        lock=threading.RLock(),
        state={"analysis": None},
        precompute_phashes=lambda valid: None,
        invalidate_face_cluster_map=lambda: calls.append(1),
    )
    monkeypatch.setattr(aw, "run_face_phase", lambda *a, **k: (2, 1))
    monkeypatch.setattr(aw, "run_clip_phase", lambda *a, **k: 0)
    worker = aw.AnalyzeWorker()
    worker.ctx = ctx
    counts: dict = {}
    worker._downstream_phases(
        None, [{"filepath": "/x/a.jpg"}], {"face_recognition_available": True}, counts
    )
    assert counts["faces_found"] == 2
    assert calls == [1]


def test_the_resume_invalidates_even_when_the_run_stops(tmp_path, monkeypatch):
    from bpp.errors import BppError
    from bpp.web.face_recovery import recover_pending_face_extractions

    app, ctx, _run_id = _pending_run_with_one_face(tmp_path)

    def stopped(*a, **k):
        raise BppError("model changed", user_message="Face analysis skipped")

    monkeypatch.setattr("bpp.web.face_worker.extract_and_cluster_faces", stopped)
    calls: list = []
    monkeypatch.setattr(ctx, "invalidate_face_cluster_map", lambda: calls.append(1))
    with app.app_context():
        recover_pending_face_extractions(ctx.get_conn(), ctx=ctx, cancel_event=None)
    assert calls == [1]


def test_the_analyze_worker_invalidates_even_when_the_face_phase_raises(monkeypatch):
    import pytest

    from bpp.web import analyze_worker as aw

    calls: list = []
    ctx = SimpleNamespace(
        lock=threading.RLock(),
        state={"analysis": None},
        precompute_phashes=lambda valid: None,
        invalidate_face_cluster_map=lambda: calls.append(1),
    )

    def boom(*a, **k):
        raise RuntimeError("child crashed")

    monkeypatch.setattr(aw, "run_face_phase", boom)
    worker = aw.AnalyzeWorker()
    worker.ctx = ctx
    with pytest.raises(RuntimeError):
        worker._downstream_phases(
            None, [{"filepath": "/x/a.jpg"}], {"face_recognition_available": True}, {}
        )
    assert calls == [1]


def test_the_face_worker_invalidates_after_its_subprocess(tmp_path, monkeypatch):
    from bpp.db.connection import init_db
    from bpp.web.face_worker import FaceWorker

    db_path = str(tmp_path / "t.db")
    init_db(db_path)
    monkeypatch.setattr(
        "bpp.web.analyze_face_extract.run_face_extraction_subprocess",
        lambda *a, **k: (1, 1, 123),
    )
    calls: list = []
    worker = FaceWorker()
    worker.ctx = SimpleNamespace(invalidate_face_cluster_map=lambda: calls.append(1))
    worker._run([{"id": 1, "filepath": str(tmp_path / "a.jpg"), "face_count": 1}], db_path, {})
    assert calls == [1]
