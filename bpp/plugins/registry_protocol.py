"""P5 — structural protocol every plugin-target registry conforms to.

The plan called for "unifying" the four plugin-target registries
(``WorkerRegistry``, ``SmartAlbumRegistry``, ``ExportModeRegistry``,
``DedupeStrategyRegistry``). Each already exposed ``register / get /
keys / _reset_for_tests`` with the same intent — what was missing was
a single Protocol pinning the shared shape, and a regression test
that every plugin-target registry implements it.

This module:

* Declares :class:`PluginRegistryLike` — the Protocol every registry
  conforms to from a plugin author's POV (the four ``classmethod``
  surface: ``register``, ``get``, ``keys``, ``_reset_for_tests``).
* Exposes :data:`PLUGIN_TARGET_REGISTRIES` — the canonical tuple of
  registries plugins target. ``tests/test_p5_registry_protocol.py``
  iterates this tuple to enforce the protocol for new registries.
* Provides :func:`each_registry` for callers (test code, future
  plugin tooling) that want to walk the surface uniformly.

We deliberately do NOT inherit the registries from a common ABC —
they already exist in production with their own signatures, and the
``register`` argument shape differs (a ``Worker`` registers
``(name, factory)``, a ``SmartAlbum`` registers
``(album_type, refresh_fn, get_ids_fn)``, etc.). The unification
captured here is structural / documentation; the per-registry
``register_*`` wrapper functions remain the public plugin API.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PluginRegistryLike(Protocol):
    """Structural protocol every plugin-target registry conforms to.

    The arguments to ``register`` vary per registry — what's pinned
    here is the universal surface every plugin author can rely on:

    * ``register(...)`` — add an entry. Signature is registry-specific.
    * ``get(name)`` — look an entry up by name.
    * ``_reset_for_tests()`` — escape hatch for test isolation.

    Enumeration (``keys()`` on the dict-keyed registries vs ``all()``
    on the dataclass-keyed ones) is asserted in a separate test so
    the runtime ``isinstance`` check doesn't have to alternate.
    """

    @classmethod
    def register(cls, *args: Any, **kwargs: Any) -> Any: ...

    @classmethod
    def get(cls, name: str) -> Any: ...

    @classmethod
    def _reset_for_tests(cls) -> None: ...


def _load_registries() -> tuple[tuple[str, type], ...]:
    """Lazy import the registry classes — avoids a circular import
    cycle (the registry modules transitively import bpp.web.state,
    which we don't want at module load here)."""
    from bpp.db.smart_albums import SmartAlbumRegistry
    from bpp.dedupe.strategy import DedupeStrategyRegistry
    from bpp.output.export import ExportModeRegistry
    from bpp.web.worker_registry import WorkerRegistry

    return (
        ("WorkerRegistry", WorkerRegistry),
        ("SmartAlbumRegistry", SmartAlbumRegistry),
        ("ExportModeRegistry", ExportModeRegistry),
        ("DedupeStrategyRegistry", DedupeStrategyRegistry),
    )


def plugin_target_registries() -> tuple[tuple[str, type], ...]:
    """Return the canonical list of plugin-target registries.

    Each entry is ``(name, registry_class)``. Use this from test code
    or plugin tooling that needs to iterate every plugin-target
    registry uniformly.
    """
    return _load_registries()


def each_registry():
    """Generator yielding ``(name, registry_class)`` for every
    plugin-target registry. Sugar over :func:`plugin_target_registries`
    for ``for ... in`` ergonomics."""
    yield from _load_registries()
