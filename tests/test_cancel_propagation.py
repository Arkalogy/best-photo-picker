"""P1.6 — cross-process cancellation: real subprocesses, real cancel.

These tests stand opposite to the in-process unit tests in
``tests/test_cancel.py``. They spawn actual child processes and confirm:

1. Setting a :class:`ProcessCancellation` from the parent halts the
   scoring subprocess before it finishes a backlog of images.
2. The same token signature halts ``run_face_extraction_subprocess`` at
   the next chunk boundary (the audit-found gap before P1).
3. Passing a ``threading.Event`` (raw) still works via ``as_token`` —
   back-compat for callers that haven't migrated yet.
4. No child PIDs survive past the parent runner returning.

Performance: each test spawns 1-2 real subprocesses, each of which
loads model files from cache. Budget: ~10-15 s per test on a warm
cache, ~30 s cold. We use the smallest possible image counts that
still exercise the cancellation path.
"""

from __future__ import annotations

import multiprocessing
import os
import tempfile
import threading
import time

import pytest

from bpp.utils.cancel import ProcessCancellation, ThreadCancellation

# Mid-flight cancel asserts a wall-clock bound (cancel propagates within
# 60s). That only holds when the scoring child reaches its between-photos
# cancel poll quickly. On CI free runners model-load alone exceeds 3 min,
# so the child sits in startup well past 60s before it can observe the
# flag — the cancel still propagates, just not inside the bound. Same
# slow-startup pathology the AnalyzeWorker tests skip on CI. Runs
# deterministically locally (<60s on a warm cache).
_SKIP_SLOW_ANALYZE = pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="Scoring subprocess startup exceeds 3 min on CI free runners",
)


def _make_test_images(n: int = 5) -> list[str]:
    """Create N tiny synthetic JPEGs in a temp dir."""
    import numpy as np

    try:
        import cv2
    except ImportError:
        pytest.skip("cv2 required")

    tmpdir = tempfile.mkdtemp(prefix="bpp_cancel_test_")
    paths: list[str] = []
    for i in range(n):
        img = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
        path = os.path.join(tmpdir, f"test_{i}.jpg")
        cv2.imwrite(path, img)
        paths.append(path)
    return paths


def _pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ── Scoring subprocess ──


class TestCancelPropagatesToScoring:
    def test_pre_cancelled_token_returns_early(self):
        """A token already set before launch must short-circuit the child."""
        from bpp.web.analyze_worker import run_scoring_subprocess

        images = _make_test_images(3)
        token = ProcessCancellation()
        token.set()

        results, child_pid = run_scoring_subprocess(
            images=images,
            config={"max_long_side": 256},
            db_path=os.path.join(tempfile.mkdtemp(), "cache.db"),
            cancel_event=token,
        )
        # Pre-cancelled child sees the flag on its first poll and exits
        # before scoring anything. Empty results is the expected shape.
        assert isinstance(results, list)
        assert len(results) == 0, (
            f"Pre-cancelled child must produce 0 results, got {len(results)}: {results}"
        )
        assert not _pid_alive(child_pid), f"Child {child_pid} still alive after cancelled scoring"

    def test_raw_mp_event_back_compat(self):
        """Legacy callers still pass raw ``multiprocessing.Event``."""
        from bpp.web.analyze_worker import run_scoring_subprocess

        images = _make_test_images(2)
        cancel = multiprocessing.Event()
        cancel.set()

        results, child_pid = run_scoring_subprocess(
            images=images,
            config={"max_long_side": 256},
            db_path=os.path.join(tempfile.mkdtemp(), "cache.db"),
            cancel_event=cancel,
        )
        assert isinstance(results, list)
        assert not _pid_alive(child_pid)

    def test_threading_event_bridged_to_child(self):
        """A pre-set ``threading.Event`` from a different thread is
        bridged into the spawn child via ``mirror_token_to_process_event``.
        The child must see the flag and exit early.
        """
        from bpp.web.analyze_worker import run_scoring_subprocess

        images = _make_test_images(2)
        thread_evt = threading.Event()
        thread_evt.set()  # pre-cancel before launch

        results, child_pid = run_scoring_subprocess(
            images=images,
            config={"max_long_side": 256},
            db_path=os.path.join(tempfile.mkdtemp(), "cache.db"),
            cancel_event=thread_evt,
        )
        assert isinstance(results, list)
        assert not _pid_alive(child_pid)


# ── Face extraction subprocess ──


