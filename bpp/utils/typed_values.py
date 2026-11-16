"""Values read from a library, checked to be the type the API promises.

SQLite keeps any text in a column declared INTEGER, REAL or TEXT, and a
library folder can come from someone else. Fields the page treats as a
number, a date or a hash are passed through these before they are sent,
so a row can't deliver other text under that name.
"""

from __future__ import annotations

import math
import re
from typing import Any

# What a stored date or month looks like: digits first, then only the
# characters date and time notations use. Not a format check (EXIF and
# ISO spellings both pass); it rules out everything else.
_DATE_TEXT = re.compile(r"[0-9][0-9A-Za-z :.+/\-]{0,39}")
_HASH_TEXT = re.compile(r"[0-9A-Fa-f]{1,128}")


def as_int(value: Any) -> int | None:
    """*value* as an int, or None when it isn't a whole number."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value == int(value):
        return int(value)
    return None


def as_float(value: Any) -> float | None:
    """*value* as a finite float, or None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def as_date_text(value: Any) -> str:
    """A stored date, day or month as text, or "" when it isn't one."""
    return value if isinstance(value, str) and _DATE_TEXT.fullmatch(value) else ""


def as_hash(value: Any) -> str | None:
    """A stored hex digest, or None when the value isn't one."""
    return value if isinstance(value, str) and _HASH_TEXT.fullmatch(value) else None
