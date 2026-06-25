"""Unit tests for the cancellation contract — ``bpp.utils.cancel``.

Subprocess-fire integration tests live in
``tests/test_cancel_propagation.py`` (P1.6 — real spawn children, real
SIGKILL). This file is the in-process unit layer.
"""

from __future__ import annotations

import multiprocessing
import threading
import time

import pytest

from bpp.utils.cancel import (
    CancellationToken,
    ProcessCancellation,
    ThreadCancellation,
    _RawEventToken,
    as_token,
    make_pair,
    mirror_token_to_process_event,
    sleep_or_cancel,
)


# Module-level spawn target so ``multiprocessing.spawn`` can pickle it.
# Nested functions inside test methods fail with ``AttributeError`` on
# spawn — the start method imports this module by name and looks up the
# target there.
def _child_waits_for_cancel(t: ProcessCancellation, q: multiprocessing.Queue) -> None:
    # Wait up to 5 s for the parent to signal cancel.
    fired = t.wait(timeout=5)
    q.put(("ok", fired))


class TestThreadCancellation:
    def test_starts_unset(self):
        tok = ThreadCancellation()
        assert tok.is_set() is False

    def test_set_makes_is_set_true(self):
        tok = ThreadCancellation()
        tok.set()
        assert tok.is_set() is True

    def test_set_is_idempotent(self):
        tok = ThreadCancellation()
        tok.set()
        tok.set()
        assert tok.is_set() is True

    def test_wait_returns_immediately_when_already_set(self):
        tok = ThreadCancellation()
        tok.set()
        t0 = time.monotonic()
        assert tok.wait(timeout=5) is True
        assert time.monotonic() - t0 < 0.1

    def test_wait_times_out_when_unset(self):
        tok = ThreadCancellation()
        t0 = time.monotonic()
        assert tok.wait(timeout=0.1) is False
        elapsed = time.monotonic() - t0
        assert 0.08 <= elapsed <= 0.5

    def test_satisfies_protocol(self):
        # runtime_checkable Protocol — duck-type assertion.
        tok = ThreadCancellation()
        assert isinstance(tok, CancellationToken)


class TestProcessCancellation:
    def test_starts_unset(self):
        tok = ProcessCancellation()
        assert tok.is_set() is False

    def test_set_makes_is_set_true(self):
        tok = ProcessCancellation()
        tok.set()
        assert tok.is_set() is True

    def test_exposes_raw_event_for_back_compat(self):
        tok = ProcessCancellation()
        raw = tok.event
        # Setting the raw event must propagate to the token.
        raw.set()
        assert tok.is_set() is True

    def test_state_shared_across_spawn_child(self):
        """The whole point: a parent can set the token and the spawn
        child sees ``is_set() is True``. ``multiprocessing.Event`` is
        shared by inheritance through ``Process``, not by direct
        ``pickle.dumps`` — so the real test is a round trip through
        an actual subprocess.

        This is the regression gate for the silent cross-process
        cancellation-gap failure mode the audit found in
        ``run_face_extraction_subprocess``.
        """
        ctx = multiprocessing.get_context("spawn")
        tok = ProcessCancellation(ctx=ctx)
        result_q: multiprocessing.Queue = ctx.Queue()

        proc = ctx.Process(target=_child_waits_for_cancel, args=(tok, result_q))
        proc.start()
        time.sleep(0.1)  # let the child reach .wait()
        tok.set()
        proc.join(timeout=10)
        assert proc.exitcode == 0, f"child exited abnormally: {proc.exitcode}"
        tag, fired = result_q.get(timeout=1)
        assert tag == "ok"
        assert fired is True, "spawn child did not observe parent's cancel signal"

    def test_satisfies_protocol(self):
        tok = ProcessCancellation()
        assert isinstance(tok, CancellationToken)


