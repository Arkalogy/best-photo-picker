"""Base class for background workers with progress queue and thread management."""

from __future__ import annotations

import contextlib
import errno
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from bpp.constants import WORKER_JOIN_TIMEOUT_S
from bpp.utils.logging import get_logger

log = get_logger(__name__)


# OSError errnos that mean "the source the worker was reading from
# vanished" — drive ejected, network share unmounted, file deleted
# under our feet. For these, the user can take a concrete recovery
# action (reconnect) — much more useful than "check server logs."
# Anything outside this set falls through to the generic message.
_SOURCE_VANISHED_ERRNOS: frozenset[int] = frozenset(
    {
        errno.ENODEV,  # no such device — drive ejected
        errno.ENOENT,  # no such file or directory — path removed
        errno.EIO,  # I/O error — drive went bad / network drop
        errno.ESTALE,  # stale NFS handle — NAS share remounted
    }
)


_PROGRESS_QUEUE_MAX = 1000
# Cap drop-warning logs to once per minute per worker. The queue
# fills when the SSE consumer disconnects or stalls — without rate
# limiting, a single stuck stream could write tens of thousands of
# lines/second to server.log on a hot analyze loop.
_DROP_LOG_INTERVAL_S = 60.0


#: Wraps every worker start (set by the in-app installer): raises while
#: an install runs, and keeps one from starting until this start is done
#: — pip swaps package files, and a worker loads packages as it goes.
def _no_guard(_worker: Any) -> contextlib.AbstractContextManager[Any]:
    return contextlib.nullcontext()


_start_guard: Callable[[Any], contextlib.AbstractContextManager[Any]] = _no_guard


def start_guard(worker: Any) -> contextlib.AbstractContextManager[Any]:
    """The installer's guard for starting *worker* (None: work that may use
    any installable package)."""
    return _start_guard(worker)


def raise_if_blocked(worker: Any) -> None:
    """Raise ConflictError if *worker* couldn't start now (an install runs
    or waits for a restart): asked before slow or irreversible preparation
    (a model download, wiping data) rather than after it."""
    with _start_guard(worker):
        pass


def set_start_guard(guard: Callable[[Any], contextlib.AbstractContextManager[Any]]) -> None:
    global _start_guard
    _start_guard = guard


