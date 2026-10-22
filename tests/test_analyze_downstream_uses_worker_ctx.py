"""The analyze worker's downstream phases reach the app state through
the worker's own ``ctx``, not the request-bound ``get_ctx``: on the
worker thread there is no request, so the derived-recovery job (hashes,
sidecars, duplicate clusters, Moments, smart albums) never started when
nobody was watching the progress stream."""

from __future__ import annotations

import threading
from types import SimpleNamespace


def test_downstream_phases_kick_recovery_through_the_worker_ctx(monkeypatch):
    from bpp.web import analyze_worker as aw

    kicked: list = []
    ctx = SimpleNamespace(
        lock=threading.RLock(),
        state={"analysis": None},
        precompute_phashes=lambda valid: kicked.append(list(valid)),
    )
    monkeypatch.setattr(aw, "run_face_phase", lambda *a, **k: (0, 0))
    monkeypatch.setattr(aw, "run_clip_phase", lambda *a, **k: 0)
    worker = aw.AnalyzeWorker()
    worker.ctx = ctx
    valid = [{"filepath": "/x/a.jpg", "phash": None, "ahash": None}]
    counts: dict = {}
    worker._downstream_phases(None, valid, {"model_clip": False}, counts)
    # The raw scoring dicts carry no row ids: the cache is dropped so
    # readers reload the stored rows instead.
    assert ctx.state["analysis"] is None
    assert kicked == [valid], "derived recovery must start without a request context"
