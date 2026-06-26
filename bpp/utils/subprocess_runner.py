"""Bounded subprocess runner — P2 of refactor-plan.md.

The audit identified that ``run_scoring_subprocess`` and
``run_face_extraction_subprocess`` reimplement the same machinery from
scratch: queue setup, sentinel handling, fatal-error path, timeout,
graceful-then-force join, ``proc.exitcode`` post-mortem, cancel-event
shape normalization. Drift between the two has already caused real
bugs (P1's audit found face extraction silently dropping cancel — a
gap that scoring didn't have).

This module hoists that machinery into a generic
:class:`BoundedSubprocessRunner` parameterized by a
:class:`Phase` protocol. Each phase owns:

* the target function the child runs,
* how to build the child's argv (queue + cancel event come for free),
* an opaque accumulator (typed by ``O``) the runner threads through
  per-message reduction,
* a single reducer that turns each queue message into ``(new_state,
  optional_progress_msg_to_forward)``.

The runner owns everything else. Adding a Phase-4 worker becomes "write
a Phase class," not "duplicate 80 lines of drain-loop machinery."

ADR: docs/adr/0001-subprocess-runner.md.
"""

from __future__ import annotations

import multiprocessing
import queue
import threading
from collections.abc import Callable
from typing import Any, Generic, Protocol, TypeVar

from bpp.constants import (
    SUBPROCESS_FORCE_JOIN_S,
    SUBPROCESS_GRACEFUL_JOIN_S,
)
from bpp.utils.logging import get_logger

log = get_logger(__name__)

#: Queue sentinel. The child puts this last; the runner stops draining
#: when it sees it. Must be picklable AND identity-stable across spawn
#: (``object()`` is not — the child's re-imported module has a different
#: identity). A unique string is the simplest stable shape and can't
#: collide with real messages because phases never emit it.
SENTINEL = "__BPP_SUBPROCESS_SENTINEL__"


def _is_raw_mp_event(obj: object) -> bool:
    """True if *obj* is a raw ``multiprocessing.Event`` (any context).

    Same gotcha as ``analyze_scoring._is_raw_mp_event`` — the
    ``multiprocessing.synchronize`` submodule must be touched before
    ``isinstance`` against its ``Event`` class resolves.
    """
    import multiprocessing.synchronize as _mps

    return isinstance(obj, _mps.Event)


# Input / output type variables. ``I`` is whatever payload the phase
# consumes ("list of image paths" / "list of face dicts" / etc.);
# ``O`` is the accumulator the runner threads through, typed by the
# phase.
I = TypeVar("I")  # noqa: E741 — single-letter generics read fine in this context
O = TypeVar("O")  # noqa: E741


class Phase(Protocol[I, O]):
    """Contract a subprocess phase implements for :class:`BoundedSubprocessRunner`.

    Implementations supply the child entry point plus three small hooks
    the runner needs to drive the drain loop. The runner does not need
    to know what the phase computes — only how to ask for a fresh
    accumulator, how to reduce one message into it, and how to build
    the child's argv.
    """

    #: Human-readable phase name. Used in log lines so a tail of
    #: ``server.log`` distinguishes scoring vs. face extract vs. ...
    name: str

    def target(self) -> Callable[..., None]:
        """The function the child process executes.

        Called only in the child; the parent never invokes it. The
        runner sticks this into ``multiprocessing.Process(target=...)``.
        """
        ...

    def build_args(
        self,
        payload: I,
        result_queue: multiprocessing.Queue,
        cancel_event: multiprocessing.synchronize.Event,
    ) -> tuple[Any, ...]:
        """Build the positional args tuple for the child.

        Receives the runner-provided ``result_queue`` and ``cancel_event``
        already plumbed (callers don't construct them). The phase decides
        in what order they appear in its target's signature.
        """
        ...

    def initial_state(self) -> O:
        """Build a fresh accumulator for one ``run()`` call."""
        ...

    def reduce(self, state: O, msg: Any) -> tuple[O, dict[str, Any] | None]:
        """Fold one queue message into ``state``.

        Return ``(new_state, progress_msg)``. When ``progress_msg`` is
        not ``None``, the runner forwards it to the caller's
        ``progress_callback``. This split lets phases decide what's a
        result-bearing message vs. a progress tick without the runner
        having to know.
        """
        ...