class BackgroundWorker:
    """Common boilerplate for background workers: thread lifecycle, queue, cancellation."""

    _worker_name: str = "Worker"
    #: Whether this work may import packages an in-app install writes, or
    #: libraries it upgrades (models, face/scipy code, numpy in the processes
    #: it starts): if so it doesn't start while an install runs, or after one
    #: before the restart. None (plugins' workers): unknown, so yes.
    loads_installable_packages: bool | None = None

    #: The app state that owns this worker (set by ``WebAppState`` when it
    #: builds the pool). Worker threads have no Flask context, so this is
    #: how an in-worker finalize reaches the caches it must refresh.
    ctx: Any = None

    def __init__(self) -> None:
        self.progress_queue: queue.Queue[dict[str, Any]] = queue.Queue(
            maxsize=_PROGRESS_QUEUE_MAX,
        )
        self._thread: threading.Thread | None = None
        self.running = False
        self._cancelled = threading.Event()
        # Drop-warning rate limiter: (last_log_time, drops_since_last_log)
        self._drop_log_state: tuple[float, int] = (0.0, 0)
        self._drop_log_lock = threading.Lock()
        # Last-activity timestamp (epoch seconds). Stamped at _safe_run
        # entry and on every _emit. Exposed via /api/v1/health so
        # operators can distinguish a stuck worker (alive=True but
        # last_activity grew minutes-stale) from a slow-but-progressing
        # one. 0.0 means "never active in this process".
        self._last_activity: float = 0.0

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _start_thread(self, *args: Any) -> bool:
        """Prepare and launch the background thread.

        Subclasses should expose a typed ``start()`` that validates arguments
        and delegates to ``_start_thread(*args)``.  Returns False if the
        worker is already running; raises ConflictError while an in-app
        install it may load runs, or after one until a restart (see
        ``loads_installable_packages``).
        """
        if self.is_alive:
            return False  # already running: that, not an install, is the answer
        with _start_guard(self):
            return self._start_thread_unguarded(*args)

    def _start_thread_unguarded(self, *args: Any) -> bool:
        if self.is_alive:
            return False

        self.running = True
        self._cancelled.clear()

        # Drain any stale progress messages
        while not self.progress_queue.empty():
            try:
                self.progress_queue.get_nowait()
            except queue.Empty:
                break

        self._thread = threading.Thread(
            target=self._safe_run,
            args=args,
            daemon=True,
        )
        self._thread.start()
        return True

    def cancel(self) -> bool:
        """Request cancellation.  Returns True if a running worker was cancelled."""
        if not self.is_alive:
            return False
        self._cancelled.set()
        return True

    def cancel_and_join(self, timeout: float = WORKER_JOIN_TIMEOUT_S) -> None:
        """Cancel and wait for the worker thread to finish.

        Logs a warning if the thread is still alive after *timeout* seconds.
        Safe to call even when the worker is not running.
        """
        if not self.is_alive:
            return
        self.cancel()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        if self.is_alive:
            # Swallow logging-during-interpreter-shutdown races. The
            # worker thread is daemon and will be terminated regardless.
            # The warning is informational, not load-bearing.
            with contextlib.suppress(ValueError, OSError):
                log.warning(
                    "%s did not stop within %.1fs, proceeding anyway",
                    self._worker_name,
                    timeout,
                )

    def _emit(self, msg: dict[str, Any]) -> None:
        """Put a progress message, discarding oldest if the queue is full.

        A full queue means the SSE consumer (browser tab) is slow or
        disconnected. Drop the oldest event to keep the worker
        unblocked and append the new one. Rate-limited drop log so a
        future operator looking at "the progress bar froze" can tell
        from server.log whether messages are actually being lost vs
        the SSE stream being dead vs the worker stalled.
        """
        # Every progress emit refreshes the activity timestamp,
        # regardless of whether the message ultimately fits in the
        # queue. The intent is "the worker is doing work right now,"
        # not "an SSE consumer received an event."
        self._last_activity = time.time()
        try:
            self.progress_queue.put_nowait(msg)
            return
        except queue.Full:
            pass
        with contextlib.suppress(queue.Empty):
            self.progress_queue.get_nowait()
        with contextlib.suppress(queue.Full):
            self.progress_queue.put_nowait(msg)

        # Log at most once per _DROP_LOG_INTERVAL_S, bundling the
        # number of drops since the last log line so a stuck stream
        # produces one diagnostic line per minute, not 60k.
        now = time.monotonic()
        with self._drop_log_lock:
            last_t, drops = self._drop_log_state
            drops += 1
            if now - last_t >= _DROP_LOG_INTERVAL_S:
                log.warning(
                    "%s: progress queue full — dropped %d event(s) in the last %.0fs "
                    "(SSE consumer disconnected or slow?)",
                    self._worker_name,
                    drops,
                    now - last_t if last_t > 0 else _DROP_LOG_INTERVAL_S,
                )
                self._drop_log_state = (now, 0)
            else:
                self._drop_log_state = (last_t, drops)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _safe_run(self, *args: Any) -> None:
        """Wrapper around ``_run`` that guarantees cleanup."""
        # Startup breadcrumb: when a worker hangs (model download stall,
        # SQLite lock contention, etc.) the operator can confirm from
        # server.log alone that the worker actually started and didn't
        # silently fail at registration time.
        self._last_activity = time.time()
        log.info("%s started", self._worker_name)
        try:
            self._run(*args)
        except OSError as e:
            # don't emit raw OSError text — its `str(e)` bakes
            # in the absolute path of whatever file/dir the OS call
            # was operating on, leaking the owner's filesystem layout
            # to LAN clients via the SSE progress stream. The full
            # traceback is in server.log for diagnosis.
            log.error("%s failed (filesystem error)", self._worker_name, exc_info=True)
            if e.errno in _SOURCE_VANISHED_ERRNOS:
                # SD card ejected / network share dropped / source file
                # deleted — give the user a concrete recovery action
                # instead of pointing them at server logs they can't
                # read.
                msg = (
                    f"{self._worker_name} stopped: the source folder "
                    "became inaccessible (drive ejected, network share "
                    "dropped, or file was deleted). Reconnect and try "
                    "again."
                )
            else:
                msg = f"{self._worker_name} encountered a filesystem error. Check server logs."
            self._emit({"type": "error", "message": msg})
        except Exception as e:
            msg = _install_wait_message(self._worker_name, e)
            if msg is not None:  # expected (installed, restart pending): no crash
                log.info("%s waits for a restart: %s", self._worker_name, e)
            else:
                log.error("%s failed", self._worker_name, exc_info=True)
                msg = f"{self._worker_name} failed. Check server logs for details."
            self._emit({"type": "error", "message": msg})
        finally:
            self.running = False
            try:
                from bpp.db.connection import close_all_connections

                close_all_connections()
            except Exception:
                # include exc_info so we can
                # diagnose connection-close hangs from server.log
                # rather than the bare warning that used to be here.
                log.warning(
                    "Failed to close DB connections in %s",
                    self._worker_name,
                    exc_info=True,
                )

    def _run(self, *args: Any) -> None:
        raise NotImplementedError


def _install_wait_message(worker: str, exc: BaseException) -> str | None:
    """A plain message when *exc* means "installed from the app, loads after
    a restart" (not a failure to dig into the logs for)."""
    from bpp.scoring.install_hold import HeldForRestart, still_installing

    # No package name: paired devices follow the same progress stream, and
    # what was installed is for the owner only.
    if isinstance(exc, HeldForRestart) and still_installing(exc):
        return f"{worker} stopped: a package is being installed — wait for it, then try again."
    if isinstance(exc, HeldForRestart):
        return (
            f"{worker} stopped: a package was just installed — restart Best Photo "
            "Picker, then try again."
        )
    return None
