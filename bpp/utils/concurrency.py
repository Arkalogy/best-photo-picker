"""Concurrency helpers for parallel image processing."""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from typing import Any

from bpp.constants import MAX_WORKER_PROCESSES, SEQUENTIAL_THRESHOLD
from bpp.utils.logging import get_logger

log = get_logger(__name__)

#: Jobs kept queued per worker: enough that no worker waits for its next
#: item, few enough that a 50k-photo run doesn't hold 50k pending jobs
#: (and Ctrl+C stops after those few instead of running every queued job).
QUEUED_PER_WORKER = 2


def get_worker_count(requested: int = 0) -> int:
    """Determine number of worker processes.

    0 means auto (cpu_count / 2, min 1, max 8).
    """
    if requested > 0:
        return requested
    cpus = os.cpu_count() or 2
    return max(1, min(cpus // 2, MAX_WORKER_PROCESSES))


def _run_one(
    func: Callable[..., Any], item: Any, fatal_exceptions: tuple[type[BaseException], ...]
) -> Any:
    """``func(item)``; a non-fatal failure gives None with a warning."""
    try:
        return func(item)
    except fatal_exceptions:
        raise
    except Exception as e:
        log.warning("Worker failed for item %s: %s", item, e, exc_info=True)
        return None


def parallel_map(
    func: Callable[..., Any],
    items: list[Any],
    workers: int = 0,
    on_done: Callable[[], None] | None = None,
    fatal_exceptions: tuple[type[BaseException], ...] = (),
) -> list[Any]:
    """Process items in parallel using ProcessPoolExecutor.

    func must accept a single item and return a result dict.
    Items should be lightweight (file paths, not images).
    Returns results in same order as items. An item whose job raised
    gives None (with a warning) — except *fatal_exceptions*, which stop
    the whole run (e.g. a model that failed its integrity check). Same
    rules with one worker or many. *on_done* is called once per finished
    item (progress). A worker process that dies stops the run with a
    RuntimeError saying so.
    """
    n_workers = get_worker_count(workers)

    if n_workers == 1 or len(items) <= SEQUENTIAL_THRESHOLD:
        results = []
        for item in items:
            results.append(_run_one(func, item, fatal_exceptions))
            if on_done is not None:
                on_done()
        return results

    results: list[Any] = [None] * len(items)
    window = n_workers * QUEUED_PER_WORKER
    pending: dict[Future, int] = {}
    next_idx = 0
    finished = 0
    try:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            while next_idx < len(items) or pending:
                # Top the queue up to the window, then wait for any job to end.
                while next_idx < len(items) and len(pending) < window:
                    pending[pool.submit(func, items[next_idx])] = next_idx
                    next_idx += 1
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    idx = pending.pop(future)
                    try:
                        results[idx] = future.result()
                    except (BrokenProcessPool, *fatal_exceptions):
                        raise
                    except Exception as e:
                        # The remote traceback rides along as __cause__.
                        log.warning("Worker failed for item %s: %s", items[idx], e, exc_info=True)
                        results[idx] = None
                    finished += 1
                    if on_done is not None:
                        on_done()
    except BrokenProcessPool as exc:
        # Not necessarily memory: a crash in a native library, or Ctrl+C
        # reaching the workers first, look the same from here.
        raise RuntimeError(
            f"A worker process stopped unexpectedly (out of memory, a crash, or an "
            f"interrupt) after {finished} of {len(items)} items — re-run to continue"
        ) from exc

    return results
