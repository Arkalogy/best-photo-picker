"""P5b — unified plugin protocol with lifecycle hooks.

The pre-P5 plugin loader (``bpp.plugins``) supports plugins via a
single zero-arg ``setup()`` callable invoked at process startup.
That covers "register your detector / album type / worker factory at
process boot" but offers no hook for:

* per-library setup (e.g. open a per-library connection a plugin owns),
* clean teardown when the user switches library,
* clean shutdown on Ctrl-C / SIGTERM.

This module adds an opt-in protocol — :class:`Plugin` — with four
lifecycle hooks. Plugins that conform get called at the right points;
plugins that don't (the existing zero-arg ``setup()`` shape) keep
working unchanged. The protocol is a contract, not a mandate.

Lifecycle:

* ``on_register(app)`` — once per process, just after the plugin's
  setup callable returns. Receives the Flask app (None in CLI
  contexts). Use for: registering Flask blueprints, attaching
  CLI subcommands, hooking the photo event bus.
* ``on_db_restore(corrupted_sidecar_path)`` — fires once per process
  if Protection C auto-restored the DB from ``.backup`` at startup.
  Receives the path of the corrupt-DB sidecar that was kept for
  triage. Use for: invalidating plugin-owned caches that index
  rows by id (the restored DB may have a different row set than
  the corrupt one your cache was built against). Fires AFTER
  ``on_register`` and BEFORE ``on_library_open`` so the plugin can
  flush stale state before re-priming.
* ``on_library_open(ctx)`` — once per library after ``ctx.startup()``
  succeeds. Use for: opening a per-library resource (a side-cache DB,
  a model singleton), priming derived state.
* ``on_library_close(ctx)`` — once per library, fired BEFORE
  ``switch_library`` swaps DB / paths. Mirror of ``on_library_open``;
  use for closing a resource opened there.
* ``on_shutdown()`` — once per process at server / CLI exit. Mirror
  of ``on_register``.

Registration: a plugin's ``setup()`` callable creates an instance of
its plugin class and calls :func:`register_plugin` to hand it to the
host. The host stores it in a process-wide list and fires the
lifecycle hooks at the right points via :func:`fire_*` helpers.

A plugin doesn't have to subclass anything — duck-typing satisfies
the Protocol. A plugin that only needs ``on_library_open`` defines
just that method (and a no-op for the others, or omits them — the
host checks for presence before calling).

ADR: docs/adr/0003-plugin-protocol.md.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from bpp.utils.logging import get_logger

if TYPE_CHECKING:
    from flask import Flask

    from bpp.web.state import WebAppState

log = get_logger(__name__)


@runtime_checkable
class Plugin(Protocol):
    """Lifecycle protocol a plugin instance may implement.

    All four methods are optional in practice — the host checks via
    ``hasattr`` before calling. Implementing none turns this into a
    no-op "presence-only" registration (useful when a plugin only
    cares about the registry-based side-effects of its module
    import).
    """

    def on_register(self, app: Flask | None) -> None:
        """Called once at startup after the plugin's setup callable
        returns. ``app`` may be ``None`` in CLI / test contexts."""
        ...

    def on_db_restore(self, corrupted_sidecar_path: str) -> None:
        """Called once at startup if Protection C auto-restored the
        DB from ``.backup``. ``corrupted_sidecar_path`` is the path of
        the kept-for-triage ``.corrupted-{ts}`` sidecar. Use to flush
        plugin-owned caches that may reference rows that no longer
        exist (or miss rows that came back). Optional."""
        ...

    def on_library_open(self, ctx: WebAppState) -> None:
        """Called once per library, after the WebAppState's
        ``startup()`` completes."""
        ...

    def on_library_close(self, ctx: WebAppState) -> None:
        """Called once per library, before ``switch_library`` swaps
        the DB connection pool. Use to release per-library resources."""
        ...

    def on_shutdown(self) -> None:
        """Called once at process exit."""
        ...


#: Process-wide list of registered plugin instances. Append-only at
#: setup time, fully populated before any library lifecycle event
#: fires. Iteration order is registration order — first-registered
#: hooks fire first on open, last on close (mirroring stack semantics).
_PLUGINS: list[Plugin] = []
_lock = threading.Lock()

#: One-shot signal that the Protection C restore fired at startup.
#: Set by :func:`note_db_restore` (called from ``bpp.commands.serve``
#: right after the restore succeeds) and drained by
#: :func:`fire_on_db_restore_if_pending` (called from ``app.py`` after
#: ``fire_on_register`` so plugins are guaranteed to be registered
#: before the signal is consumed). ``None`` means "no restore at this
#: startup."
_pending_db_restore_path: str | None = None


def register_plugin(plugin: Plugin) -> None:
    """Register a plugin instance for lifecycle hooks.

    Idempotent on identity — re-registering the same instance is a
    no-op. Registering two different instances of the same class is
    allowed (the plugin author may have a reason).

    Plugins typically call this from their ``setup()`` entry-point
    callable: ``register_plugin(MyPlugin())``.
    """
    with _lock:
        if plugin in _PLUGINS:
            return
        _PLUGINS.append(plugin)


def _reset_plugins_for_tests() -> None:
    """Clear the plugin list AND the pending restore signal. Test-only hook."""
    global _pending_db_restore_path
    with _lock:
        _PLUGINS.clear()
        _pending_db_restore_path = None


def note_db_restore(corrupted_sidecar_path: str) -> None:
    """Stash the corrupt-DB sidecar path so the next
    :func:`fire_on_db_restore_if_pending` call fires the hook with it.

    Called from ``bpp.commands.serve`` immediately after Protection C's
    auto-restore succeeds. Plugins aren't registered yet at that point
    (they load inside ``create_app``), so we can't fire the hook
    directly — this defers it until ``fire_on_register`` runs.
    """
    global _pending_db_restore_path
    with _lock:
        _pending_db_restore_path = corrupted_sidecar_path


def fire_on_db_restore_if_pending() -> None:
    """If a restore was noted at startup, fire ``on_db_restore`` on
    every registered plugin and clear the signal. No-op when no
    restore happened.

    Best-effort: a failing hook is logged and the next plugin still
    fires (same trust contract as the other ``fire_*`` helpers).
    """
    global _pending_db_restore_path
    with _lock:
        path = _pending_db_restore_path
        _pending_db_restore_path = None
    if path is None:
        return
    for plugin in list(_PLUGINS):
        _safe_fire(plugin, "on_db_restore", path)


def fire_on_register(app: Flask | None) -> None:
    """Call ``on_register`` on every registered plugin that has it.

    Best-effort: a single plugin's exception is logged at WARNING and
    the rest still fire. Startup is never aborted by a plugin failure
    — same trust contract as ``bpp.plugins.load_plugin_entry_points``.
    """
    for plugin in list(_PLUGINS):
        _safe_fire(plugin, "on_register", app)


def fire_on_library_open(ctx: WebAppState) -> None:
    """Call ``on_library_open`` on every registered plugin that has it."""
    for plugin in list(_PLUGINS):
        _safe_fire(plugin, "on_library_open", ctx)


def fire_on_library_close(ctx: WebAppState) -> None:
    """Call ``on_library_close`` on every registered plugin that has it.

    Fires in REVERSE registration order so resources opened later are
    closed first — symmetric with stack-based context-manager
    semantics."""
    for plugin in reversed(list(_PLUGINS)):
        _safe_fire(plugin, "on_library_close", ctx)


def fire_on_shutdown() -> None:
    """Call ``on_shutdown`` on every registered plugin that has it.

    Reverse registration order (same rationale as ``on_library_close``)."""
    for plugin in reversed(list(_PLUGINS)):
        _safe_fire(plugin, "on_shutdown")


def _safe_fire(plugin: Plugin, method: str, *args: Any) -> None:
    """Invoke ``plugin.method(*args)`` if it exists; swallow and log
    any exception so one bad plugin can't break the others.

    Missing methods (the duck-typed "implements only some hooks"
    shape) are silently skipped — that's the intended affordance,
    not an error.
    """
    fn = getattr(plugin, method, None)
    if fn is None:
        return
    try:
        fn(*args)
    except Exception:
        # T4: log the module-qualified class name, not ``%r``. ``repr()``
        # on a default plugin object renders ``<pkg.mod.Cls object at
        # 0x...>``; the pointer address is noise and the bare class name
        # gets lost in it. ``module.Class`` is what on-call greps for.
        cls = type(plugin)
        qual = f"{cls.__module__}.{cls.__qualname__}"
        log.warning(
            "Plugin %s raised in %s — continuing with the next plugin",
            qual,
            method,
            exc_info=True,
        )
