"""Unified cancellation contract — P1 of refactor-plan.md.

The audit found three incompatible cancellation styles in the codebase:

  * ``threading.Event`` in :class:`bpp.web.base_worker.BackgroundWorker._cancelled`
  * a separate ``multiprocessing.Event`` passed into subprocess children
    by ``run_scoring_subprocess``
  * a raw ``Callable[[], bool]`` parameter named ``cancellation_check``
    in :func:`bpp.web.face_worker.extract_and_cluster_faces`

And one outright gap:

  * :func:`bpp.web.analyze_face_extract.run_face_extraction_subprocess`
    does NOT propagate cancellation into its child. The chunk loop also
    has no cancel check between chunks. A user clicking Cancel during a
    5 000-photo face extraction stops seeing progress in the UI but the
    child process runs to completion.

This module defines the unified ``CancellationToken`` protocol and two
concrete implementations:

  * :class:`ThreadCancellation` — for in-process callers
    (``BackgroundWorker``, in-process workers, request handlers).
  * :class:`ProcessCancellation` — picklable, for subprocess children.

Plus a helper :func:`mirror_token_to_process_event` that bridges an
in-process token to a subprocess one via a daemon polling thread.
The bridge is what makes the "user clicks Cancel in the Flask request
thread → subprocess child sees it within a poll interval" pattern work
without each subprocess runner having to plumb it manually.

ADR: docs/adr/0002-cancellation-contract.md.
"""

from __future__ import annotations

import multiprocessing
import threading
import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    pass


@runtime_checkable
class CancellationToken(Protocol):
    """Read/write boolean signal that propagates across thread *and*
    process boundaries. Same shape as ``threading.Event`` and
    ``multiprocessing.Event``, but picklable across spawn boundaries
    when backed by the process implementation.

    Implementations: :class:`ThreadCancellation`, :class:`ProcessCancellation`.
    """

    def is_set(self) -> bool:
        """Return ``True`` if cancel has been signalled."""
        ...

    def set(self) -> None:
        """Signal cancel. Idempotent."""
        ...

    def wait(self, timeout: float | None = None) -> bool:
        """Block until set or *timeout* seconds elapse. Returns is_set()."""
        ...


class ThreadCancellation:
    """In-process cancellation token. Wraps :class:`threading.Event`.

    Use for: ``BackgroundWorker._cancelled``, request-handler ↔ in-thread
    worker, anything that stays in the same Python interpreter.
    """

    def __init__(self) -> None:
        self.event = threading.Event()

    def is_set(self) -> bool:
        return self.event.is_set()

    def set(self) -> None:
        self.event.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self.event.wait(timeout=timeout)


class ProcessCancellation:
    """Cross-process cancellation token. Wraps :class:`multiprocessing.Event`.

    Picklable — the parent constructs one, passes it into the spawn
    child via the subprocess runner args, and either side can call
    ``.set()`` to signal the other.

    Multiprocessing ``Event`` instances are bound to a context manager
    that must outlive the child. Construct from the same context as
    your :class:`multiprocessing.Process` (default: ``mp.get_context("spawn")``).
    """

    def __init__(self, ctx: multiprocessing.context.BaseContext | None = None) -> None:
        if ctx is None:
            # spawn is the production start method (matches analyze_scoring
            # and analyze_face_extract). Using the default global multiprocessing
            # bound Event here would break picklability through spawn.
            ctx = multiprocessing.get_context("spawn")
        self._event = ctx.Event()

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        self._event.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout=timeout)

    @property
    def event(self) -> multiprocessing.synchronize.Event:
        """Underlying ``multiprocessing.Event`` — exposed for back-compat
        with call sites that already take a raw ``mp.Event`` parameter
        (e.g. legacy ``run_scoring_subprocess(cancel_event=...)``).

        Prefer passing the token directly when writing new code.
        """
        return self._event


