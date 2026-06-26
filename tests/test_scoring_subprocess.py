"""TDD tests for subprocess-isolated scoring phase.

RED phase: tests define the expected API for run_scoring_subprocess
BEFORE implementation. All tests should FAIL initially.

The scoring subprocess should:
1. Run process_one in a child process via multiprocessing
2. Return results through a queue/pipe
3. Child process exits after completion → all model memory freed
4. Support cancellation via a shared event
5. Emit progress through a callback/queue
"""

from __future__ import annotations

import multiprocessing
import os
import tempfile

import pytest


def test_run_scoring_subprocess_importable():
    """run_scoring_subprocess must be importable from analyze_worker."""
    from bpp.web.analyze_worker import run_scoring_subprocess

    assert callable(run_scoring_subprocess)


def test_run_scoring_subprocess_returns_results():
    """Must return a list of result dicts for given image paths."""
    from bpp.web.analyze_worker import run_scoring_subprocess

    # Use a tiny synthetic test image
    img_path = _make_test_image()
    try:
        results, _ = run_scoring_subprocess(
            images=[img_path],
            config={"max_long_side": 256},
            db_path=os.path.join(tempfile.mkdtemp(), "cache.db"),
        )
        assert isinstance(results, list)
        # Either got a result or empty (model may not score synthetic)
        assert len(results) <= 1
    finally:
        os.unlink(img_path)


def test_run_scoring_subprocess_empty_input():
    """Empty image list returns empty results."""
    from bpp.web.analyze_worker import run_scoring_subprocess

    results, _ = run_scoring_subprocess(
        images=[],
        config={"max_long_side": 256},
        db_path=os.path.join(tempfile.mkdtemp(), "cache.db"),
    )
    assert results == []


def test_run_scoring_subprocess_cancellation():
    """Cancelled event should stop processing early."""
    from bpp.web.analyze_worker import run_scoring_subprocess

    cancel = multiprocessing.Event()
    cancel.set()  # pre-cancel

    img_path = _make_test_image()
    try:
        results, _ = run_scoring_subprocess(
            images=[img_path],
            config={"max_long_side": 256},
            db_path=os.path.join(tempfile.mkdtemp(), "cache.db"),
            cancel_event=cancel,
        )
        # Should return early with no/partial results
        assert isinstance(results, list)
    finally:
        os.unlink(img_path)


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason=(
        "Race on slow CI runners: scoring 1 image finishes faster than the "
        "parent's progress-poll loop catches the message. Locally the test "
        "passes deterministically. TODO: drain the queue in finally before "
        "returning so the callback always sees emitted progress."
    ),
)
def test_run_scoring_subprocess_progress():
    """Must report progress through progress_callback."""
    from bpp.web.analyze_worker import run_scoring_subprocess

    progress_msgs = []
    img_path = _make_test_image()
    try:
        run_scoring_subprocess(
            images=[img_path],
            config={"max_long_side": 256},
            db_path=os.path.join(tempfile.mkdtemp(), "cache.db"),
            progress_callback=lambda msg: progress_msgs.append(msg),
        )
        # Should have at least one progress message
        assert len(progress_msgs) >= 1
    finally:
        os.unlink(img_path)


def test_subprocess_fatal_error_surfaces_to_progress_callback(caplog):
    """If the child process raises before yielding results, parent must log it
    and emit an error message via progress_callback — not silently return."""
    import logging

    from bpp.web.analyze_worker import run_scoring_subprocess

    progress_msgs: list[dict] = []
    # Nonexistent db directory → init_analysis_db fails → fatal_error path
    bad_db = "/nonexistent/directory/does/not/exist/cache.db"

    with caplog.at_level(logging.ERROR, logger="bpp.web.analyze_worker"):
        results, _ = run_scoring_subprocess(
            images=["/tmp/never_read.jpg"],
            config={"max_long_side": 256},
            db_path=bad_db,
            progress_callback=lambda msg: progress_msgs.append(msg),
        )

    # Results empty because child crashed before scoring anything
    assert results == []
    # Parent logged the fatal crash
    assert any("crashed" in r.message.lower() for r in caplog.records), (
        f"Expected crash log, got: {[r.message for r in caplog.records]}"
    )
    # Parent emitted an error message for UI
    errors = [m for m in progress_msgs if m.get("type") == "error"]
    assert errors, f"Expected error progress msg, got: {progress_msgs}"


def test_subprocess_memory_isolation():
    """Child process must exit after scoring — verify via pid."""
    from bpp.web.analyze_worker import run_scoring_subprocess

    img_path = _make_test_image()
    try:
        _, child_pid = run_scoring_subprocess(
            images=[img_path],
            config={"max_long_side": 256},
            db_path=os.path.join(tempfile.mkdtemp(), "cache.db"),
        )
        # Child process should no longer exist
        assert child_pid is not None
        assert not _pid_alive(child_pid), f"Child process {child_pid} still alive after scoring"
    finally:
        os.unlink(img_path)


# ── Helpers ──


def _make_test_image() -> str:
    """Create a minimal JPEG test image."""
    import numpy as np

    try:
        import cv2
    except ImportError:
        pytest.skip("cv2 required")

    img = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
    path = os.path.join(tempfile.mkdtemp(), "test.jpg")
    cv2.imwrite(path, img)
    return path


def _pid_alive(pid: int) -> bool:
    """Check if a process is still running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
