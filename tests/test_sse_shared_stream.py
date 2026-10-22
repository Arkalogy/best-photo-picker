"""One progress stream for every worker, and it is not destructive.

Each worker's progress went through one queue that the first SSE client
drained: a second tab, a reload or a paired phone watching the same run
took messages the other never saw, and the CLIP, face-extraction and
export streams never re-checked whether a paired device was still trusted
(only analyze and import did). Every stream now reads the worker's event
log — each reader sees every message — and re-checks trust the same way.
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from bpp.web.base_worker import BackgroundWorker
from bpp.web.sse import stream_worker_progress


class _Chatty(BackgroundWorker):
    _worker_name = "chatty"
    loads_installable_packages = False

    def __init__(self, n: int = 2):
        super().__init__()
        self.release = threading.Event()
        self.n = n

    def start(self):
        return self._start_thread()

    def _run(self, *_a):
        for i in range(1, self.n + 1):
            self._emit({"type": "progress", "current": i})
        self.release.wait(5)
        self._emit({"type": "done"})


def _frames(gen):
    return [json.loads(f[len("data: ") :]) for f in gen if f.startswith("data: ")]


@pytest.fixture
def worker():
    w = _Chatty()
    assert w.start()
    yield w
    w.release.set()
    w._thread.join(timeout=5)


def test_two_readers_both_see_every_message(worker):
    worker.release.set()
    a = _frames(stream_worker_progress(worker))
    b = _frames(stream_worker_progress(worker))
    want = [
        {"type": "progress", "current": 1},
        {"type": "progress", "current": 2},
        {"type": "done"},
    ]
    assert a == want
    assert b == want, "the second reader must not find the queue drained"


def test_a_reader_joining_late_still_gets_the_run_so_far(worker):
    worker._thread.join(timeout=0.5)  # the first two messages are out
    gen = stream_worker_progress(worker)
    first = json.loads(next(gen)[len("data: ") :])
    assert first == {"type": "progress", "current": 1}
    worker.release.set()
    rest = _frames(gen)
    assert rest[-1] == {"type": "done"}


def test_on_done_runs_once_per_run_however_many_readers(worker):
    worker.release.set()
    seen = []
    list(stream_worker_progress(worker, on_done=lambda m: seen.append(m)))
    list(stream_worker_progress(worker, on_done=lambda m: seen.append(m)))
    assert seen == [{"type": "done"}]


def test_a_cancelled_run_ends_every_stream_at_once():
    class _Cancels(_Chatty):
        def _run(self, *_a):
            self._emit({"type": "progress", "current": 1})
            self._emit({"type": "cancelled"})

    w = _Cancels()
    assert w.start()
    w._thread.join(timeout=5)
    frames = _frames(stream_worker_progress(w))
    assert frames[-1] == {"type": "cancelled"}


def test_a_revoked_device_is_cut_off(monkeypatch):
    monkeypatch.setattr("bpp.web.share_runtime.is_lan_sharing_enabled", lambda conn: True)
    monkeypatch.setattr("bpp.web.share_devices.is_device_trusted", lambda conn, fp: False)
    ctx = SimpleNamespace(get_conn=lambda: None)
    worker = _Chatty(n=8)  # enough messages to reach a trust re-check
    assert worker.start()
    worker.release.set()
    frames = _frames(stream_worker_progress(worker, ctx=ctx, device_fp="phone-1"))
    # Nothing is sent to a device that lost its standing, not even the
    # events already queued: the check runs before every batch.
    assert frames == [{"type": "error", "message": "auth_revoked"}]


def test_the_local_app_is_never_re_checked(worker, monkeypatch):
    def boom(conn, fp):
        raise AssertionError("the local app has no device to re-check")

    monkeypatch.setattr("bpp.web.share_devices.is_device_trusted", boom)
    worker.release.set()
    assert _frames(stream_worker_progress(worker, ctx=None, device_fp=None))[-1] == {"type": "done"}


def test_a_run_ending_right_after_a_quiet_wait_is_still_delivered(monkeypatch):
    """The worker's last message lands between the reader's wait timing
    out and its liveness check: the reader must still deliver it."""
    monkeypatch.setattr("bpp.web.sse.PROGRESS_QUEUE_TIMEOUT_S", 0.05)
    w = _Chatty(n=1)
    assert w.start()
    gen = stream_worker_progress(w)
    assert json.loads(next(gen)[6:]) == {"type": "progress", "current": 1}
    # The next wait times out (nothing new); then the worker finishes.
    nxt = next(gen)  # the keepalive after the quiet wait…
    assert json.loads(nxt[6:])["type"] == "keepalive" or json.loads(nxt[6:])["type"] == "done"
    w.release.set()
    w._thread.join(timeout=5)
    frames = [json.loads(nxt[6:]), *_frames(gen)]
    assert frames[-1] == {"type": "done"}
    assert {"type": "error", "message": "Worker stopped unexpectedly"} not in frames


def test_a_reader_still_on_the_old_run_gets_its_end_before_the_new_run():
    w = _Chatty(n=1)
    assert w.start()
    w.release.set()
    w._thread.join(timeout=5)
    gen = stream_worker_progress(w)
    assert json.loads(next(gen)[6:]) == {"type": "progress", "current": 1}
    # The next run starts while this reader is parked.
    w.release.clear()
    assert w.start()
    rest = _frames(gen)
    assert rest == [{"type": "done"}], "the first run's end, not the second run's start"
    w.release.set()
    w._thread.join(timeout=5)


def test_every_progress_route_uses_the_shared_stream():
    import pathlib

    web = pathlib.Path(__file__).resolve().parents[1] / "bpp" / "web"
    for name in ("bp_analysis.py", "bp_clip.py", "bp_faces_extract.py", "bp_export.py"):
        src = (web / name).read_text()
        assert "stream_worker_progress(" in src, name
        assert "progress_queue.get(" not in src, f"{name} drains the queue itself"
    assert "_stream_with_revoke_check" not in (web / "bp_analysis.py").read_text()