class BoundedSubprocessRunner(Generic[I, O]):
    """Drive a :class:`Phase` in a memory-bounded subprocess.

    "Bounded" in three senses:

    * Per-message queue timeout (``message_timeout_s``) — a stuck child
      can't hang the parent forever.
    * Graceful-then-force join (``graceful_join_s`` → ``force_join_s``) —
      the parent always reaps within a known bound after the drain ends.
    * Cancel-event integration — every runner consults a token; never
      again the situation where one flow ignored cancel and another
      didn't (the P1 audit's load-bearing bug).

    The runner does NOT bound peak memory inside the child — that's
    each phase's responsibility (e.g. face-extraction chunks for
    bounded allocator growth).
    """

    def __init__(
        self,
        phase: Phase[I, O],
        *,
        message_timeout_s: float = 300.0,
        graceful_join_s: float = SUBPROCESS_GRACEFUL_JOIN_S,
        force_join_s: float = SUBPROCESS_FORCE_JOIN_S,
        daemon: bool = True,
    ) -> None:
        """Build a runner for one phase.

        ``message_timeout_s`` (default 5 min) bounds how long the parent
        will wait for the next message before declaring the child stuck
        and force-killing. Face extraction historically used 10 min
        because individual photo processing can take a while when models
        are cold; each call site can tune.

        ``daemon=False`` for face extraction — its child spawns a
        ProcessPool internally, and Python forbids daemonic processes
        from having children. Scoring is fine with ``daemon=True``.
        """
        self.phase = phase
        self.message_timeout_s = message_timeout_s
        self.graceful_join_s = graceful_join_s
        self.force_join_s = force_join_s
        self.daemon = daemon

    def run(
        self,
        payload: I,
        *,
        cancel_event: Any = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[O, int | None]:
        """Execute the phase in a child process. Returns ``(state, child_pid)``.

        ``state`` is whatever the phase's reducer accumulated. ``child_pid``
        is the terminated child's PID — useful for post-mortem memory
        checks. Both are returned even when the child crashes or times out
        so callers can react to partial work without an exception path.

        ``cancel_event`` accepts the unified P1 contract: a
        :class:`bpp.utils.cancel.CancellationToken`, a raw
        ``multiprocessing.Event``, a raw ``threading.Event``, or
        ``None``. The runner normalizes it into a picklable mp.Event
        for the child (bridging via mirror thread when needed) so the
        phase doesn't have to know.
        """
        # P1 cancel-event normalization — one place, all phases.
        # T1.2: ``bridge_stop`` is the natural-completion hint for any
        # mirror-bridge thread the normalization spun up. Set after the
        # child has joined so the bridge exits instead of polling
        # forever in this long-lived parent process.
        child_cancel_event, bridge_stop = self._prepare_cancel_event(cancel_event)

        result_queue: multiprocessing.Queue = multiprocessing.Queue()
        proc = multiprocessing.Process(
            target=self.phase.target(),
            args=self.phase.build_args(payload, result_queue, child_cancel_event),
            daemon=self.daemon,
        )
        proc.start()
        child_pid = proc.pid
        log.info("Phase %r started (pid=%s)", self.phase.name, child_pid)

        try:
            state, child_crashed = self._drain(result_queue, proc, progress_callback)

            # Graceful join, then force kill if needed. Force-kill window
            # is bounded by ``force_join_s`` — after that we accept the
            # leak (process listed as zombie until the OS reaps).
            proc.join(timeout=self.graceful_join_s)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=self.force_join_s)
        finally:
            # T1.2: tell the cancel-bridge thread (if any) the child is
            # done. ``try/finally`` covers the rare drain/kill exception
            # path too — without this, a raise from `_drain` would leak
            # the bridge.
            if bridge_stop is not None:
                bridge_stop.set()

        # Crash detection via exitcode. The drain loop catches the
        # explicit fatal_error path; this catches SIGKILL / SIGSEGV
        # that bypass it. Negative exitcode = signal; positive =
        # uncaught exception. Either is a crash from our perspective.
        if proc.exitcode is not None and proc.exitcode != 0 and not child_crashed:
            log.warning(
                "Phase %r child exited abnormally (pid=%s, exitcode=%s) — "
                "treating as crashed. Any work committed before the crash is kept.",
                self.phase.name,
                child_pid,
                proc.exitcode,
            )
            child_crashed = True

        log.info(
            "Phase %r done (pid=%s, exitcode=%s, crashed=%s)",
            self.phase.name,
            child_pid,
            proc.exitcode,
            child_crashed,
        )
        return state, child_pid

    # ── internals ──

    def _prepare_cancel_event(
        self, cancel_event: Any
    ) -> tuple[multiprocessing.synchronize.Event, threading.Event | None]:
        """Normalize the cancel input into a picklable mp.Event.

        See P1's ADR (docs/adr/0002-cancellation-contract.md) for the
        accepted shapes. The implementation mirrors what each runner
        used to do inline; centralizing here is the actual P2 win.

        Returns ``(mp_event, bridge_stop)``. ``bridge_stop`` is ``None``
        when no mirror-bridge thread was spawned (caller already gave
        us a usable mp.Event). When non-``None``, the caller MUST
        ``.set()`` it after the child has joined so the bridge thread
        exits — see T1.2.
        """
        from bpp.utils.cancel import (
            ProcessCancellation,
            as_token,
            mirror_token_to_process_event,
        )

        token = as_token(cancel_event)
        if token is None:
            return ProcessCancellation().event, None
        if isinstance(token, ProcessCancellation):
            return token.event, None
        if _is_raw_mp_event(cancel_event):
            return cancel_event, None
        # ThreadCancellation, raw threading.Event, or wrapped — bridge.
        bridge_target = ProcessCancellation()
        bridge_stop = threading.Event()
        mirror_token_to_process_event(token, bridge_target, stop_event=bridge_stop)
        return bridge_target.event, bridge_stop

    def _drain(
        self,
        result_queue: multiprocessing.Queue,
        proc: multiprocessing.Process,
        progress_callback: Callable[[dict[str, Any]], None] | None,
    ) -> tuple[O, bool]:
        """Pull messages until SENTINEL, fatal_error, or timeout.

        Returns ``(final_state, child_crashed)``. The child_crashed flag
        captures both the explicit fatal_error path and the timeout
        path; SIGKILL/SIGSEGV detection happens in the caller via
        ``proc.exitcode`` after join.
        """
        state = self.phase.initial_state()
        child_crashed = False
        while True:
            try:
                msg = result_queue.get(timeout=self.message_timeout_s)
            except queue.Empty:
                log.warning(
                    "Phase %r queue timeout — pid=%s alive=%s exitcode=%s",
                    self.phase.name,
                    proc.pid,
                    proc.is_alive(),
                    proc.exitcode,
                )
                if progress_callback:
                    progress_callback(
                        {
                            "type": "error",
                            "message": f"{self.phase.name} timed out",
                        }
                    )
                child_crashed = True
                break

            if msg == SENTINEL:
                break

            # Explicit fatal-error path. The child caught the exception
            # and surfaced it as a structured message before SENTINEL.
            if isinstance(msg, dict) and msg.get("type") == "fatal_error":
                log.error(
                    "Phase %r subprocess crashed (pid=%s): %s\n%s",
                    self.phase.name,
                    proc.pid,
                    msg.get("error"),
                    msg.get("traceback"),
                )
                child_crashed = True
                if progress_callback:
                    progress_callback(
                        {
                            "type": "error",
                            "message": f"{self.phase.name} crashed: {msg.get('error')}",
                        }
                    )
                # Keep draining — the child may still emit SENTINEL on
                # its way out, and we want to consume it cleanly.
                continue

            state, fwd = self.phase.reduce(state, msg)
            if fwd is not None and progress_callback:
                progress_callback(fwd)

        return state, child_crashed
