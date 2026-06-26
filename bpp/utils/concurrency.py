"""Concurrency helpers for parallel image processing."""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

from bpp.constants import MAX_WORKER_PROCESSES, SEQUENTIAL_THRESHOLD
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def get_worker_count(requested: int = 0) -> int:
    """Determine number of worker processes.

    0 means auto (cpu_count / 2, min 1, max 8).
    """
    if requested > 0:
        return requested
    cpus = os.cpu_count() or 2
    return max(1, min(cpus // 2, MAX_WORKER_PROCESSES))


def parallel_map(
    func: Callable[..., Any],
    items: list[Any],
    workers: int = 0,
) -> list[Any]:
    """Process items in parallel using ProcessPoolExecutor.

    func must accept a single item and return a result dict.
    Items should be lightweight (file paths, not images).
    Returns results in same order as items.
    """
    n_workers = get_worker_count(workers)

    if n_workers == 1 or len(items) <= SEQUENTIAL_THRESHOLD:
        # Sequential fallback
        results = []
        for item in items:
            results.append(func(item))
        return results

    results: list[Any] = [None] * len(items)
    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        future_to_idx = {pool.submit(func, item): i for i, item in enumerate(items)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                log.warning("Worker failed for item %s: %s", items[idx], e)
                results[idx] = None

    return results