class TestMirrorTokenToProcessEvent:
    def test_setting_source_propagates_to_target(self):
        """The bridge thread must fire when the source token sets, even
        though the target is a separate mp.Event."""
        source = ThreadCancellation()
        target = ProcessCancellation()
        mirror_token_to_process_event(source, target, poll_interval_s=0.02)
        assert target.is_set() is False
        source.set()
        # Wait up to 1 s for the bridge to propagate. Default poll is
        # 100 ms — 1 s gives 10x headroom for slow CI.
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if target.is_set():
                break
            time.sleep(0.01)
        assert target.is_set() is True, "bridge thread did not propagate within 1s"

    def test_bridge_exits_when_target_set_from_other_side(self):
        """If the subprocess completes naturally and sets the target,
        the bridge thread must NOT keep spinning forever — it should
        exit on the next poll tick."""
        source = ThreadCancellation()
        target = ProcessCancellation()
        bridge = mirror_token_to_process_event(source, target, poll_interval_s=0.02)
        target.set()
        bridge.join(timeout=1)
        assert not bridge.is_alive(), "bridge thread leaked after target was set externally"

    def test_bridge_exits_when_stop_event_set(self):
        """T1.2: a successful subprocess run leaves the target UNSET
        (cancel signal never fires). Without an external stop signal,
        the bridge thread polls forever — every BoundedSubprocessRunner
        call leaks a thread in the long-lived Flask parent.

        The fix: callers pass a ``stop_event`` they ``.set()`` after the
        subprocess returns. The bridge must wake within one poll
        interval and exit.
        """
        source = ThreadCancellation()
        target = ProcessCancellation()
        stop = threading.Event()
        bridge = mirror_token_to_process_event(
            source, target, poll_interval_s=0.02, stop_event=stop
        )
        # Simulate "subprocess finished naturally" — cancel never fired,
        # target was never set. Caller signals natural completion.
        stop.set()
        bridge.join(timeout=1)
        assert not bridge.is_alive(), (
            "bridge thread must exit when stop_event is set after "
            "natural subprocess completion (T1.2 — fixes per-run "
            "polling-thread leak)"
        )
        # And cancel did NOT propagate (the target was never touched).
        assert target.is_set() is False, (
            "stop_event must not fire the cancel signal — it's a "
            "natural-completion hint, not a cancel"
        )

    def test_stop_event_does_not_block_cancel_propagation(self):
        """T1.2: if the source fires BEFORE stop_event is set, the
        cancel still reaches the target. The stop_event is a hint, not
        a kill switch — it only takes effect when no cancel has
        happened yet."""
        source = ThreadCancellation()
        target = ProcessCancellation()
        stop = threading.Event()
        bridge = mirror_token_to_process_event(
            source, target, poll_interval_s=0.02, stop_event=stop
        )
        source.set()
        # Wait up to 1 s for propagation.
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if target.is_set():
                break
            time.sleep(0.01)
        assert target.is_set() is True, "stop_event must not suppress legitimate cancel propagation"
        bridge.join(timeout=1)
        assert not bridge.is_alive()


class TestMakePair:
    def test_returns_token_pair_and_started_bridge(self):
        thread_tok, process_tok, bridge = make_pair()
        assert isinstance(thread_tok, ThreadCancellation)
        assert isinstance(process_tok, ProcessCancellation)
        assert bridge.is_alive()
        # Smoke-check the bridge: setting one fires the other.
        thread_tok.set()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if process_tok.is_set():
                break
            time.sleep(0.01)
        assert process_tok.is_set() is True


class TestAsToken:
    def test_none_returns_none(self):
        assert as_token(None) is None

    def test_threading_event_wrapped(self):
        evt = threading.Event()
        tok = as_token(evt)
        assert tok is not None
        assert isinstance(tok, _RawEventToken)
        assert tok.is_set() is False
        evt.set()
        assert tok.is_set() is True

    def test_multiprocessing_event_wrapped(self):
        evt = multiprocessing.Event()
        tok = as_token(evt)
        assert tok is not None
        assert tok.is_set() is False
        tok.set()
        assert evt.is_set() is True

    def test_existing_token_passes_through_unchanged(self):
        existing = ThreadCancellation()
        result = as_token(existing)
        assert result is existing


class TestSleepOrCancel:
    def test_sleep_elapses_when_not_cancelled(self):
        tok = ThreadCancellation()
        t0 = time.monotonic()
        assert sleep_or_cancel(tok, 0.1) is False
        assert time.monotonic() - t0 >= 0.08

    def test_returns_immediately_when_cancelled_first(self):
        tok = ThreadCancellation()
        tok.set()
        t0 = time.monotonic()
        assert sleep_or_cancel(tok, 5) is True
        assert time.monotonic() - t0 < 0.1

    def test_none_degrades_to_plain_sleep(self):
        t0 = time.monotonic()
        assert sleep_or_cancel(None, 0.1) is False
        assert time.monotonic() - t0 >= 0.08

    def test_fires_mid_sleep(self):
        tok = ThreadCancellation()

        def _delayed_fire():
            time.sleep(0.05)
            tok.set()

        t = threading.Thread(target=_delayed_fire, daemon=True)
        t.start()
        t0 = time.monotonic()
        assert sleep_or_cancel(tok, 5) is True
        # Returned within ~50 ms + a small jitter band — must NOT have
        # waited the full 5 s.
        assert time.monotonic() - t0 < 0.5


@pytest.mark.parametrize(
    "tok_factory",
    [ThreadCancellation, ProcessCancellation],
)
class TestProtocolConformance:
    """Both implementations satisfy CancellationToken behaviourally."""

    def test_lifecycle(self, tok_factory):
        tok = tok_factory()
        assert tok.is_set() is False
        tok.set()
        assert tok.is_set() is True
        assert tok.wait(timeout=0.1) is True