class TestCancelPropagatesToFaceExtraction:
    """The audit's load-bearing case: face extraction silently ignored
    cancel before P1. These tests are the regression gate."""

    def test_pre_cancelled_token_halts_at_first_chunk(self, tmp_path):
        """A pre-set cancel token must stop the chunk loop before
        spawning a single chunk subprocess. The runner returns 0 faces
        without paying any model-load cost."""
        from bpp.web.analyze_face_extract import run_face_extraction_subprocess

        # Build a dummy with_faces list — paths don't need to exist
        # because we never reach the photo-decode phase.
        with_faces = [{"filepath": f"/tmp/fake_{i}.jpg", "face_count": 1} for i in range(5)]
        token = ProcessCancellation()
        token.set()  # pre-cancel

        # The runner short-circuits before launching a chunk
        # subprocess; we still need a valid db_path argument shape.
        db_path = str(tmp_path / "cache.db")
        faces_found, face_clusters, child_pid = run_face_extraction_subprocess(
            with_faces=with_faces,
            config={"max_long_side": 256, "_face_extract_workers": 1},
            db_path=db_path,
            cancel_event=token,
        )
        assert faces_found == 0, f"Pre-cancel must yield 0 faces; got {faces_found}"
        assert face_clusters == 0
        # When the chunk loop short-circuits before launching any chunk,
        # child_pid is None — no subprocess ever started.
        assert child_pid is None, (
            f"Pre-cancel must short-circuit before chunk spawn; got pid={child_pid}"
        )

    def test_threading_event_back_compat(self, tmp_path):
        """``BackgroundWorker._cancelled`` is a ``threading.Event`` —
        the runner must accept it transparently."""
        from bpp.web.analyze_face_extract import run_face_extraction_subprocess

        with_faces = [{"filepath": f"/tmp/fake_{i}.jpg", "face_count": 1} for i in range(3)]
        thread_evt = threading.Event()
        thread_evt.set()

        faces_found, face_clusters, child_pid = run_face_extraction_subprocess(
            with_faces=with_faces,
            config={"max_long_side": 256, "_face_extract_workers": 1},
            db_path=str(tmp_path / "cache.db"),
            cancel_event=thread_evt,
        )
        assert faces_found == 0
        assert face_clusters == 0
        assert child_pid is None

    def test_empty_input_returns_immediately(self, tmp_path):
        """Belt check: even with no cancel, empty input returns
        cleanly. This is the trivial baseline that the cancel paths
        must match in shape."""
        from bpp.web.analyze_face_extract import run_face_extraction_subprocess

        faces_found, face_clusters, child_pid = run_face_extraction_subprocess(
            with_faces=[],
            config={"max_long_side": 256},
            db_path=str(tmp_path / "cache.db"),
        )
        assert (faces_found, face_clusters, child_pid) == (0, 0, None)


# ── End-to-end: mid-flight cancel ──


class TestMidFlightCancel:
    """The realistic cancel scenario: user clicks Cancel while the
    subprocess is running. Token fires AFTER launch, not before.

    We test via a small image set because waiting for a full 5,000-photo
    run in CI is impractical; the goal here is "cancel actually
    propagates" not "performance under load."
    """

    @_SKIP_SLOW_ANALYZE
    def test_scoring_cancel_after_launch_eventually_stops(self):
        """A cancel set from a parallel thread mid-flight halts scoring.

        The child polls the cancel event between photos, so the parent
        sees the runner return after at most ~one photo's worth of work.
        """
        from bpp.web.analyze_worker import run_scoring_subprocess

        images = _make_test_images(8)
        token = ProcessCancellation()

        def _fire_after_delay():
            time.sleep(0.5)
            token.set()

        t = threading.Thread(target=_fire_after_delay, daemon=True)
        t.start()

        t0 = time.monotonic()
        _results, child_pid = run_scoring_subprocess(
            images=images,
            config={"max_long_side": 256},
            db_path=os.path.join(tempfile.mkdtemp(), "cache.db"),
            cancel_event=token,
        )
        elapsed = time.monotonic() - t0

        # Generous bound — 8 tiny images even fully scored should take
        # under ~30 s on the slowest CI runner. We're not asserting
        # speed-up here, just that the runner returns and the child is gone.
        assert elapsed < 60, (
            f"Cancel did not propagate within 60s — runner stuck for {elapsed:.1f}s"
        )
        assert not _pid_alive(child_pid), (
            f"Child {child_pid} survived cancel — runner returned but PID alive"
        )


# ── Smoke: ThreadCancellation token also flows through ──


def test_thread_cancellation_token_via_as_token():
    """A :class:`ThreadCancellation` passed in goes through
    ``as_token`` → no-op (pass-through) and then gets bridged to an
    ``mp.Event`` for the spawn child. End result: pre-set token = empty
    results, same shape as the ProcessCancellation case."""
    from bpp.web.analyze_worker import run_scoring_subprocess

    images = _make_test_images(2)
    token = ThreadCancellation()
    token.set()

    results, child_pid = run_scoring_subprocess(
        images=images,
        config={"max_long_side": 256},
        db_path=os.path.join(tempfile.mkdtemp(), "cache.db"),
        cancel_event=token,
    )
    assert isinstance(results, list)
    assert not _pid_alive(child_pid)
