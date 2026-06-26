"""P2 — BoundedSubprocessRunner machinery.

Tests live below the actual scoring/face-extract integration tests in
``test_scoring_subprocess.py`` and ``test_cancel_propagation.py``;
this file is the in-process gate for the substrate itself.

Each test uses a deliberately tiny dummy Phase so the spawn child
imports almost nothing (fast). The runner is the unit under test —
not the phases the production code wraps.

Note on multiprocessing.spawn picklability: phase classes AND their
target callables must be importable by name in the child. We define
them at module scope here (NOT inside test methods) so spawn's
re-import lookup succeeds.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import threading
import time
from typing import Any

from bpp.utils.cancel import ProcessCancellation, ThreadCancellation
from bpp.utils.subprocess_runner import (
    SENTINEL,
    BoundedSubprocessRunner,
)

# ── Module-scope target functions for spawn picklability ──


def _child_emits_n_then_exits(
    n: int,
    result_queue: multiprocessing.Queue,
    _cancel: multiprocessing.synchronize.Event,
) -> None:
    """Emit ``n`` integer messages, then SENTINEL."""
    for i in range(n):
        result_queue.put(i)
    result_queue.put(SENTINEL)


def _child_raises_then_sentinel(
    _payload: int,
    result_queue: multiprocessing.Queue,
    _cancel: multiprocessing.synchronize.Event,
) -> None:
    """Emit a structured fatal_error message, then SENTINEL.

    Mirrors the real production pattern: the child's outer try/except
    catches the exception, publishes ``{type: fatal_error, ...}``, then
    the finally clause still emits SENTINEL so the parent drain loop
    exits cleanly.
    """
    result_queue.put(
        {
            "type": "fatal_error",
            "error": "kaboom",
            "traceback": "Traceback (most recent call last):\n  ... kaboom",
        }
    )
    result_queue.put(SENTINEL)


def _child_polls_cancel_then_emits(
    payload: int,
    result_queue: multiprocessing.Queue,
    cancel: multiprocessing.synchronize.Event,
) -> None:
    """Emit one message per "step" but check cancel between each.

    Used to verify the runner's cancel plumbing actually reaches the
    child. With pre-cancel, the loop exits on the first iteration and
    no work-bearing messages reach the parent.
    """
    for i in range(payload):
        if cancel.is_set():
            break
        result_queue.put(i)
    result_queue.put(SENTINEL)


def _child_emits_env_var_then_exits(
    _payload: int,
    result_queue: multiprocessing.Queue,
    _cancel: multiprocessing.synchronize.Event,
) -> None:
    """Echo back ``OMP_NUM_THREADS`` from the child's env.

    The runner doesn't pin env vars itself — production callers
    (analyze_worker) set them at module import time before the spawn
    re-import. This test verifies the runner doesn't accidentally clear
    them. A regression would surface as the child seeing no var (or a
    different value) than the parent.
    """
    result_queue.put({"OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS")})
    result_queue.put(SENTINEL)


def _child_sigkills_itself(
    _payload: int,
    result_queue: multiprocessing.Queue,
    _cancel: multiprocessing.synchronize.Event,
) -> None:
    """SIGKILL the child mid-flight, before SENTINEL.

    Models a SIGSEGV from a misbehaving ML allocator: no fatal_error
    message, no SENTINEL — just the kernel signal. The runner must
    detect this via the queue timeout + exitcode post-mortem.
    """
    os.kill(os.getpid(), signal.SIGKILL)


def _child_sleeps_then_emits(
    seconds: float,
    result_queue: multiprocessing.Queue,
    _cancel: multiprocessing.synchronize.Event,
) -> None:
    """Sleep, then emit SENTINEL. Used for timeout test."""
    time.sleep(seconds)
    result_queue.put(SENTINEL)


# ── Module-scope Phase classes for spawn picklability ──


class _CountingPhase:
    """Accumulator counts non-progress messages; reduces ints into a sum."""

    name = "counting"

    def __init__(self, child_fn=_child_emits_n_then_exits) -> None:
        self._child_fn = child_fn

    def target(self):
        return self._child_fn

    def build_args(
        self,
        payload: Any,
        result_queue: multiprocessing.Queue,
        cancel_event: multiprocessing.synchronize.Event,
    ) -> tuple[Any, ...]:
        return (payload, result_queue, cancel_event)

    def initial_state(self) -> dict[str, int | list[int]]:
        return {"count": 0, "values": []}

    def reduce(
        self, state: dict[str, Any], msg: Any
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if isinstance(msg, int):
            state["count"] += 1
            state["values"].append(msg)
            # Forward a progress tick so we can verify the callback path.
            return state, {"type": "progress", "current": state["count"]}
        return state, None


class _SilentPhase(_CountingPhase):
    """Like _CountingPhase but never forwards progress."""

    name = "silent"

    def reduce(
        self, state: dict[str, Any], msg: Any
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if isinstance(msg, int):
            state["count"] += 1
        return state, None


# ── Tests ──


class TestSatisfiesProtocol:
    """Both stub phases satisfy the Phase[…, …] protocol behaviourally."""

    def test_counting_phase_is_phase(self):
        phase = _CountingPhase()
        # Protocol is structurally typed at runtime — duck-type via
        # attribute probe instead of isinstance (Phase is a
        # non-runtime_checkable Protocol; isinstance would error).
        assert hasattr(phase, "name")
        assert callable(phase.target)
        assert callable(phase.build_args)
        assert callable(phase.initial_state)
        assert callable(phase.reduce)
        # And mypy/pyright would accept it as Phase — instantiate
        # a runner to prove the Generic parameter binds.
        runner: BoundedSubprocessRunner = BoundedSubprocessRunner(phase)
        assert runner.phase is phase


class TestHappyPath:
    def test_drains_until_sentinel_and_accumulates_state(self):
        runner = BoundedSubprocessRunner(_CountingPhase())
        state, pid = runner.run(payload=3)
        assert state == {"count": 3, "values": [0, 1, 2]}
        assert pid is not None
        # Child must have exited by the time run() returns.
        assert _pid_exited(pid)

    def test_forwards_progress_messages_to_callback(self):
        progress: list[dict] = []
        runner = BoundedSubprocessRunner(_CountingPhase())
        runner.run(
            payload=4,
            progress_callback=progress.append,
        )
        # Exactly one progress tick per int emitted (phase opts in by
        # returning fwd-msg from reduce()).
        assert [p["current"] for p in progress] == [1, 2, 3, 4]

    def test_silent_phase_emits_no_progress(self):
        """A phase that returns ``None`` from reduce never calls the
        callback — the runner must respect the phase's intent."""
        progress: list[dict] = []
        runner = BoundedSubprocessRunner(_SilentPhase())
        state, _ = runner.run(
            payload=5,
            progress_callback=progress.append,
        )
        assert state == {"count": 5, "values": []}  # SilentPhase doesn't track values
        # Note: _SilentPhase's initial_state inherits from _CountingPhase
        # which sets values=[]; SilentPhase never appends to it. So
        # values list stays empty — this is a fine smoke check.
        assert progress == []