def mirror_token_to_process_event(
    source: CancellationToken,
    target: ProcessCancellation,
    poll_interval_s: float = 0.1,
    *,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Bridge an in-process ``CancellationToken`` to a ``ProcessCancellation``.

    Starts a daemon thread that polls ``source`` and ``.set()``s ``target``
    when the source fires. The thread exits as soon as it sets the target
    or the process dies.

    This is how :class:`bpp.web.base_worker.BackgroundWorker` (which signals
    cancel via a ``threading.Event``) reaches a spawn child that's reading
    a ``multiprocessing.Event``. Without it, every subprocess runner has
    to plumb the bridge itself — the audit found this gap in
    ``run_face_extraction_subprocess`` and adjacent flows.

    ``poll_interval_s`` defaults to 100 ms. Higher latency than that and
    the cancel button feels broken; lower and we waste CPU on a no-op
    polling thread.

    ``stop_event`` (T1.2) is the natural-completion signal. When the
    worker finishes WITHOUT a cancel, neither ``source`` nor ``target``
    ever fires, so the bridge thread polls forever — in a long-lived
    Flask parent process every successful subprocess run leaked a
    thread. Callers can pre-create a ``threading.Event`` and pass it
    here, then ``.set()`` it once the child has joined. The bridge
    exits on the next poll tick. ``daemon=True`` still ensures bridges
    die with the process; ``stop_event`` is the explicit-cleanup path
    so they die at the right moment in long-running parents.

    Returns the bridge thread so callers can ``.join()`` it for cleanup
    if needed. Most callers don't need to — it dies with the process.
    """

    def _bridge() -> None:
        while True:
            if source.is_set():
                target.set()
                return
            # Use the source's own wait() so the bridge exits the moment
            # the source fires, not after the next poll tick.
            if source.wait(timeout=poll_interval_s):
                target.set()
                return
            # As a safety net, also bail if the target was set from the
            # other end (e.g. the child completed naturally and the
            # runner cleaned up). Without this we'd burn CPU in a
            # finished-pipeline window.
            if target.is_set():
                return
            # T1.2: caller-driven natural-completion signal. Checked
            # AFTER the cancel paths above so a cancel that fires
            # concurrently with stop_event still propagates.
            if stop_event is not None and stop_event.is_set():
                return

    t = threading.Thread(
        target=_bridge,
        name="cancel-token-mirror",
        daemon=True,
    )
    t.start()
    return t


def make_pair(
    ctx: multiprocessing.context.BaseContext | None = None,
) -> tuple[ThreadCancellation, ProcessCancellation, threading.Thread]:
    """Build a (thread-side, process-side, bridge thread) tuple.

    Common pattern at the parent/child boundary: the request handler
    holds the thread token, the spawn child gets the process token,
    the bridge thread keeps them in sync. Returned bridge thread is
    already started.

    Example::

        thread_tok, process_tok, _bridge = make_pair()
        runner = BoundedSubprocessRunner(...)
        results = runner.run(input, process_tok, progress_cb=cb)
        # ... and from the Flask cancel handler:
        thread_tok.set()
    """
    thread_tok = ThreadCancellation()
    process_tok = ProcessCancellation(ctx=ctx)
    bridge = mirror_token_to_process_event(thread_tok, process_tok)
    return thread_tok, process_tok, bridge


# Backward-compat helpers for the existing code paths. Each accepts the
# old shape (raw ``multiprocessing.Event`` or ``threading.Event``) AND
# the new ``CancellationToken`` so migration is incremental.


def as_token(maybe_event_or_token: object) -> CancellationToken | None:
    """Coerce a legacy ``threading.Event`` / ``multiprocessing.Event``
    parameter into a :class:`CancellationToken` wrapper, or pass through
    a token unchanged. Returns ``None`` when the input is ``None``.

    Used by subprocess runners during the migration window so they can
    accept both shapes without breaking existing callers.
    """
    if maybe_event_or_token is None:
        return None
    if isinstance(maybe_event_or_token, (ThreadCancellation, ProcessCancellation)):
        return maybe_event_or_token  # already a token
    # Synthesize a thin wrapper around the raw Event without copying state.
    return _RawEventToken(maybe_event_or_token)


class _RawEventToken:
    """Adapter: wrap a raw ``threading.Event`` or ``multiprocessing.Event``
    so it satisfies the :class:`CancellationToken` protocol.

    Used only by :func:`as_token` for back-compat — new code should use
    :class:`ThreadCancellation` / :class:`ProcessCancellation` directly.
    """

    def __init__(self, event: object) -> None:
        self._event = event

    def is_set(self) -> bool:
        return bool(self._event.is_set())  # type: ignore[attr-defined]

    def set(self) -> None:
        self._event.set()  # type: ignore[attr-defined]

    def wait(self, timeout: float | None = None) -> bool:
        # ``multiprocessing.Event.wait`` returns the flag's state; some
        # legacy variants return None. Normalize.
        result = self._event.wait(timeout=timeout)  # type: ignore[attr-defined]
        if result is None:
            return self.is_set()
        return bool(result)


def sleep_or_cancel(token: CancellationToken | None, seconds: float) -> bool:
    """Sleep up to *seconds*, but return immediately if *token* fires.

    Returns ``True`` if cancelled, ``False`` if the sleep elapsed naturally.
    Useful inside chunk loops: ``if sleep_or_cancel(token, 0.5): return``.

    When *token* is ``None`` this degrades to a plain time.sleep().
    """
    if token is None:
        time.sleep(seconds)
        return False
    return token.wait(timeout=seconds)
