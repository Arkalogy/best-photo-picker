"""Ensure every BackgroundWorker subclass is registered in WorkerRegistry.

If you add a new worker and forget to register it, this test fails —
preventing zombie threads on shutdown/library-switch.

Plus a small set of plugin-API tests for WorkerRegistry itself
(register collision, replace=True, idempotent re-registration).
"""

from __future__ import annotations

import ast
import os

import pytest

from bpp.web.state import WorkerRegistry


def _find_worker_subclasses():
    """Scan bpp/web/ for classes inheriting from BackgroundWorker."""
    web_dir = os.path.join(os.path.dirname(__file__), "..", "bpp", "web")
    subclasses = set()
    for fname in os.listdir(web_dir):
        if not fname.endswith(".py") or fname.startswith("__"):
            continue
        path = os.path.join(web_dir, fname)
        with open(path) as f:
            try:
                tree = ast.parse(f.read())
            except SyntaxError:
                continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    base_name = None
                    if isinstance(base, ast.Name):
                        base_name = base.id
                    elif isinstance(base, ast.Attribute):
                        base_name = base.attr
                    if base_name == "BackgroundWorker":
                        subclasses.add(node.name)
    return subclasses


def test_all_workers_registered():
    """Every BackgroundWorker subclass must be registered in WorkerRegistry.

    The registry holds factories (typically the class itself); we check
    by class name to be tolerant of factories that wrap or alias the
    underlying class.
    """
    subclasses = _find_worker_subclasses()
    registered_names = {
        f.__name__ if hasattr(f, "__name__") else type(f).__name__ for f in WorkerRegistry.values()
    }
    missing = subclasses - registered_names
    assert not missing, (
        f"BackgroundWorker subclass(es) not registered in WorkerRegistry: "
        f"{missing}. Call WorkerRegistry.register(name, ClassName) in state.py."
    )


# ─── Plugin API ──────────────────────────────────────────────────────


@pytest.fixture(autouse=False)
def _isolate_registry():
    yield
    WorkerRegistry._reset_for_tests()


class TestWorkerRegistryAPI:
    def test_register_new_worker(self, _isolate_registry):
        class MyWorker:
            pass

        WorkerRegistry.register("my_worker", MyWorker)
        assert WorkerRegistry.get("my_worker") is MyWorker

    def test_register_collision_raises(self, _isolate_registry):
        WorkerRegistry.register("custom", lambda: None)
        with pytest.raises(ValueError, match="already registered"):
            WorkerRegistry.register("custom", lambda: None)

    def test_register_replace_overrides(self, _isolate_registry):
        def first():
            return "first"

        def second():
            return "second"

        WorkerRegistry.register("custom", first)
        WorkerRegistry.register("custom", second, replace=True)
        assert WorkerRegistry.get("custom")() == "second"

    def test_register_same_factory_is_idempotent(self, _isolate_registry):
        def f():
            return None

        WorkerRegistry.register("custom", f)
        WorkerRegistry.register("custom", f)  # no error

    def test_reset_drops_plugins_keeps_builtins(self, _isolate_registry):
        WorkerRegistry.register("plugin_worker", lambda: None)
        # SIM118 wants `in WorkerRegistry` but the registry is a class
        # with explicit dict-like classmethods, not an instance — keep .keys().
        assert "plugin_worker" in WorkerRegistry.keys()  # noqa: SIM118
        WorkerRegistry._reset_for_tests()
        assert "plugin_worker" not in WorkerRegistry.keys()  # noqa: SIM118
        # Built-ins survive
        for builtin in ("analyze", "face", "import", "clip"):
            assert builtin in WorkerRegistry.keys()  # noqa: SIM118

    def test_webappstate_picks_up_registered_workers(self, _isolate_registry, tmp_path):
        """End-to-end: register a worker, instantiate WebAppState,
        verify it appears in self._workers."""
        from bpp.web.app import create_app

        class StubWorker:
            def __init__(self):
                self.cancelled = False

            def cancel_and_join(self, timeout=None):
                self.cancelled = True

        WorkerRegistry.register("stub", StubWorker)
        app = create_app(workdir=str(tmp_path / "wd"), library_path=str(tmp_path))
        ctx = app.extensions["bpp"]
        assert "stub" in ctx._workers
        assert isinstance(ctx._workers["stub"], StubWorker)
