"""P4 finish — LibraryLifecycle facade + collaborator drain semantics.

Two named tests from refactor-plan.md P4:

* ``test_switch_library_drains_all_collaborators``
* ``test_facade_property_delegates_log_deprecation_once``

Plus a couple of focused unit tests on the LibraryLifecycle class.
"""

from __future__ import annotations

import logging
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from bpp.web._deprecated_attr import _reset_logged_for_tests, deprecated_attr
from bpp.web.library_lifecycle import LibraryLifecycle

# ── LibraryLifecycle delegate semantics ──


class TestLibraryLifecycleFacade:
    def test_class_holds_ctx_reference(self):
        ctx = SimpleNamespace(paths=SimpleNamespace(workdir="/tmp/wd", library_path="/tmp/lib"))
        lc = LibraryLifecycle(ctx)
        assert lc._ctx is ctx

    def test_workdir_property_reads_through_ctx(self):
        ctx = SimpleNamespace(paths=SimpleNamespace(workdir="/x", library_path="/y"))
        lc = LibraryLifecycle(ctx)
        assert lc.workdir == "/x"
        assert lc.library_path == "/y"

    def test_startup_delegates_to_state_lifecycle(self, monkeypatch):
        called = {"with_ctx": None}

        def _fake_startup(ctx):
            called["with_ctx"] = ctx

        import bpp.web.state_lifecycle as _impl

        monkeypatch.setattr(_impl, "startup", _fake_startup)

        ctx = SimpleNamespace()
        LibraryLifecycle(ctx).startup()
        assert called["with_ctx"] is ctx

    def test_switch_library_delegates_to_state_lifecycle(self, monkeypatch):
        called = {}

        def _fake_switch(ctx, new_path):
            called["ctx"] = ctx
            called["new_path"] = new_path

        import bpp.web.state_lifecycle as _impl

        monkeypatch.setattr(_impl, "switch_library", _fake_switch)

        ctx = SimpleNamespace()
        LibraryLifecycle(ctx).switch_library("/new")
        assert called == {"ctx": ctx, "new_path": "/new"}


# ── @deprecated_attr decorator ──


class TestDeprecatedAttr:
    @pytest.fixture(autouse=True)
    def _reset(self):
        _reset_logged_for_tests()
        yield
        _reset_logged_for_tests()

    def test_first_access_logs_warning(self, caplog):
        class _Holder:
            @property
            @deprecated_attr("new.path (P4)")
            def legacy_attr(self):
                return 42

        h = _Holder()
        with caplog.at_level(logging.WARNING, logger="bpp.web._deprecated_attr"):
            _ = h.legacy_attr
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "first access must produce a WARNING"
        msg = warnings[0].getMessage()
        assert "deprecated" in msg
        assert "new.path (P4)" in msg

    def test_facade_property_delegates_log_deprecation_once(self, caplog):
        """The plan's named test. The bare-attribute access on a
        legacy property fires exactly ONE warning per process even
        across many accesses."""

        class _Holder:
            @property
            @deprecated_attr("new.path (P4)")
            def legacy_attr(self):
                return 42

        h = _Holder()
        with caplog.at_level(logging.WARNING, logger="bpp.web._deprecated_attr"):
            for _ in range(50):
                _ = h.legacy_attr

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1, (
            f"deprecation warning must fire exactly once per process; got {len(warnings)} warnings"
        )

    def test_returns_underlying_value_correctly(self):
        class _Holder:
            @property
            @deprecated_attr("X")
            def x(self):
                return "hello"

        assert _Holder().x == "hello"

    def test_threadsafe_first_access(self):
        """Two threads racing on the first access must both succeed,
        and the warning must fire at most once."""

        class _Holder:
            @property
            @deprecated_attr("Y")
            def y(self):
                return 1

        h = _Holder()
        results: list[int] = []
        errors: list[BaseException] = []

        def _worker():
            try:
                results.append(h.y)
            except BaseException as e:
                errors.append(e)

        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert errors == []
        assert results == [1] * 8


# ── Drain test — switch_library cancels every collaborator's resources ──


