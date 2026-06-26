"""P4 — WebAppState collaborator: library lifecycle.

The functions in :mod:`bpp.web.state_lifecycle` (``startup``,
``shutdown``, ``switch_library``, plus the per-step helpers) are
already cohesive — each one operates against a ``WebAppState`` and
moves the lifecycle forward. P4's plan called for promoting them into
a class so:

* Tests can construct a ``LibraryLifecycle`` against a fake ctx
  instead of having to mock out the module-level functions.
* The collaborator boundary becomes explicit in the type system —
  endpoints that need lifecycle hooks (e.g. ``ctx.switch_library``)
  have a typed object to hand to clients.

The class is a thin facade — it doesn't re-implement anything. Each
method delegates to the existing module-level function. WebAppState's
existing thin wrappers (``ctx.startup() / shutdown() / switch_library()``)
keep working unchanged.

See ``docs/architecture-notes.md`` for the P4 collaborator surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bpp.utils.logging import get_logger

if TYPE_CHECKING:
    from bpp.web.state import WebAppState

log = get_logger(__name__)


class LibraryLifecycle:
    """Library-lifecycle facade over the existing state_lifecycle module.

    Holds a reference to one :class:`WebAppState`; each method moves
    that ctx's lifecycle through one transition. The class is created
    by ``WebAppState.__init__`` so endpoints can reach it via
    ``ctx.lifecycle.switch_library(...)`` (new path) while the
    legacy ``ctx.switch_library(...)`` still works via the delegate.

    The class is intentionally thin — it owns no state beyond the
    ctx reference. All real work lives in
    :mod:`bpp.web.state_lifecycle` so the module + class can evolve
    together without breaking either entry point.
    """

    def __init__(self, ctx: WebAppState) -> None:
        self._ctx = ctx

    def startup(self) -> None:
        """Run the per-library initialization sequence (DB init,
        thumbs warm, journal recovery, file-health checks)."""
        from bpp.web import state_lifecycle as _impl

        _impl.startup(self._ctx)

    def shutdown(self) -> None:
        """Cancel all workers, drain background threads, close DB pool."""
        from bpp.web import state_lifecycle as _impl

        _impl.shutdown(self._ctx)

    def switch_library(self, new_path: str) -> None:
        """Hot-swap to a different library. Cancels workers, fires
        on_library_close plugin hooks, replaces paths + DB pool, fires
        on_library_open hooks via startup."""
        from bpp.web import state_lifecycle as _impl

        _impl.switch_library(self._ctx, new_path)

    @property
    def workdir(self) -> str:
        """Convenience read of the current library's workdir."""
        return self._ctx.paths.workdir

    @property
    def library_path(self) -> str:
        """Convenience read of the current library's root path."""
        return self._ctx.paths.library_path
