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

#: After a refused full scan, the next scans are skipped for 1, 2, 4 …
#: ticks of the 5-minute sampler, up to this many (an hour).
SCAN_BACKOFF_MAX_TICKS = 12


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


def periodic_sample_tick(
    conn: sqlite3.Connection,
    dirs: dict[str, str],
    invalidate_fn: Callable[[], None],
    on_scan_refused: Callable[[tuple[int, int] | None], None] | None,
    backoff: dict[str, int] | None = None,
) -> None:
    """One periodic check: sample ~100 random photos; a missing one
    triggers the full scan. A clean sample also retires an earlier
    "scan refused" flag — with half the library gone the sample would
    have hit a missing file — so a storage outage that ended doesn't
    show as degraded until restart."""
    from bpp.db.photos import relocate_missing, sample_random_photos
    from bpp.db.photos_missing import file_present, scan_missing

    sample = sample_random_photos(conn, count=100)
    answers = [file_present(fp) for fp in sample]
    missing_in_sample = [
        fp for fp, present in zip(sample, answers, strict=True) if present is False
    ]
    if not missing_in_sample:
        # Only a sample storage actually answered for (no None) says
        # the library is back; a flapping mount must not clear the flag.
        if on_scan_refused and all(present is True for present in answers):
            on_scan_refused(None)
        if backoff is not None:
            backoff.clear()
        return
    if backoff is not None and backoff.get("skip", 0) > 0:
        # The last full scan was refused (mass-missing guard) and
        # nothing has changed: the sample still hits missing files.
        # Every 5 minutes a 200k-stat walk of a NAS is not useful.
        backoff["skip"] -= 1
        log.info(
            "Periodic check: %d/%d sampled files missing; full scan skipped (%d ticks left)",
            len(missing_in_sample),
            len(sample),
            backoff["skip"],
        )
        return
    log.warning(
        "Periodic check: %d/%d sampled files missing, running full scan",
        len(missing_in_sample),
        len(sample),
    )
    scan = scan_missing(conn, photos_dir=dirs.get("photos") or None)
    if on_scan_refused and scan.completed:
        on_scan_refused(scan.refused)
    if backoff is not None:
        if scan.completed and scan.refused:
            wait = min(max(backoff.get("wait", 0) * 2, 1), SCAN_BACKOFF_MAX_TICKS)
            backoff.update(wait=wait, skip=wait)
        else:
            backoff.clear()
    if scan.newly_missing:
        photos_dir = dirs.get("photos", "")
        if photos_dir and os.path.isdir(photos_dir):
            relocate_missing(conn, photos_dir)
        invalidate_fn()


def start_health_checks(
    get_conn: Callable[[], sqlite3.Connection],
    dirs: dict[str, str],
    invalidate_fn: Callable[[], None],
    on_scan_refused: Callable[[tuple[int, int] | None], None] | None = None,
) -> HealthCheckHandle:
    """Launch background threads for missing-file detection and periodic sampling.

    Args:
        get_conn: Callable that returns a DB connection.
        dirs: Library directory dict (needs "photos" key).
        invalidate_fn: Called when the analysis cache should be refreshed.
        on_scan_refused: Told ``(missing, total)`` when a scan refused to
            mark more than half the library missing, ``None`` when a scan
            ran clean — /api/v1/health shows it.

    Returns:
        A ``HealthCheckHandle`` with stop_event and thread references.
    """
    from bpp.db.library import backfill_sha256
    from bpp.db.photos import (
        get_photo_count,
        relocate_missing,
    )
    from bpp.db.photos_missing import scan_missing

    stop_event = threading.Event()
    threads: list[threading.Thread] = []

    # One-time full scan on startup
    def _startup_scan() -> None:
        try:
            if stop_event.is_set():
                return
            # Half-copies a killed import left in any batch folder.
            from bpp.db.library_copy import remove_stale_import_tmp

            if dirs.get("photos") and os.path.isdir(dirs["photos"]):
                remove_stale_import_tmp(dirs["photos"])
            conn = get_conn()
            if get_photo_count(conn) == 0:
                return
            scan = scan_missing(conn, photos_dir=dirs.get("photos") or None)
            if on_scan_refused and scan.completed:
                on_scan_refused(scan.refused)
            newly_missing = scan.newly_missing
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
        backoff: dict[str, int] = {}
        while not stop_event.wait(300):
            try:
                periodic_sample_tick(get_conn(), dirs, invalidate_fn, on_scan_refused, backoff)
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
