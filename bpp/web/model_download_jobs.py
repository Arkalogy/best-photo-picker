"""Background model-download jobs with progress.

A model download can take minutes (CLIP is ~600 MB), far longer than the
browser keeps a request open, so the Download / Redownload endpoints run
the license and status checks synchronously, then hand the fetch to a job
here and return at once. The picker polls :func:`snapshot` for progress.

One job per key at a time: starting a key that is still running joins
that job instead of downloading the same file twice.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from bpp.errors import BppError
from bpp.utils.download import report_progress
from bpp.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class DownloadJob:
    key: str
    label: str
    status: str = "running"  # running | done | error
    bytes_done: int = 0
    bytes_total: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    #: Where the job is: ``starting`` (before the fetch: checks, locks),
    #: ``downloading`` (bytes arriving), ``finishing`` (every byte in;
    #: the file is being verified or unpacked). Set by the fetch itself.
    phase: str = "starting"
    #: Last sign of life (a byte, a phase change, a verification step):
    #: the picker's stall clock runs from this, not from the byte count,
    #: so a healthy wait before the first byte or after the last one
    #: isn't called "no progress".
    updated_at: float = field(default_factory=time.time)

    def _progress(self, done: int, total: int | None) -> None:
        self.bytes_done = done
        self.bytes_total = total
        self.phase = "finishing" if total is not None and done >= total else "downloading"
        self.updated_at = time.time()

    def _phase(self, name: str) -> None:
        self.phase = name
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.key,
            "label": self.label,
            "status": self.status,
            "phase": self.phase,
            "updated_at": self.updated_at,
            "bytes_done": self.bytes_done,
            "bytes_total": self.bytes_total,
            "result": self.result,
            "error": self.error,
        }


_lock = threading.Lock()
_jobs: dict[str, DownloadJob] = {}


def running_labels() -> list[str]:
    """What's downloading right now (for the installer's "wait for …")."""
    with _lock:
        return [f"the {job.label} download" for job in _jobs.values() if job.status == "running"]


def start(key: str, label: str, work: Callable[[], dict[str, Any]]) -> tuple[DownloadJob, bool]:
    """Run ``work`` in a background thread unless ``key`` is already running.

    Returns ``(job, started)``; ``started`` is False when an in-flight job
    for the same key was joined instead.
    """
    # (An install waits for running downloads; a download's own package
    # checks go through the install hold, so no blanket refusal here.)
    with _lock:
        existing = _jobs.get(key)
        if existing is not None and existing.status == "running":
            return existing, False
        job = DownloadJob(key=key, label=label)
        _jobs[key] = job

    def _run() -> None:
        log.info("Download job started: %s (%s)", key, label)
        try:
            with report_progress(job._progress, on_phase=job._phase):
                job.result = work()
            job.status = "done"
            log.info("Download job finished: %s", key)
        except BppError as exc:
            job.error = exc.user_message or str(exc)
            job.status = "error"
            log.warning("Download job failed: %s — %s", key, job.error)
        except Exception as exc:
            from bpp.utils.download import plain_reason

            job.error = f"Couldn't download {label}: {plain_reason(exc)}"
            job.status = "error"
            log.warning("Download job failed: %s", key, exc_info=True)

    threading.Thread(target=_run, name=f"download:{key}", daemon=True).start()
    return job, True


def snapshot(key: str) -> dict[str, Any] | None:
    """Current state of the job for ``key``, or None if there is none."""
    with _lock:
        job = _jobs.get(key)
    return job.to_dict() if job is not None else None


def _reset_for_tests() -> None:
    with _lock:
        _jobs.clear()