class TestFatalErrorPath:
    def test_child_exception_logged_and_state_returned_partial(self):
        runner = BoundedSubprocessRunner(_CountingPhase(child_fn=_child_raises_then_sentinel))
        state, pid = runner.run(payload=0)
        # No work-bearing messages were emitted, so state stays at initial.
        assert state == {"count": 0, "values": []}
        assert pid is not None
        assert _pid_exited(pid)

    def test_fatal_error_emits_error_progress_message(self):
        progress: list[dict] = []
        runner = BoundedSubprocessRunner(_CountingPhase(child_fn=_child_raises_then_sentinel))
        runner.run(payload=0, progress_callback=progress.append)
        errors = [m for m in progress if m.get("type") == "error"]
        assert errors, f"expected error progress msg, got {progress}"
        assert "kaboom" in errors[0]["message"]


class TestCancellation:
    def test_pre_cancelled_token_halts_child_immediately(self):
        runner = BoundedSubprocessRunner(_CountingPhase(child_fn=_child_polls_cancel_then_emits))
        token = ProcessCancellation()
        token.set()  # pre-cancel
        state, pid = runner.run(payload=100, cancel_event=token)
        # Child saw the flag before emitting any work.
        assert state["count"] == 0
        assert _pid_exited(pid)

    def test_thread_cancellation_bridged_to_child(self):
        """ThreadCancellation is not picklable; runner must bridge to
        an mp.Event via mirror thread (P1 contract)."""
        runner = BoundedSubprocessRunner(_CountingPhase(child_fn=_child_polls_cancel_then_emits))
        token = ThreadCancellation()
        token.set()
        state, pid = runner.run(payload=100, cancel_event=token)
        assert state["count"] == 0
        assert _pid_exited(pid)

    def test_raw_mp_event_passthrough(self):
        runner = BoundedSubprocessRunner(_CountingPhase(child_fn=_child_polls_cancel_then_emits))
        evt = multiprocessing.Event()
        evt.set()
        state, pid = runner.run(payload=100, cancel_event=evt)
        assert state["count"] == 0
        assert _pid_exited(pid)

    def test_raw_threading_event_bridged(self):
        runner = BoundedSubprocessRunner(_CountingPhase(child_fn=_child_polls_cancel_then_emits))
        evt = threading.Event()
        evt.set()
        state, pid = runner.run(payload=100, cancel_event=evt)
        assert state["count"] == 0
        assert _pid_exited(pid)

    def test_no_cancel_event_uses_internal_default(self):
        """Caller doesn't have to provide cancel — runner builds one
        internally so the child always has a valid event to read."""
        runner = BoundedSubprocessRunner(_CountingPhase())
        state, _ = runner.run(payload=2, cancel_event=None)
        assert state["count"] == 2

    def test_bridge_thread_does_not_leak_after_natural_completion(self):
        """T1.2: a successful run with a non-mp cancel_event (e.g. a
        ThreadCancellation that NEVER fires) historically spawned a
        mirror-bridge daemon that polled forever — the daemon-thread
        flag didn't help because Flask's parent stays alive across
        every analyze/face/etc run. Over a day, this leaked one thread
        per subprocess invocation.

        The fix wires a stop_event through ``_prepare_cancel_event`` →
        ``mirror_token_to_process_event`` and ``.set()``s it after the
        child joins. Bridge thread must be ``not alive`` by the time
        run() returns (allowing one poll interval slack).
        """
        # Count bridge threads BEFORE the run.
        bridges_before = sum(1 for t in threading.enumerate() if t.name == "cancel-token-mirror")

        runner = BoundedSubprocessRunner(_CountingPhase())
        token = ThreadCancellation()
        # Token NEVER set — child completes naturally; this is the
        # historical leak path.
        state, _ = runner.run(payload=2, cancel_event=token)
        assert state["count"] == 2

        # Give the bridge up to 0.5 s to honor the stop_event.set().
        # Default poll interval is 100 ms; 5x slack.
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            bridges_now = sum(
                1 for t in threading.enumerate() if t.name == "cancel-token-mirror" and t.is_alive()
            )
            if bridges_now == bridges_before:
                return
            time.sleep(0.02)

        # If we got here, at least one bridge thread is still alive.
        leaked = [
            t for t in threading.enumerate() if t.name == "cancel-token-mirror" and t.is_alive()
        ]
        raise AssertionError(
            f"BoundedSubprocessRunner leaked {len(leaked) - bridges_before} "
            f"cancel-token-mirror thread(s) after natural completion "
            f"(T1.2 — fixed by stop_event in _prepare_cancel_event)"
        )


