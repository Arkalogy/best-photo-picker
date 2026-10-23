"""Batches for ``WHERE x IN (...)`` queries.

SQLite caps the number of bound parameters, so a long id list is sent
``SQL_BATCH_SIZE`` values at a time. Every call site used to spell the
slicing and the placeholder string itself; this is the one copy.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import TypeVar

from bpp.constants import SQL_BATCH_SIZE

T = TypeVar("T")


def in_batches(values: Sequence[T]) -> Iterator[tuple[str, list[T]]]:
    """Yield ``(placeholders, batch)`` pairs: ``placeholders`` is the
    ``?,?,...`` string for ``batch``, a slice of *values*."""
    step = SQL_BATCH_SIZE
    for i in range(0, len(values), step):
        batch = list(values[i : i + step])
        yield ",".join("?" * len(batch)), batch
