"""Free helpers used by state.py — extracted to keep WebAppState focused.

These functions and the AppState TypedDict have no dependency on the
WebAppState class itself, so they don't need to live in the same
module. Extracting them shrinks state.py and gives each helper a
sensible home that doesn't pull the whole state surface into scope
when imported.
"""

from __future__ import annotations

import os
from typing import Any, TypedDict

from bpp.utils.logging import get_logger

log = get_logger(__name__)


class AppState(TypedDict):
    """Typed schema for the shared app state dict."""

    input_dir: str | None
    workdir: str | None
    library_path: str
    config: dict[str, Any]
    analysis: list[dict[str, Any]] | None
    extensions: list[str]


# Bounds for API parameter validation
_WEIGHT_MIN, _WEIGHT_MAX = 0.0, 10.0
_K_MIN, _K_MAX = 1, 10000


def clamp_weight(val: Any) -> float:
    """Clamp a weight parameter to valid range."""
    return max(_WEIGHT_MIN, min(float(val), _WEIGHT_MAX))


def clamp_k(val: Any, default: int = 50) -> int:
    """Clamp k parameter to valid range."""
    try:
        return max(_K_MIN, min(int(val), _K_MAX))
    except (TypeError, ValueError):
        return default


def heic_available() -> bool:
    """Return True if pillow_heif is importable.

    Determines whether HEIC/HEIF source files can be opened. Used by
    the import path to decide whether to surface a "HEIC support
    unavailable" warning, and by the face-crop path to decide whether
    EXIF transpose can run on HEIF photos.
    """
    try:
        import pillow_heif  # noqa: F401

        return True
    except ImportError:
        return False


def consume_restore_sentinel(db_p: str) -> bool:
    """Try to atomically consume a `.restore-pending` sentinel.

    Returns True if the sentinel was present AND successfully renamed
    (caller should skip the next backup rotation). Returns False if
    the sentinel didn't exist OR the rename failed (caller falls
    through to normal backup rotation).

    Why rename, not remove: the skip side effect must be gated on a
    successful consumption. With `os.remove` + skip, a transient
    permission error on the remove would leave the sentinel in place
    AND skip the rotation — every subsequent startup would then
    repeat the skip indefinitely (one-shot turns into "skip forever").
    `os.replace` is atomic on POSIX/Windows, so either the sentinel
    is renamed (consumed exactly once) or it's not (we treat as
    no-sentinel and fall through).

    The consumed marker (`<db>.restore-pending.consumed`) is then
    best-effort deleted. Even if that delete fails, future startups
    don't react to `.consumed` — the path is harmless residue.
    """
    sentinel = db_p + ".restore-pending"
    if not os.path.isfile(sentinel):
        return False
    consumed_path = sentinel + ".consumed"
    try:
        os.replace(sentinel, consumed_path)
    except OSError as e:
        log.warning(
            "Could not consume restore sentinel %s: %s — "
            "falling through to normal backup rotation. "
            ".backup.prev may be overwritten.",
            sentinel,
            e,
        )
        return False
    log.info(
        "Restore-pending sentinel consumed (renamed to %s) — "
        "skipping backup rotation to preserve .backup.prev",
        consumed_path,
    )
    # Best-effort cleanup. Subsequent startups don't react to
    # .consumed so a leak here is harmless.
    try:
        os.remove(consumed_path)
    except OSError:
        log.warning("Could not delete consumed sentinel %s", consumed_path, exc_info=True)
    return True
