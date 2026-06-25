"""Shared utilities for deduplication modules."""

from __future__ import annotations

import datetime


def parse_date(date_str: str) -> datetime.datetime:
    """Parse an ISO-format date string."""
    return datetime.datetime.fromisoformat(date_str)


def within_time_window(d1: str, d2: str, window_seconds: int) -> bool:
    """Check if two date strings are within *window_seconds* of each other."""
    try:
        dt1 = parse_date(d1)
        dt2 = parse_date(d2)
        return abs((dt1 - dt2).total_seconds()) <= window_seconds
    except (ValueError, TypeError):
        return False
