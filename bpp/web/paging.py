"""``limit`` / ``offset`` query parameters for list endpoints.

A missing ``limit`` means the whole list (the historical response); a
given one is clamped to ``max_limit``. The result dict carries the page
and the fields every paginated response reports.
"""

from __future__ import annotations

from typing import Any

from flask import request


def page_args(max_limit: int = 5000) -> tuple[int | None, int]:
    """``(limit, offset)`` from the request: ``limit`` None when absent."""
    limit = request.args.get("limit", type=int)
    offset = max(0, request.args.get("offset", default=0, type=int) or 0)
    if limit is not None:
        limit = max(1, min(limit, max_limit))
    return limit, offset


def page_fields(items: list[Any], total: int, limit: int | None, offset: int) -> dict[str, Any]:
    """The pagination fields of a response: ``count`` is this page's
    size, ``total`` the whole list, ``has_more`` whether a later page
    exists."""
    return {
        "count": len(items),
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": limit is not None and offset + len(items) < total,
    }
