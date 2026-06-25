"""Safe JSON helpers for handling potentially corrupt DB fields."""

from __future__ import annotations

import json
from typing import Any

from bpp.utils.logging import get_logger

log = get_logger(__name__)


def safe_json_loads(
    text: str | None,
    default: Any = None,
    *,
    context: str = "",
) -> Any:
    """Parse JSON text, returning *default* on failure instead of crashing.

    Logs a warning with optional *context* when parsing fails.
    """
    if not text:
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        if context:
            log.warning("Corrupt JSON in %s, using default", context)
        else:
            log.warning("Corrupt JSON, using default")
        return default
