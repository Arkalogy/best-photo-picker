"""P4b — AnalysisStore collaborator unit tests."""

from __future__ import annotations

import threading
import time

import pytest

from bpp.web.analysis_store import AnalysisStore


class TestConstruction:
    def test_default_factory_values(self):
        store = AnalysisStore()
        assert isinstance(store.phash_ready, threading.Event)
        assert store.phash_ready.is_set() is False
        assert store.phash_generation == 0
        assert store.compute_thread is None
        assert store.warm_thread is None
        assert isinstance(store.cancel_warm, threading.Event)
        assert store.cancel_warm.is_set() is False


class TestBumpGenerationAndResetPhash:
    def test_increments_generation_and_swaps_event(self):
        store = AnalysisStore()
        original_event = store.phash_ready
        original_event.set()  # simulate the old library's thread firing
        assert original_event.is_set() is True

        store.bump_generation_and_reset_phash()

        # Generation bumped.
        assert store.phash_generation == 1
        # Fresh Event swapped in — new one is not set, old one is still set.
        assert store.phash_ready is not original_event
        assert store.phash_ready.is_set() is False
        assert original_event.is_set() is True  # untouched

    def test_repeated_bumps_increment(self):
        store = AnalysisStore()
        store.bump_generation_and_reset_phash()
        store.bump_generation_and_reset_phash()
        store.bump_generation_and_reset_phash()
        assert store.phash_generation == 3


class TestJoinThreads:
    def _spawn(self, store: AnalysisStore, slot: str, sleep_s: float = 0.05) -> None:
        """Spawn a daemon thread that polls cancel_warm and exits when set."""

        def _loop():
            while not store.cancel_warm.is_set():
                time.sleep(sleep_s)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        setattr(store, slot, t)

    def test_signals_cancel_and_joins_both(self):
        store = AnalysisStore()
        self._spawn(store, "warm_thread")
        self._spawn(store, "compute_thread")
        assert store.warm_thread is not None and store.warm_thread.is_alive()
        assert store.compute_thread is not None and store.compute_thread.is_alive()

        store.join_threads(timeout=2.0)

        # cancel_warm fired → threads exit; references cleared.
        assert store.cancel_warm.is_set() is True
        assert store.warm_thread is None
        assert store.compute_thread is None

    def test_empty_handles_are_noop(self):
        store = AnalysisStore()
        # Must not raise when no threads are tracked.
        store.join_threads(timeout=1.0)
        assert store.cancel_warm.is_set() is True

    def test_stuck_thread_logged_not_blocking(self, caplog):
        """A thread that won't exit within timeout triggers ERROR but
        join_threads still returns and clears the slot."""
        import logging

        store = AnalysisStore()

        # Use a thread that ignores the cancel signal — simulates a
        # buggy daemon that doesn't poll the cancel_warm Event.
        unstoppable_done = threading.Event()

        def _ignores_cancel():
            # Run until externally signaled — never look at cancel_warm.
            unstoppable_done.wait(timeout=10)

        t = threading.Thread(target=_ignores_cancel, daemon=True)
        t.start()
        store.warm_thread = t

        with caplog.at_level(logging.ERROR, logger="bpp.web.analysis_store"):
            store.join_threads(timeout=0.2)

        # Reference cleared even though thread is still alive.
        assert store.warm_thread is None
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors, "stuck thread must produce ERROR log"
        assert any("warm_thread" in r.getMessage() for r in errors)

        # Release the stuck thread so the test process exits cleanly.
        unstoppable_done.set()
        t.join(timeout=1)


class TestWebAppStateDelegation:
    """Legacy ``ctx.phash_ready`` / ``ctx._phash_generation`` /
    ``ctx._compute_thread`` / ``ctx._warm_thread`` / ``ctx._cancel_warm``
    must continue to read + write through to the AnalysisStore."""

    def test_legacy_attributes_route_through_store(self):
        from types import SimpleNamespace

        from bpp.web.state import WebAppState

        store = AnalysisStore()
        fake_ctx = SimpleNamespace(analysis_store=store)

        # Read delegates.
        assert WebAppState.phash_ready.fget(fake_ctx) is store.phash_ready
        assert WebAppState._phash_generation.fget(fake_ctx) == 0
        assert WebAppState._compute_thread.fget(fake_ctx) is None
        assert WebAppState._warm_thread.fget(fake_ctx) is None
        assert WebAppState._cancel_warm.fget(fake_ctx) is store.cancel_warm

        # Write delegates.
        new_event = threading.Event()
        WebAppState.phash_ready.fset(fake_ctx, new_event)
        assert store.phash_ready is new_event

        WebAppState._phash_generation.fset(fake_ctx, 42)
        assert store.phash_generation == 42

        dummy_thread = threading.Thread(target=lambda: None)
        WebAppState._warm_thread.fset(fake_ctx, dummy_thread)
        assert store.warm_thread is dummy_thread


@pytest.mark.parametrize("op", ["bump", "join"])
def test_method_smoke_no_unexpected_state_leak(op):
    """Belt check: neither method leaves the store in a half-state."""
    store = AnalysisStore()
    if op == "bump":
        store.bump_generation_and_reset_phash()
        assert store.phash_generation == 1
        assert isinstance(store.phash_ready, threading.Event)
    else:
        store.join_threads(timeout=0.1)
        assert store.warm_thread is None
        assert store.compute_thread is None
