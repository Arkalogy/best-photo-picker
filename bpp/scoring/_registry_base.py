"""Generic registry base shared by FaceDetectorRegistry and FaceEmbedderRegistry.

Both registries store named dataclass entries with the same CRUD pattern.
This module provides the shared boilerplate so each concrete registry
only needs to define its dataclass and module-level wrappers.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Generic, TypeVar

from bpp.utils.logging import get_logger

T = TypeVar("T")

_default_log = get_logger(__name__)


class _ScoringRegistry(Generic[T]):
    """Insertion-ordered registry for scoring pipeline entries.

    Not thread-safe — registrations happen sequentially at module import time,
    so no locking is needed in practice.
    """

    def __init__(self, entry_label: str, log: logging.Logger | None = None) -> None:
        self._store: dict[str, T] = {}
        self._label = entry_label
        self._log = log or _default_log

    def register(self, entry: T, name: str) -> None:
        replacing = name in self._store
        self._store[name] = entry
        self._log.debug(
            "Registered %s %r%s",
            self._label,
            name,
            " — replacing existing entry" if replacing else "",
        )

    def get(self, name: str) -> T | None:
        return self._store.get(name)

    def list_all(self) -> list[T]:
        return list(self._store.values())

    def iter_all(self) -> Iterator[T]:
        return iter(self._store.values())