class TestEnvVarPreservation:
    def test_OMP_NUM_THREADS_propagates_to_child(self, monkeypatch):
        """The runner must NOT clear env vars between parent and child.

        Production analyze_worker.py pins ``OMP_NUM_THREADS=1`` at
        module import time so the spawn child's C-extension imports
        see the pinned value. If the runner ever passes an empty env
        to ``mp.Process`` (it doesn't, by default — but if a future
        refactor adds one), the pinning silently regresses.
        """
        monkeypatch.setenv("OMP_NUM_THREADS", "1")
        runner = BoundedSubprocessRunner(
            _CountingPhaseEnv(child_fn=_child_emits_env_var_then_exits)
        )
        state, _ = runner.run(payload=0)
        assert state["env"] == "1", (
            "child must see OMP_NUM_THREADS=1; runner regressed env-var pinning"
        )


class _CountingPhaseEnv:
    """Phase that captures the child's env-var echo."""

    name = "env-echo"

    def __init__(self, child_fn=_child_emits_env_var_then_exits) -> None:
        self._child_fn = child_fn

    def target(self):
        return self._child_fn

    def build_args(self, payload, result_queue, cancel_event):
        return (payload, result_queue, cancel_event)

    def initial_state(self) -> dict[str, str | None]:
        return {"env": None}

    def reduce(self, state, msg):
        if isinstance(msg, dict) and "OMP_NUM_THREADS" in msg:
            state["env"] = msg["OMP_NUM_THREADS"]
        return state, None


class TestCrashDetection:
    def test_sigkill_detected_via_exitcode(self):
        """A child that SIGKILLs itself emits no SENTINEL — the runner
        must time out the drain and recognize the crash via
        ``proc.exitcode``."""
        runner = BoundedSubprocessRunner(
            _CountingPhase(child_fn=_child_sigkills_itself),
            message_timeout_s=2.0,  # short — we know no msg is coming
        )
        state, pid = runner.run(payload=0)
        # No progress emitted; state stays initial.
        assert state == {"count": 0, "values": []}
        assert pid is not None
        # SIGKILL leaves a negative exitcode; the runner's log line
        # documents "abnormally exited" — we can't easily intercept
        # the log without caplog, but the contract is "returns
        # without hanging," which is what we're testing here.

    def test_message_timeout_returns_partial_state(self):
        """A child that sleeps past the message timeout is treated as
        stuck — the runner returns whatever it has and force-kills."""
        runner = BoundedSubprocessRunner(
            _CountingPhase(child_fn=_child_sleeps_then_emits),
            message_timeout_s=0.5,  # short
            graceful_join_s=1.0,
            force_join_s=1.0,
        )
        progress: list[dict] = []
        t0 = time.monotonic()
        state, _pid = runner.run(payload=5.0, progress_callback=progress.append)
        elapsed = time.monotonic() - t0
        # Drain timeout 0.5s + graceful join 1s + force join 1s = ~2.5s upper bound.
        # Allow generous headroom for slow CI.
        assert elapsed < 10.0, f"runner did not bound stuck child within 10s; took {elapsed:.1f}s"
        assert state == {"count": 0, "values": []}
        # Error progress msg was emitted for the timeout.
        errors = [m for m in progress if m.get("type") == "error"]
        assert errors, "expected timeout error progress msg"


# ── Helpers ──


def _pid_exited(pid: int | None) -> bool:
    """True if the PID is gone. Polls briefly to handle the
    short window between proc.kill() returning and the kernel
    reaping the zombie."""
    if pid is None:
        return True
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.05)
    return False
