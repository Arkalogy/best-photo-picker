"""Retry helpers for transient I/O failures on NAS/network storage."""

from __future__ import annotations

import errno
import os
import time
from collections.abc import Callable
from typing import TypeVar

from bpp.constants import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_BASE_DELAY_S
from bpp.utils.logging import get_logger

log = get_logger(__name__)

T = TypeVar("T")

# Errors typically caused by transient NAS issues (mount drops, SMB timeouts, etc.)
# errno constants, not numbers: ETIMEDOUT / EHOSTDOWN / ESTALE are 60 /
# 64 / 70 on macOS and 110 / 112 / 116 on Linux.
_TRANSIENT_ERRNOS = frozenset(
    {
        errno.EIO,  # I/O error
        errno.ETIMEDOUT,  # Connection timed out
        errno.EHOSTDOWN,  # Host is down
        errno.EHOSTUNREACH,  # No route to host
        errno.ENETUNREACH,  # Network is unreachable
        errno.ECONNRESET,  # Connection reset by peer
        errno.ESTALE,  # Stale file handle (NFS; macOS says "Stale NFS file handle")
    }
)


def is_transient(exc: OSError) -> bool:
    """Return True if the OSError looks transient (NAS flake, not a real bug)."""
    if exc.errno in _TRANSIENT_ERRNOS:
        return True
    msg = str(exc).lower()
    return any(
        kw in msg
        for kw in (
            "stale file handle",
            "stale nfs file handle",  # macOS wording
            "connection reset",
            "broken pipe",
            "host is down",
            "no route to host",
            "network is unreachable",
            "timed out",
            "resource temporarily unavailable",
            "input/output error",
        )
    )


def retry_io(
    func: Callable[..., T],
    *args: object,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_RETRY_BASE_DELAY_S,
    label: str = "",
    **kwargs: object,
) -> T:
    """Call *func* with retry on transient OSError.

    Uses exponential backoff: 0.5s, 1s, 2s by default.
    Non-transient errors are raised immediately.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except OSError as e:
            last_exc = e
            if attempt >= max_retries or not is_transient(e):
                raise
            delay = base_delay * (2**attempt)
            tag = label or func.__name__
            log.warning(
                "Transient I/O error in %s (attempt %d/%d, retry in %.1fs): %s",
                tag,
                attempt + 1,
                max_retries + 1,
                delay,
                e,
            )
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]  # pragma: no cover


def check_storage_accessible(path: str) -> dict[str, object]:
    """Quick check whether a storage path is accessible.

    Returns {"accessible": bool, "error": str | None, "latency_ms": float}.
    """
    start = time.monotonic()
    try:
        os.listdir(path)
        latency = (time.monotonic() - start) * 1000
        return {"accessible": True, "error": None, "latency_ms": round(latency, 1)}
    except OSError as e:
        latency = (time.monotonic() - start) * 1000
        return {
            "accessible": False,
            "error": str(e),
            "latency_ms": round(latency, 1),
        }
