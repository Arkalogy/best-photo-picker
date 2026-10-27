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


def rows_in(conn, sql: str, values, extra: Sequence = ()) -> Iterator[tuple]:
    """Run *sql* once per batch of *values* and yield every row. *sql* holds
    one ``{ph}`` slot for the IN list; *extra* params follow the batch.
    Duplicates are sent once. A query that groups or de-duplicates must
    key on the batched column (each group then sits in one batch);
    otherwise the caller merges the rows."""
    for ph, batch in in_batches(list(dict.fromkeys(values))):
        yield from conn.execute(sql.replace("{ph}", ph), [*batch, *extra])
