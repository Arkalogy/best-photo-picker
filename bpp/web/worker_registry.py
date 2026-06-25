"""Background worker registry — the plugin-facing API for adding worker kinds.

Extracted from `state.py` during the v0.1 cleanup. The registry was
about ~75 LOC of class definitions + built-in registrations that had
no actual dependency on `WebAppState` itself; keeping them in state.py
just inflated that file. Moving the registry out also gives plugin
authors a single import target (`from bpp.web.worker_registry import
WorkerRegistry`) instead of pulling from the kitchen-sink `state`
module.

`WebAppState.__init__` calls `WorkerRegistry.items()` to build its
per-instance `_workers` dict, so the registry is loaded before any
state is instantiated — the import graph stays acyclic.
"""

from __future__ import annotations

from typing import Any, ClassVar

from bpp.web.analyze_worker import AnalyzeWorker
from bpp.web.clip_worker import ClipWorker
from bpp.web.export_worker import ExportWorker
from bpp.web.face_worker import FaceWorker
from bpp.web.import_worker import ImportWorker


class WorkerRegistry:
    """Mutable registry of background-worker factories.

    Each entry is `name → factory()` where factory is a zero-arg
    callable returning a fresh `BackgroundWorker`. Worker instances
    are per-WebAppState (one set per library), so the registry holds
    factories rather than instances — `WebAppState.__init__` calls
    each factory once at startup.

    Plugins can register new worker kinds via:

        WorkerRegistry.register("my_worker", MyWorker)

    The cancel-on-shutdown loop and library-switch drain in
    `WebAppState` iterate `self._workers`, which is built from the
    registry — so a registered worker gets cancel/join lifecycle for
    free.

    `tests/test_worker_registry.py` enforces the invariant that every
    BackgroundWorker subclass in the codebase is registered.
    """

    _factories: ClassVar[dict[str, Any]] = {}
    _builtin_keys: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def register(cls, name: str, factory: Any, *, replace: bool = False) -> None:
        """Register a worker factory.

        Default is collision-safe: re-registering the same factory is
        a no-op, but a different factory for the same name raises.
        Pass `replace=True` to override unconditionally (test-only path).
        """
        existing = cls._factories.get(name)
        if existing is not None and existing is not factory and not replace:
            raise ValueError(
                f"Worker {name!r} already registered with a different "
                "factory (pass replace=True if intentional)"
            )
        cls._factories[name] = factory

    @classmethod
    def get(cls, name: str) -> Any:
        return cls._factories.get(name)

    @classmethod
    def items(cls) -> Any:
        return cls._factories.items()

    @classmethod
    def keys(cls) -> Any:
        return cls._factories.keys()

    @classmethod
    def values(cls) -> Any:
        return cls._factories.values()

    @classmethod
    def _reset_for_tests(cls) -> None:
        """Roll back to the built-in set."""
        cls._factories = {k: v for k, v in cls._factories.items() if k in cls._builtin_keys}


# Built-in registrations. Calling .register() directly here lets the
# registry's collision-safety apply to even the built-ins.
_BUILTIN_WORKERS: tuple[tuple[str, Any], ...] = (
    ("analyze", AnalyzeWorker),
    ("face", FaceWorker),
    ("import", ImportWorker),
    ("clip", ClipWorker),
    ("export", ExportWorker),
)
for _name, _factory in _BUILTIN_WORKERS:
    WorkerRegistry.register(_name, _factory)
WorkerRegistry._builtin_keys = frozenset(name for name, _ in _BUILTIN_WORKERS)
