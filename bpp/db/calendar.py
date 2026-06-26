"""Calendar view data queries."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from bpp.constants import ACTIVE_PHOTO_SQL


def get_daily_counts(conn: sqlite3.Connection, year: int, month: int) -> list[dict[str, Any]]:
    """Return photo counts per day for a given year/month.

    Returns list of {"day": int, "count": int, "top_hash": str|None}
    ordered by day. Each entry includes the sha256 of the highest-scored
    photo that day for use as a thumbnail.
    """
    month_prefix = f"{year:04d}-{month:02d}"
    active = ACTIVE_PHOTO_SQL

    # Two-pass approach avoids O(N*M) correlated subquery:
    # 1. Counts per day
    count_rows = conn.execute(
        "SELECT CAST(substr(date, 9, 2) AS INTEGER) AS day, COUNT(*) AS count "
        f"FROM photos WHERE substr(date, 1, 7) = ? AND LENGTH(date) >= 10 AND {active} "
        "GROUP BY day ORDER BY day",
        (month_prefix,),
    ).fetchall()

    if not count_rows:
        return []

    # 2. Top-scored photo per day (single pass with window function)
    top_rows = conn.execute(
        "SELECT day, sha256 FROM ("
        "  SELECT CAST(substr(date, 9, 2) AS INTEGER) AS day, sha256, "
        "    ROW_NUMBER() OVER (PARTITION BY CAST(substr(date, 9, 2) AS INTEGER) "
        "      ORDER BY aggregate_score DESC) AS rn "
        f"  FROM photos WHERE substr(date, 1, 7) = ? AND LENGTH(date) >= 10 AND {active}"
        ") WHERE rn = 1",
        (month_prefix,),
    ).fetchall()
    top_map = {r["day"]: r["sha256"] for r in top_rows}

    return [
        {"day": r["day"], "count": r["count"], "top_hash": top_map.get(r["day"])}
        for r in count_rows
    ]


def get_year_daily_counts(conn: sqlite3.Connection, year: int) -> dict[int, list[dict[str, Any]]]:
    """Return daily photo counts for every month in a given year.

    Returns dict mapping month (1-12) to list of {"day": int, "count": int}.
    Omits months with no photos.  No thumbnails (used for year overview).
    """
    year_prefix = f"{year:04d}"
    active = ACTIVE_PHOTO_SQL

    rows = conn.execute(
        "SELECT CAST(substr(date, 6, 2) AS INTEGER) AS month, "
        "  CAST(substr(date, 9, 2) AS INTEGER) AS day, "
        "  COUNT(*) AS count "
        f"FROM photos WHERE substr(date, 1, 4) = ? AND LENGTH(date) >= 10 AND {active} "
        "GROUP BY month, day ORDER BY month, day",
        (year_prefix,),
    ).fetchall()

    result: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        m = r["month"]
        result.setdefault(m, []).append({"day": r["day"], "count": r["count"]})
    return result


def get_year_months(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all year/month combos that have photos.

    Returns list of {"year": int, "month": int, "count": int}
    ordered chronologically.
    """
    rows = conn.execute(
        "SELECT CAST(substr(date_month, 1, 4) AS INTEGER) AS year, "
        "  CAST(substr(date_month, 6, 2) AS INTEGER) AS month, "
        "  COUNT(*) AS count "
        "FROM photos "
        f"WHERE {ACTIVE_PHOTO_SQL} "
        "AND date_month IS NOT NULL "
        "GROUP BY date_month ORDER BY date_month"
    ).fetchall()
    return [{"year": r["year"], "month": r["month"], "count": r["count"]} for r in rows]


def get_on_this_day(
    conn: sqlite3.Connection,
    month: int | None = None,
    day: int | None = None,
    max_per_year: int = 20,
) -> list[dict[str, Any]]:
    """Return photos from the same month+day in past years, grouped by year.

    Returns list of year entries sorted most-recent first:
    [{"year": 2022, "years_ago": 4, "count": 5, "hero_hash": "abc",
      "photos": [{"id": 1, "hash": "abc", "score": 0.8, ...}, ...]}, ...]
    """
    today = date.today()
    if month is None:
        month = today.month
    if day is None:
        day = today.day

    active = ACTIVE_PHOTO_SQL
    md_str = f"{month:02d}-{day:02d}"

    # Find all photos matching this month-day, excluding current year
    rows = conn.execute(
        "SELECT id, filepath, sha256, date, aggregate_score, original_filename "
        f"FROM photos WHERE substr(date, 6, 5) = ? AND LENGTH(date) >= 10 AND {active} "
        "AND CAST(substr(date, 1, 4) AS INTEGER) != ? "
        "ORDER BY aggregate_score DESC",
        (md_str, today.year),
    ).fetchall()

    if not rows:
        return []

    # Group by year
    by_year: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        year = int(r["date"][:4])
        photo = {
            "id": r["id"],
            "hash": r["sha256"],
            "filepath": r["filepath"],
            "score": r["aggregate_score"] or 0,
            "filename": r["original_filename"] or r["filepath"].split("/")[-1],
            "date": r["date"],
        }
        by_year.setdefault(year, []).append(photo)

    # Build result: top N photos per year, sorted by score desc
    result = []
    for year in sorted(by_year.keys(), reverse=True):
        photos = by_year[year]  # already sorted by score desc from query
        total = len(photos)
        top_photos = photos[:max_per_year]
        result.append(
            {
                "year": year,
                "years_ago": today.year - year,
                "count": total,
                "hero_hash": top_photos[0]["hash"] if top_photos else None,
                "photos": top_photos,
            }
        )

    return result
