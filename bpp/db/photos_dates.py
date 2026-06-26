"""Date-keyed photo queries + date-field updates.

Extracted from :mod:`bpp.db.photos` as part of the 500-LOC cap split.
Photo dates live in three denormalized columns (``date``, ``date_day``,
``date_month``) so the day / month aggregates used by the timeline and
calendar views don't have to call ``substr(date, …)`` per row.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from bpp.constants import ACTIVE_PHOTO_SQL, active_photo_sql
from bpp.db.photos import PHOTO_COLS_SLIM


def get_date_distribution(
    conn: sqlite3.Connection, album_id: int | None = None
) -> list[dict[str, Any]]:
    """Return photo counts grouped by month.

    Returns list of {"month": "YYYY-MM", "count": N} ordered chronologically.
    Excludes deleted and missing photos, and those with no date_month.
    """
    if album_id is not None:
        rows = conn.execute(
            "SELECT p.date_month, COUNT(*) AS count FROM photos p "
            "JOIN album_photos ap ON ap.photo_id = p.id "
            f"WHERE ap.album_id = ? AND {active_photo_sql('p')} "
            "AND p.date_month IS NOT NULL "
            "GROUP BY p.date_month ORDER BY p.date_month",
            (album_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT date_month, COUNT(*) AS count FROM photos "
            f"WHERE {ACTIVE_PHOTO_SQL} "
            "AND date_month IS NOT NULL "
            "GROUP BY date_month ORDER BY date_month"
        ).fetchall()
    return [{"month": r[0], "count": r[1]} for r in rows]


def get_photos_by_date_range(
    conn: sqlite3.Connection, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Return photos whose date falls within [start_date, end_date].

    Dates are compared as strings (ISO 8601: YYYY-MM-DDTHH:MM:SS).
    Excludes deleted and missing photos.
    """
    rows = conn.execute(
        f"SELECT {PHOTO_COLS_SLIM} FROM photos "
        "WHERE date >= ? AND date <= ? "
        f"AND {ACTIVE_PHOTO_SQL} "
        "ORDER BY date",
        (start_date, end_date),
    ).fetchall()
    return [dict(r) for r in rows]


def update_photo_date(conn: sqlite3.Connection, photo_id: int, new_date: str) -> None:
    """Update a photo's date, date_day, and date_month fields.

    Args:
        conn: Database connection.
        photo_id: Photo ID.
        new_date: ISO 8601 date string (e.g. '2025-06-20T14:30:00').

    Raises:
        ValueError: If date format is invalid or photo not found.
    """
    if not re.match(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?", new_date):
        raise ValueError(f"Invalid date format: {new_date}")

    row = conn.execute("SELECT id FROM photos WHERE id=?", (photo_id,)).fetchone()
    if not row:
        raise ValueError(f"Photo {photo_id} not found")

    date_day = new_date[:10]
    date_month = new_date[:7]

    conn.execute(
        "UPDATE photos SET date=?, date_day=?, date_month=? WHERE id=?",
        (new_date, date_day, date_month, photo_id),
    )
    conn.commit()
