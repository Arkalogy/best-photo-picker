"""R8-H2: BackgroundWorker._safe_run() must not emit raw exception
text on the SSE progress stream.

`OSError.__str__` typically embeds the absolute path of whatever
file/dir the OS call was operating on:
  `[Errno 2] No such file or directory: '/Users/alice/.../file'`

The progress stream is consumed by any client polling the worker —
including paired LAN devices via the LAN-share gate. Leaking the
owner's filesystem layout into that stream contradicts the
LAN-shared-but-host-isolated trust model. R8-H2 changes both error
branches in `_safe_run` to emit a generic message, with the full
traceback logged to server.log (owner-only per R5-H2).
"""

from __future__ import annotations

import pytest

from bpp.web.base_worker import BackgroundWorker


class _OSErrorWorker(BackgroundWorker):
    """Worker whose `_run` always raises an OSError carrying an
    absolute filesystem path in its text. Worker name is set via
    the class-level `_worker_name` attribute (BackgroundWorker's
    pattern)."""

    _worker_name = "TestOSError"
    SECRET_PATH = "/Users/alice/Pictures/Private/Library/secret.jpg"

    def _run(self, *_args):
        raise OSError(f"[Errno 2] No such file or directory: '{self.SECRET_PATH}'")


class _GenericErrorWorker(BackgroundWorker):
    """Inverse: a non-OSError raises through the broader Exception
    branch, which is also generic-by-design."""

    _worker_name = "TestGeneric"
    SECRET_PATH = "/Users/alice/Pictures/Private/Library/other.jpg"

    def _run(self, *_args):
        raise RuntimeError(f"runtime crash referencing {self.SECRET_PATH}")


@pytest.fixture
def emitted_events():
    return []


def test_oserror_does_not_leak_path_to_progress_stream(emitted_events):
    """The exploit: OSError text → SSE progress stream → LAN client.
    After R8-H2 the emitted message is generic and the path stays in
    the (owner-only) server.log."""
    worker = _OSErrorWorker()
    worker._emit = emitted_events.append  # type: ignore[method-assign]

    worker._safe_run()

    error_events = [e for e in emitted_events if e.get("type") == "error"]
    assert error_events, "OSError must produce an error event"
    msg = error_events[0]["message"]
    assert _OSErrorWorker.SECRET_PATH not in msg
    assert "/Users/alice" not in msg
    assert "[Errno 2]" not in msg
    assert "No such file" not in msg
    # Generic message still names the worker so users can correlate
    # the error to which subsystem failed
    assert "TestOSError" in msg


def test_generic_exception_also_does_not_leak(emitted_events):
    """Inverse: the broader Exception branch was already generic
    before R8-H2; lock that contract so a future refactor doesn't
    regress it."""
    worker = _GenericErrorWorker()
    worker._emit = emitted_events.append  # type: ignore[method-assign]

    worker._safe_run()

    error_events = [e for e in emitted_events if e.get("type") == "error"]
    assert error_events
    msg = error_events[0]["message"]
    assert _GenericErrorWorker.SECRET_PATH not in msg
    assert "TestGeneric" in msg


# ---------------------------------------------------------------
# Source-vanished friendly-message branch (M5 follow-up)
# ---------------------------------------------------------------
#
# The base OSError handler protects the LAN-client path-leak case
# above. M5 adds a second responsibility: when the errno tells us the
# source disappeared (SD card ejected, NAS share unmounted, file
# deleted), the toast should tell the user what to do ("reconnect and
# try again") instead of pointing them at server logs they can't read.
# Anything outside the source-vanished set falls back to the generic
# 'check logs' message — verify that branch stays intact.


import errno  # noqa: E402 — kept near the section it documents


def _worker_for_errno(err: int) -> BackgroundWorker:
    """Build a single-shot worker whose _run raises OSError(err)."""

    class _Errno(BackgroundWorker):
        _worker_name = "TestErrno"

        def _run(self, *_args):
            raise OSError(err, "synthetic")

    return _Errno()


@pytest.mark.parametrize(
    "err",
    [errno.ENODEV, errno.ENOENT, errno.EIO, errno.ESTALE],
)
def test_source_vanished_errnos_get_friendly_message(emitted_events, err):
    """Each errno in the source-vanished set triggers the recoverable
    'reconnect and try again' message — the user gets an actionable
    next step instead of 'check server logs'."""
    worker = _worker_for_errno(err)
    worker._emit = emitted_events.append  # type: ignore[method-assign]
    worker._safe_run()

    error_events = [e for e in emitted_events if e.get("type") == "error"]
    assert error_events, f"errno={err} must produce an error event"
    msg = error_events[0]["message"]
    assert "Reconnect and try again" in msg, (
        f"errno={err} should use the friendly source-vanished message; got: {msg!r}"
    )
    assert "check server logs" not in msg.lower(), (
        f"errno={err} must not fall through to the generic 'check logs' message"
    )


@pytest.mark.parametrize(
    "err",
    [errno.EACCES, errno.EPERM, errno.ENOSPC, errno.EDQUOT],
)
def test_other_oserror_errnos_fall_back_to_generic(emitted_events, err):
    """Errnos outside the source-vanished set keep the generic
    'filesystem error / check server logs' message — the friendly
    branch is opt-in by errno, not a blanket override."""
    worker = _worker_for_errno(err)
    worker._emit = emitted_events.append  # type: ignore[method-assign]
    worker._safe_run()

    error_events = [e for e in emitted_events if e.get("type") == "error"]
    msg = error_events[0]["message"]
    assert "filesystem error" in msg.lower(), (
        f"errno={err} should keep the generic message; got: {msg!r}"
    )
    assert "Reconnect" not in msg, f"errno={err} must not borrow the source-vanished friendly text"


class TestCancellationEvent:
    """_cancelled must be a threading.Event (atomic set/clear/is_set)."""

    def test_cancelled_is_threading_event(self):
        import threading

        w = _OSErrorWorker()
        assert isinstance(w._cancelled, threading.Event), (
            "_cancelled must be threading.Event, not bool"
        )

    def test_cancel_sets_event(self):
        w = _OSErrorWorker()
        assert not w._cancelled.is_set()
        w._cancelled.set()
        assert w._cancelled.is_set()

    def test_start_thread_clears_event(self):
        """_start_thread() must clear the event so a restarted worker is not pre-cancelled."""
        w = _OSErrorWorker()
        w._cancelled.set()
        w._start_thread()
        assert not w._cancelled.is_set(), "_start_thread() must clear the cancellation event"
        if w._thread:
            w._thread.join(timeout=2)
