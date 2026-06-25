"""Background file-health monitoring (missing detection, SHA-256 backfill)."""

from __future__ import annotations

import gc
import os
import sqlite3
import threading
from collections.abc import Callable
from typing import NamedTuple

from bpp.utils.logging import get_logger

log = get_logger(__name__)


class HealthCheckHandle(NamedTuple):
    """Handle returned by start_health_checks for lifecycle management."""

    stop_event: threading.Event
    threads: list[threading.Thread]

    def stop_and_join(self, timeout: float = 5.0) -> None:
        """Signal all threads to stop and wait for them to finish."""
        self.stop_event.set()
        per_thread = timeout / max(len(self.threads), 1)
        for t in self.threads:
            t.join(timeout=per_thread)


def start_health_checks(
    get_conn: Callable[[], sqlite3.Connection],
    dirs: dict[str, str],
    invalidate_fn: Callable[[], None],
) -> HealthCheckHandle:
    """Launch background threads for missing-file detection and periodic sampling.

    Args:
        get_conn: Callable that returns a DB connection.
        dirs: Library directory dict (needs "photos" key).
        invalidate_fn: Called when the analysis cache should be refreshed.

    Returns:
        A ``HealthCheckHandle`` with stop_event and thread references.
    """
    from bpp.db.library import backfill_sha256
    from bpp.db.photos import (
        check_missing,
        get_photo_count,
        relocate_missing,
        sample_random_photos,
    )

    stop_event = threading.Event()
    threads: list[threading.Thread] = []

    # One-time full scan on startup
    def _startup_scan() -> None:
        try:
            if stop_event.is_set():
                return
            conn = get_conn()
            if get_photo_count(conn) == 0:
                return
            newly_missing = check_missing(conn)
            if newly_missing:
                log.warning("Startup scan: %d files missing", len(newly_missing))
                photos_dir = dirs.get("photos", "")
                if photos_dir and os.path.isdir(photos_dir):
                    relocated = relocate_missing(conn, photos_dir)
                    if relocated:
                        log.info("Relocated %d missing files by SHA-256", relocated)
                invalidate_fn()
            else:
                log.info("Startup scan: all files present")
        except Exception:
            log.warning("Startup file scan failed", exc_info=True)

    t1 = threading.Thread(target=_startup_scan, daemon=True)
    t1.start()
    threads.append(t1)

    # SHA-256 backfill: compute hashes for photos missing them
    def _sha256_backfill() -> None:
        try:
            if stop_event.is_set():
                return
            log.info("Starting SHA-256 backfill thread")
            conn = get_conn()
            count = backfill_sha256(conn)
            log.info("SHA-256 backfill thread finished: %d photos updated", count)
        except Exception:
            log.warning("SHA-256 backfill failed", exc_info=True)

    t2 = threading.Thread(target=_sha256_backfill, daemon=True)
    t2.start()
    threads.append(t2)

    # Periodic sampling: check ~100 random photos every 5 minutes
    def _periodic_sample() -> None:
        while not stop_event.wait(300):
            try:
                conn = get_conn()
                sample = sample_random_photos(conn, count=100)
                missing_in_sample = [fp for fp in sample if not os.path.isfile(fp)]
                if missing_in_sample:
                    log.warning(
                        "Periodic check: %d/%d sampled files missing, running full scan",
                        len(missing_in_sample),
                        len(sample),
                    )
                    newly_missing = check_missing(conn)
                    if newly_missing:
                        photos_dir = dirs.get("photos", "")
                        if photos_dir and os.path.isdir(photos_dir):
                            relocate_missing(conn, photos_dir)
                        invalidate_fn()
            except Exception:
                log.warning("Periodic file check failed", exc_info=True)
            # Explicit GC pass to collect Python reference cycles. CPython's
            # automatic GC handles most allocations, but long-lived daemons
            # accumulate cycles that the generational collector misses until
            # a full collection is triggered. Running it here (once per 5-min
            # health tick) keeps the heap tidy without perceptible overhead.
            gc.collect()

    t3 = threading.Thread(target=_periodic_sample, daemon=True)
    t3.start()
    threads.append(t3)

    return HealthCheckHandle(stop_event=stop_event, threads=threads)
