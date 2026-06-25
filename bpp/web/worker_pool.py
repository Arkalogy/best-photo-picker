"""P4 — WebAppState collaborator: the worker pool.

Until P4 the live workers lived as a bare ``dict[str, BackgroundWorker]``
hanging off ``WebAppState._workers``. Two separate loops in
``state_lifecycle`` iterated it for cancel+join (one in
``switch_library``, one in ``shutdown``) — a classic shape where
adding a new worker means also remembering to teach both loops about
its lifecycle.

This module owns:

* :class:`WorkerPool` — a thin, dict-like container that:
    * builds workers from :class:`bpp.web.worker_registry.WorkerRegistry`
      at construction,
    * exposes ``__getitem__`` / ``__contains__`` / ``items`` / ``values``
      so call sites can keep treating it as a dict during the
      migration window,
    * owns the single ``cancel_and_join_all(timeout)`` helper used by
      both ``switch_library`` and ``shutdown``.

``WebAppState._workers`` becomes a property delegating to
``self.workers._workers`` so existing callers (``bp_health``,
``state_lifecycle``, ``test_worker_registry``, ``test_media_state``)
keep working unchanged during the deprecation window.

See ``docs/architecture-notes.md`` for the P4 collaborator surface
(WorkerPool, ModelCache, AnalysisStore, LibraryLifecycle).
"""

from __future__ import annotations

from collections.abc import ItemsView, Iterable, Iterator, ValuesView
from typing import TYPE_CHECKING, Any

from bpp.utils.logging import get_logger

if TYPE_CHECKING:
    from bpp.web.base_worker import BackgroundWorker

log = get_logger(__name__)


class WorkerPool:
    """Holds the live :class:`BackgroundWorker` instances for one library.

    Workers are constructed once per pool — i.e. once per ``WebAppState``,
    which is built fresh at each library switch. The lifetime is the
    library, not the process: switching libraries discards the old
    pool (after cancelling + joining every worker) and constructs a
    new one against the new library's config / db / dirs.

    Dict-like surface kept intentionally minimal — call sites that
    need more are pushing complexity into the wrong layer. Currently
    the only operations production code performs are:

    * keyed lookup (``pool["analyze"]``)
    * full iteration (``pool.values()`` for cancel+join,
      ``pool.items()`` for the health-status snapshot)
    * membership (``"stub" in pool`` in tests)
    * assignment (``pool["analyze"] = stub`` in tests)
    """

    def __init__(self, registry: Any = None) -> None:
        """Build the pool by calling every factory in ``registry``.

        ``registry`` defaults to the global
        :class:`bpp.web.worker_registry.WorkerRegistry` (a class whose
        ``.items()`` classmethod returns ``(name, factory)`` pairs).
        Tests inject a plain ``dict[str, Callable[[], BackgroundWorker]]``;
        production code passes ``None`` and the registry class is used.

        Both shapes work because we only call ``.items()`` on it.
        """
        if registry is None:
            from bpp.web.worker_registry import WorkerRegistry

            registry = WorkerRegistry
        pairs: Iterable[tuple[str, Any]] = registry.items()
        self._workers: dict[str, BackgroundWorker] = {name: factory() for name, factory in pairs}

    # ── dict-like surface ──

    def __getitem__(self, name: str) -> BackgroundWorker:
        return self._workers[name]

    def __setitem__(self, name: str, worker: BackgroundWorker) -> None:
        self._workers[name] = worker

    def __contains__(self, name: object) -> bool:
        return name in self._workers

    def __iter__(self) -> Iterator[str]:
        return iter(self._workers)

    def __len__(self) -> int:
        return len(self._workers)

    def values(self) -> ValuesView[BackgroundWorker]:
        return self._workers.values()

    def items(self) -> ItemsView[str, BackgroundWorker]:
        return self._workers.items()

    def get(self, name: str, default: BackgroundWorker | None = None) -> BackgroundWorker | None:
        return self._workers.get(name, default)

    # ── lifecycle ──

    def cancel_and_join_all(self, timeout: float) -> None:
        """Cancel every worker, wait up to ``timeout`` seconds for each
        to finish. Continues through workers that fail to join — the
        per-worker timeout is the bound, and a stuck worker shouldn't
        block teardown of the others.

        Used by both ``switch_library`` (so the old library's workers
        release DB connections before the connection pool is replaced)
        and ``shutdown`` (for graceful Ctrl-C / SIGTERM handling).
        """
        for name, worker in self._workers.items():
            try:
                worker.cancel_and_join(timeout=timeout)
            except Exception:
                log.warning(
                    "Worker %r failed to cancel+join within %.1fs",
                    name,
                    timeout,
                    exc_info=True,
                )
