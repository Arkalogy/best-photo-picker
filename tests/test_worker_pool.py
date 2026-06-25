"""P4 — WorkerPool collaborator unit tests.

The pool is the smallest of the four collaborators
(see refactor-plan.md) so it lands first. Tests cover construction
from the registry, dict-like access for back-compat, and the
:meth:`cancel_and_join_all` lifecycle path used by switch_library and
shutdown.
"""

from __future__ import annotations

import threading

from bpp.web.worker_pool import WorkerPool

# ── Stub worker for tests ──


class _StubWorker:
    """Minimal BackgroundWorker stand-in.

    Records whether ``cancel_and_join`` was called and with what
    timeout. Raises optionally to exercise the per-worker exception
    handling in :meth:`WorkerPool.cancel_and_join_all`.
    """

    def __init__(self, *, raise_on_join: bool = False) -> None:
        self.cancel_calls: list[float] = []
        self.raise_on_join = raise_on_join

    def cancel_and_join(self, timeout: float) -> None:
        self.cancel_calls.append(timeout)
        if self.raise_on_join:
            raise RuntimeError("simulated stuck worker")


# ── Construction ──


class TestConstruction:
    def test_builds_workers_from_registry(self):
        registry = {"a": _StubWorker, "b": _StubWorker}
        pool = WorkerPool(registry=registry)
        assert set(pool) == {"a", "b"}
        assert isinstance(pool["a"], _StubWorker)
        assert isinstance(pool["b"], _StubWorker)

    def test_empty_registry_yields_empty_pool(self):
        pool = WorkerPool(registry={})
        assert len(pool) == 0
        assert list(pool.values()) == []
        assert "anything" not in pool

    def test_default_registry_is_global_registry(self, monkeypatch):
        """Production path: no registry arg → builds from the global
        WorkerRegistry. The four production workers must be present."""
        from bpp.web.worker_registry import WorkerRegistry

        pool = WorkerPool()
        # The registry holds the four canonical workers (analyze, face,
        # import, clip). We don't pin exact identity here because plugins
        # can register more — but the canonical four must all be there.
        registry_keys = set(WorkerRegistry.keys())
        for name in ("analyze", "face", "import", "clip"):
            assert name in pool, f"production WorkerPool must contain {name!r} (got {list(pool)})"
            assert name in registry_keys


# ── Dict-like surface ──


class TestDictLike:
    def test_getitem_setitem_contains(self):
        pool = WorkerPool(registry={"a": _StubWorker})
        replacement = _StubWorker()
        pool["a"] = replacement
        assert pool["a"] is replacement
        assert "a" in pool
        assert "missing" not in pool

    def test_get_default(self):
        pool = WorkerPool(registry={"a": _StubWorker})
        sentinel = _StubWorker()
        assert pool.get("a") is pool["a"]
        assert pool.get("missing", sentinel) is sentinel
        assert pool.get("missing") is None

    def test_items_and_values(self):
        pool = WorkerPool(registry={"a": _StubWorker, "b": _StubWorker})
        names = {name for name, _ in pool.items()}
        assert names == {"a", "b"}
        assert len(list(pool.values())) == 2


# ── Lifecycle: cancel_and_join_all ──


class TestCancelAndJoinAll:
    def test_calls_cancel_on_every_worker(self):
        a, b, c = _StubWorker(), _StubWorker(), _StubWorker()
        pool = WorkerPool(registry={})
        pool._workers = {"a": a, "b": b, "c": c}

        pool.cancel_and_join_all(timeout=2.5)

        assert a.cancel_calls == [2.5]
        assert b.cancel_calls == [2.5]
        assert c.cancel_calls == [2.5]

    def test_continues_through_failing_worker(self, caplog):
        """A worker that raises on cancel must not block the others.

        The pool catches the exception, logs it at WARNING, and moves on.
        Without this, a single stuck worker would block library switch
        and the user would see a hung 'switching…' UI.
        """
        import logging

        stuck = _StubWorker(raise_on_join=True)
        ok = _StubWorker()
        pool = WorkerPool(registry={})
        pool._workers = {"stuck": stuck, "ok": ok}

        with caplog.at_level(logging.WARNING, logger="bpp.web.worker_pool"):
            pool.cancel_and_join_all(timeout=1.0)

        assert stuck.cancel_calls == [1.0]
        assert ok.cancel_calls == [1.0], "ok worker must have been joined despite stuck failure"
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "stuck worker must produce a WARNING log line"
        assert any("stuck" in r.getMessage() for r in warnings)

    def test_empty_pool_is_noop(self):
        pool = WorkerPool(registry={})
        # Must not raise on an empty pool.
        pool.cancel_and_join_all(timeout=1.0)


# ── WebAppState delegation (back-compat) ──


class TestWebAppStateDelegation:
    """``WebAppState._workers`` is now a property delegate to
    ``self.workers._workers``. Existing call sites that read or write
    via the legacy name must keep working transparently."""

    def test_workers_property_delegates_to_pool(self, caplog):
        # Use the same in-process pattern as test_worker_registry —
        # construct a minimal ctx with a stubbed pool.
        import logging
        from types import SimpleNamespace

        from bpp.web._deprecated_attr import _reset_logged_for_tests
        from bpp.web.state import WebAppState

        # Reset the @deprecated_attr once-per-process tracker so this
        # test's first access ALWAYS exercises the wrapper's logging
        # path even when other tests touched ctx._workers first.
        _reset_logged_for_tests()

        # The property is defined on the class so we can read it from
        # a duck-typed object that has the same attribute name.
        pool = WorkerPool(registry={"x": _StubWorker})
        fake_ctx = SimpleNamespace(workers=pool)
        # Capture the deprecation warning so it doesn't pollute pytest
        # output — the legacy-attr access here is intentional.
        with caplog.at_level(logging.WARNING, logger="bpp.web._deprecated_attr"):
            workers_dict = WebAppState._workers.fget(fake_ctx)  # type: ignore[attr-defined]
        assert workers_dict is pool._workers
        assert "x" in workers_dict
        _reset_logged_for_tests()  # cleanup for other tests


# ── Thread safety smoke check ──


class TestConcurrentCancel:
    """cancel_and_join_all has no internal lock — but each
    BackgroundWorker.cancel_and_join is itself thread-safe. The pool's
    contract is "called once per teardown event," not "called
    concurrently from multiple threads." This test just confirms the
    pool doesn't deadlock if a caller does the wrong thing."""

    def test_two_concurrent_cancel_all_calls_dont_deadlock(self):
        a = _StubWorker()
        pool = WorkerPool(registry={})
        pool._workers = {"a": a}

        errs: list[BaseException] = []

        def _go():
            try:
                pool.cancel_and_join_all(timeout=0.5)
            except BaseException as e:
                errs.append(e)

        t1 = threading.Thread(target=_go)
        t2 = threading.Thread(target=_go)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        assert not t1.is_alive() and not t2.is_alive(), "deadlock in concurrent cancel-all"
        assert errs == []
        # Worker was called twice (once per thread).
        assert len(a.cancel_calls) == 2