class TestSwitchLibraryDrainsAllCollaborators:
    """The plan's named test:
    ``test_switch_library_drains_all_collaborators``.

    Validates the contract that switch_library:

    1. Cancels every BackgroundWorker via WorkerPool.cancel_and_join_all.
    2. Joins the AnalysisStore daemon threads
       (compute_thread + warm_thread) via analysis_store.join_threads.
    3. Closes the connection pool (via close_all_connections).

    We monkeypatch each collaborator's drain hook to record the call
    order; the actual switch_library exits early after the drains
    because new_dirs setup fails in our fake ctx — but the drain
    calls happen FIRST, so they're recorded.
    """

    def test_switch_library_calls_workerpool_then_analysis_store(self, monkeypatch):
        from bpp.web import state_lifecycle as _impl

        events: list[str] = []

        # Stub WorkerPool.cancel_and_join_all and AnalysisStore.join_threads
        # on the ctx so we can record the call order.
        class _StubWorkerPool:
            def cancel_and_join_all(self, timeout: Any) -> None:
                events.append("workerpool.cancel_and_join_all")

        class _StubAnalysisStore:
            def join_threads(self, timeout: Any) -> None:
                events.append("analysis_store.join_threads")

            def bump_generation_and_reset_phash(self) -> None:
                events.append("analysis_store.bump_generation")

        class _StubPaths:
            def __init__(self):
                self.dirs = {}
                self.workdir = "/wd"
                self.library_path = "/lib"

        # Build a minimal ctx that has everything switch_library reaches for
        # up through the drain calls.
        ctx = SimpleNamespace(
            workers=_StubWorkerPool(),
            analysis_store=_StubAnalysisStore(),
            paths=_StubPaths(),
            state={"workdir": "/wd", "library_path": "/lib"},
            dirs={},
            lock=threading.RLock(),
            thumbs=None,
            caches=SimpleNamespace(clip_cache={"embeddings": {}, "ready": False}),
            _switch_library_lock=threading.Lock(),
            startup=lambda: events.append("startup"),
        )

        # Use a real LibraryPaths since switch_library uses
        # dataclasses.replace on it.
        from bpp.web.state import LibraryPaths

        ctx.paths = LibraryPaths(library_path="/old", workdir="/old_wd", dirs={})

        # Stub ensure_library_dirs to return a fresh dirs dict.
        monkeypatch.setattr(
            "bpp.db.library.ensure_library_dirs",
            lambda path: {"data": "/new_wd", "thumbs": "/new_t"},
        )
        # Stub close_all_connections so we don't touch the real pool.
        monkeypatch.setattr(_impl, "close_all_connections", lambda: events.append("close_pool"))
        # No plugins registered → fire_on_library_close is a no-op.

        _impl.switch_library(ctx, "/new/path")

        # Order: workerpool first, then analysis store, then pool close,
        # then startup. The "close_pool" event is inside the lock block.
        assert events.index("workerpool.cancel_and_join_all") < events.index(
            "analysis_store.join_threads"
        )
        assert events.index("analysis_store.join_threads") < events.index("close_pool")
        assert events.index("close_pool") < events.index("startup")


class TestSwitchLibraryResetsPhase5HealthFlag:
    """Three-review M-S2: an OLD library's Phase 5 daemon can outlive
    the library switch. If it eventually fails (e.g. its captured
    connection is closed during the switch) it would .set(True) the
    ctx.phase5_failed flag AFTER the NEW library's daemon already
    completed — poisoning /api/v1/health's report for the new library
    with a 'smart album counts may be stale' degraded status.

    switch_library now resets the flag inside its locked region so a
    late-arriving old daemon's True can't survive past the switch
    point. (The new daemon also sets it False on entry; this is
    belt-and-braces against the race window between switch_library
    returning and the new daemon actually running.)
    """

    def test_switch_library_resets_phase5_failed_flag(self, monkeypatch):
        from bpp.web import state_lifecycle as _impl
        from bpp.web.state import LibraryPaths

        class _NoOpWorkerPool:
            def cancel_and_join_all(self, timeout: Any) -> None: ...

        class _NoOpAnalysisStore:
            def join_threads(self, timeout: Any) -> None: ...

            def bump_generation_and_reset_phash(self) -> None: ...

        ctx = SimpleNamespace(
            workers=_NoOpWorkerPool(),
            analysis_store=_NoOpAnalysisStore(),
            paths=LibraryPaths(library_path="/old", workdir="/old_wd", dirs={}),
            state={"workdir": "/old_wd", "library_path": "/old"},
            dirs={},
            lock=threading.RLock(),
            thumbs=None,
            caches=SimpleNamespace(clip_cache={"embeddings": {}, "ready": False}),
            _switch_library_lock=threading.Lock(),
            startup=lambda: None,
            # The poisoned state: a prior daemon ran and failed.
            phase5_failed=True,
            smart_album_backfill_done=threading.Event(),
        )
        # Event starts set (the prior daemon completed before failing).
        ctx.smart_album_backfill_done.set()

        monkeypatch.setattr(
            "bpp.db.library.ensure_library_dirs",
            lambda path: {"data": "/new_wd", "thumbs": "/new_t"},
        )
        monkeypatch.setattr(_impl, "close_all_connections", lambda: None)

        _impl.switch_library(ctx, "/new/path")

        # The switch must have reset the flag — otherwise the new
        # library's /api/v1/health would inherit the OLD library's
        # stale-album-counts degraded status.
        assert ctx.phase5_failed is False, (
            "switch_library must reset ctx.phase5_failed so a "
            "late-arriving daemon from the outgoing library can't "
            "poison the incoming library's health surface"
        )


