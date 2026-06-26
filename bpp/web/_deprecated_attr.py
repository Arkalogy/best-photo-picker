"""P4 — once-per-process deprecation warnings for legacy attribute access.

Several ``WebAppState`` attributes moved into collaborators
(:class:`WorkerPool`, :class:`ModelCache`, :class:`AnalysisStore`,
:class:`LibraryLifecycle`) during P4. The audit found ~3,800 access
sites; migrating all of them in one pass would mass-break the suite,
so P4 ships @property delegates that keep the old reads + writes
working.

A property without a deprecation signal is silent — call sites have
no incentive to migrate. ``@deprecated_attr`` wraps a property so the
first access per (attribute, process) logs a WARNING with the new
path. Once-per-process keeps the log volume bounded.

Usage::

    class WebAppState:
        @property
        @deprecated_attr("self.workers (P4 collaborator)")
        def _workers(self) -> dict[str, BackgroundWorker]:
            return self.workers._workers

The decorator order matters: ``@property`` outermost, then
``@deprecated_attr``. The inner decorator wraps the getter; the
outer decorator turns the wrapped getter into a property.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from bpp.utils.logging import get_logger

log = get_logger(__name__)

_T = TypeVar("_T")

# Track which attributes have already logged this process. The lock is
# only needed because two threads could race on a fresh attribute; once
# the set entry exists the lock-free read fast-paths.
_logged: set[str] = set()
_lock = threading.Lock()


def _reset_logged_for_tests() -> None:
    """Test-only: clear the once-per-process log tracker so a fixture
    can assert that the FIRST access on a fresh property fires the
    warning."""
    with _lock:
        _logged.clear()


def deprecated_attr(
    new_path: str,
) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Decorator: log a one-shot WARNING the first time the wrapped
    property getter fires per process.

    ``new_path`` is the human-readable name of the recommended new
    access site (e.g. ``"ctx.workers (P4 WorkerPool)"``). The log
    message includes the attribute's own ``__qualname__`` so the
    operator can see both the old name AND the new path.

    The warning fires AT MOST once per attribute per process — first
    access pays the lock + set add; subsequent accesses are lock-free
    reads.
    """

    def _decorate(getter: Callable[..., _T]) -> Callable[..., _T]:
        attr_name = getter.__qualname__

        @wraps(getter)
        def _wrapper(self: Any) -> _T:
            if attr_name not in _logged:
                # Slow path: lock + check + add + warn. Done at most
                # once per attribute per process.
                with _lock:
                    if attr_name not in _logged:
                        _logged.add(attr_name)
                        log.warning(
                            "%s is deprecated; use %s instead. Warning fires once per process.",
                            attr_name,
                            new_path,
                        )
            return getter(self)

        return _wrapper

    return _decorate
