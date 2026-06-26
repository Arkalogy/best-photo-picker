"""Picklable ``ConfigSnapshot`` for subprocess boundaries.

Wraps the existing ``_snapshot_config`` helper in a typed dataclass so
the contract is enforceable: the parent flattens a live :class:`Config`
into a ``ConfigSnapshot`` before pickling, and the child receives a
typed read-only view.

──────────────────────────────────────────────────────────────────────
WHY THIS EXISTS
──────────────────────────────────────────────────────────────────────
The runtime :class:`bpp.config_resolver.Config` holds a bound method
(``_get_conn``) for lazy DB-layer resolution. Bound methods drag their
owner's class dict through pickle, and ``cls.__dict__`` is a
``mappingproxy`` — which the spawn-method ``ForkingPickler`` refuses
with ``TypeError: cannot pickle 'mappingproxy' object``. The subprocess
crashes silently *before* the worker function runs, and the parent
sees a timeout instead of a useful traceback.

``_snapshot_config`` has been the fix since the first multiprocessing
crossing. This wrapper makes it explicit and gives us a test surface:
``test_config_snapshot_is_pickleable`` actually round-trips through
``multiprocessing.spawn``, so a future contributor adding a bound
method to ``Config`` gets a loud failure in CI instead of a silent
production timeout.

──────────────────────────────────────────────────────────────────────
USAGE
──────────────────────────────────────────────────────────────────────

    from bpp.utils.config_snapshot import ConfigSnapshot

    snapshot = ConfigSnapshot.from_live(ctx.config)
    proc = multiprocessing.Process(
        target=worker, args=(images, snapshot.values, db_path, queue),
    )

The ``.values`` attribute is a plain ``dict`` — the same shape every
existing worker has been consuming, so this is a drop-in replacement
for the old ``config_snapshot = _snapshot_config(config)`` pattern.

Idempotent on plain dicts (passing a dict in returns a snapshot wrapping
a defensive copy of that dict).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ConfigSnapshot:
    """Immutable, picklable snapshot of a :class:`Config` or dict.

    ``values`` is a plain ``dict`` — no bound methods, no class refs,
    no mappingproxy. Safe to pickle through ``multiprocessing.spawn``.
    """

    values: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_live(cls, config: Any) -> ConfigSnapshot:
        """Flatten a live ``Config`` (or any dict-like) into a snapshot.

        Idempotent — passing a plain ``dict`` returns a snapshot wrapping
        a defensive copy.
        """
        if isinstance(config, dict):
            return cls(values=dict(config))
        as_dict = getattr(config, "as_dict", None)
        if callable(as_dict):
            return cls(values=dict(as_dict()))
        # Last-resort fallback: enumerate via items() (Mapping protocol).
        return cls(values={k: v for k, v in config.items()})

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def __contains__(self, key: object) -> bool:
        return key in self.values