# ── T1.3: switch_library serialization ──


class TestSwitchLibrarySerialized:
    """T1.3: ``switch_library`` does heavy work (plugin close hooks,
    worker cancel-and-join, AnalysisStore drain, DB pool close) OUTSIDE
    of ``ctx.lock`` so the lock isn't held during the long drain. That
    leaves a window where two concurrent ``switch_library`` calls — say
    the Tauri sidecar sending a switch while a Flask endpoint is also
    handling one — interleave their drains and double-fire plugin
    hooks.

    The fix introduces a dedicated ``_switch_library_lock`` on
    ``WebAppState`` that serializes the entire body of
    ``state_lifecycle.switch_library``. Concurrent callers queue up;
    each switch runs to completion before the next starts.
    """

    def _build_ctx(self, *, in_flight_signal, slow_close):
        """Build a SimpleNamespace ctx that records when its critical
        section is entered/exited (via the slow_close hook) so we can
        assert there's never more than one switch in flight."""
        from bpp.web.state import LibraryPaths

        class _StubWorkerPool:
            def cancel_and_join_all(self, timeout: Any) -> None:
                in_flight_signal["enter"]()
                slow_close()
                in_flight_signal["exit"]()

        class _StubAnalysisStore:
            def join_threads(self, timeout: Any) -> None:
                pass

            def bump_generation_and_reset_phash(self) -> None:
                pass

        return SimpleNamespace(
            workers=_StubWorkerPool(),
            analysis_store=_StubAnalysisStore(),
            paths=LibraryPaths(library_path="/old", workdir="/old_wd", dirs={}),
            state={"workdir": "/wd", "library_path": "/lib"},
            dirs={},
            lock=threading.RLock(),
            thumbs=None,
            caches=SimpleNamespace(clip_cache={"embeddings": {}, "ready": False}),
            _switch_library_lock=threading.Lock(),
            startup=lambda: None,
        )

    def test_concurrent_switch_library_serializes(self, monkeypatch):
        """Two concurrent switch_library calls must NOT overlap their
        drain phases. The contract: in_flight counter never exceeds 1.
        """
        from bpp.web import state_lifecycle as _impl

        in_flight = 0
        max_in_flight = 0
        in_flight_lock = threading.Lock()

        def _enter() -> None:
            nonlocal in_flight, max_in_flight
            with in_flight_lock:
                in_flight += 1
                if in_flight > max_in_flight:
                    max_in_flight = in_flight

        def _exit_drain() -> None:
            nonlocal in_flight
            with in_flight_lock:
                in_flight -= 1

        def _slow_close() -> None:
            # Sleep inside the critical section so a concurrent call
            # would visibly overlap if the lock isn't held.
            time.sleep(0.05)

        ctx = self._build_ctx(
            in_flight_signal={"enter": _enter, "exit": _exit_drain},
            slow_close=_slow_close,
        )

        monkeypatch.setattr(
            "bpp.db.library.ensure_library_dirs",
            lambda path: {"data": "/new_wd", "thumbs": "/new_t"},
        )
        monkeypatch.setattr(_impl, "close_all_connections", lambda: None)

        errors: list[BaseException] = []

        def _worker(path: str) -> None:
            try:
                _impl.switch_library(ctx, path)
            except BaseException as e:
                errors.append(e)

        threads = [threading.Thread(target=_worker, args=(f"/new/path/{i}",)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == [], f"switch_library workers errored: {errors}"
        assert max_in_flight == 1, (
            f"switch_library is not serialized — observed "
            f"{max_in_flight} concurrent drains. The _switch_library_lock "
            f"must wrap the entire function body."
        )

    def test_lock_does_not_block_unrelated_calls(self, monkeypatch):
        """Sanity: the lock is released after switch_library returns
        so a subsequent call from any thread succeeds without timeout.
        """
        from bpp.web import state_lifecycle as _impl

        ctx = self._build_ctx(
            in_flight_signal={"enter": lambda: None, "exit": lambda: None},
            slow_close=lambda: None,
        )
        monkeypatch.setattr(
            "bpp.db.library.ensure_library_dirs",
            lambda path: {"data": "/new_wd", "thumbs": "/new_t"},
        )
        monkeypatch.setattr(_impl, "close_all_connections", lambda: None)

        _impl.switch_library(ctx, "/new/path/1")
        # Second call should not deadlock — lock was released.
        _impl.switch_library(ctx, "/new/path/2")
