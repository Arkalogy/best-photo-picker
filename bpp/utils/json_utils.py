"""Safe JSON helpers for handling potentially corrupt DB fields."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import Any

from bpp.utils.logging import get_logger

log = get_logger(__name__)


_BAD = object()


def _parse(text: Any, expected_type: type | tuple[type, ...]) -> tuple[Any, str | None]:
    """``(value, None)``, or ``(_BAD, why)`` when *text* can't be read as
    *expected_type*."""
    if not isinstance(text, str | bytes | bytearray) and isinstance(text, expected_type):
        return text, None  # already decoded (e.g. a JSON column a driver parsed)
    try:
        value = json.loads(text)
    except (ValueError, TypeError, RecursionError):  # ValueError covers JSONDecodeError
        return _BAD, "corrupt JSON"
    if not isinstance(value, expected_type):
        return _BAD, f"unexpected JSON shape ({type(value).__name__})"
    return value, None


def safe_json_loads(
    text: Any,
    default: Any = None,
    *,
    expected_type: type | tuple[type, ...],
    context: str = "",
) -> Any:
    """Parse JSON text, returning *default* on failure instead of crashing.

    Also returns *default* when the JSON is valid but not *expected_type*
    (``[]`` where an object was expected would otherwise crash the caller
    with ``AttributeError`` on ``.get``). *expected_type* is required —
    pass ``object`` when any shape is fine. Never raises: corrupt text,
    undecodable bytes (``json`` accepts UTF-8, UTF-16 and UTF-32), a
    non-text value of the wrong type, or absurdly deep nesting all give
    *default*; a value that's already decoded and of *expected_type* is
    returned as is. Logs a warning with the optional *context*.
    """
    if text is None or text == "" or text == b"":
        return default
    value, why = _parse(text, expected_type)
    if why is not None:
        log.warning("%s in %s, using default", why.capitalize(), context or "a field")
        return default
    return value


#: Files already reported as unreadable or damaged — some (the use-context
#: file) are read on every model license check; one warning per file is
#: enough until it reads cleanly again.
_reported: set[str] = set()


def _warn_once(where: str, message: str) -> None:
    if where not in _reported:
        _reported.add(where)
        log.warning("%s (%s), using default", message, where)


def load_json_file(
    path: str | os.PathLike,
    default: Any,
    *,
    expected_type: type | tuple[type, ...],
    context: str = "",
) -> Any:
    """Read and parse a JSON file with :func:`safe_json_loads` semantics:
    a missing, unreadable, corrupt or wrong-shape file gives *default*
    (warned once per file until it reads cleanly again)."""
    where = f"{context} ({os.fspath(path)})" if context else os.fspath(path)
    try:
        with open(path, "rb") as f:
            text = f.read()
    except FileNotFoundError:
        return default
    except OSError as exc:
        _warn_once(where, f"Can't read: {exc}")
        return default
    if not text:
        return default
    value, why = _parse(text, expected_type)
    if why is not None:
        _warn_once(where, why.capitalize())
        return default
    _reported.discard(where)  # readable again: report the next failure
    return value


def move_aside(path: str | os.PathLike, *, context: str = "") -> str | None:
    """Move a damaged file to ``<path>.corrupt-<time>`` so it's kept, not
    overwritten or deleted. Returns the new name, or ``None`` if the file
    was already gone. Raises ``OSError`` if it can't be moved."""
    dest = f"{os.fspath(path)}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10**6}"
    try:
        os.replace(path, dest)
    except FileNotFoundError:
        return None  # gone meanwhile (another save moved it): nothing to keep
    log.warning("%s was damaged; kept it as %s", context or os.fspath(path), dest)
    return dest


_UNREADABLE = object()


def _passes(is_valid: Callable[[Any], bool], value: Any) -> bool:
    # A check that trips over odd content means "not intact": keep a copy
    # and save, never block every save of a damaged file.
    try:
        return bool(is_valid(value))
    except Exception:
        return False


def load_for_rewrite(
    path: str | os.PathLike,
    default: Any,
    *,
    expected_type: type | tuple[type, ...],
    is_valid: Callable[[Any], bool] | None = None,
    context: str = "",
) -> Any:
    """Read *path* just before overwriting it; return its value, or
    *default* when it's missing.

    If it exists but can't be parsed as *expected_type* (or fails
    *is_valid*), move it to ``<path>.corrupt-<time>`` and return
    *default*. A reader falls back to an empty default for such a file,
    so saving straight over it would silently erase whatever the damaged
    file still held.

    Raises ``OSError`` if the file can't be read (permission, I/O error,
    locked by another program) or moved: a failed save beats replacing a
    healthy file that was only unreadable for a moment.
    """
    where = f"{context} ({os.fspath(path)})" if context else os.fspath(path)
    try:
        with open(path, "rb") as f:
            text = f.read()
    except FileNotFoundError:
        return default
    value = safe_json_loads(text, _UNREADABLE, context=where, expected_type=expected_type)
    if value is not _UNREADABLE and (is_valid is None or _passes(is_valid, value)):
        return value
    move_aside(path, context=where)
    return default


def set_aside_if_unreadable(
    path: str | os.PathLike,
    *,
    expected_type: type | tuple[type, ...],
    is_valid: Callable[[Any], bool] | None = None,
    context: str = "",
) -> None:
    """:func:`load_for_rewrite` for writers that don't need the old value."""
    load_for_rewrite(path, None, expected_type=expected_type, is_valid=is_valid, context=context)
