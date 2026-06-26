"""Calendar view blueprint."""

from __future__ import annotations

import os
import re

from flask import Blueprint, Response, jsonify, request

from bpp.db.calendar import (
    get_daily_counts,
    get_on_this_day,
    get_year_daily_counts,
    get_year_months,
)
from bpp.db.photos import get_photos_by_date_range
from bpp.errors import ValidationError
from bpp.utils.logging import get_logger
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("calendar", __name__)


@bp.get("/api/v1/calendar/months")
def api_calendar_months() -> tuple[Response, int]:
    """Return all year/month combos that have photos."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    months = get_year_months(conn)
    return jsonify({"months": months}), 200


@bp.get("/api/v1/calendar/days")
def api_calendar_days() -> tuple[Response, int]:
    """Return daily photo counts for a given month."""
    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    if year is None or month is None:
        raise ValidationError("year and month are required", year=year, month=month)
    if not (1 <= month <= 12) or not (1900 <= year <= 2100):
        raise ValidationError("Invalid year or month", year=year, month=month)
    ctx = get_ctx()
    conn = ctx.get_conn()
    days = get_daily_counts(conn, year, month)
    return jsonify({"days": days, "year": year, "month": month}), 200


@bp.get("/api/v1/calendar/year")
def api_calendar_year() -> tuple[Response, int]:
    """Return daily photo counts for all months in a year (year overview)."""
    year = request.args.get("year", type=int)
    if year is None:
        raise ValidationError("year is required", field="year")
    if not (1900 <= year <= 2100):
        raise ValidationError("Invalid year", year=year)
    ctx = get_ctx()
    conn = ctx.get_conn()
    months = get_year_daily_counts(conn, year)
    # Convert int keys to str for JSON
    return jsonify({"year": year, "months": {str(k): v for k, v in months.items()}}), 200


@bp.get("/api/v1/calendar/photos")
def api_calendar_photos() -> tuple[Response, int]:
    """Return photos for a specific date or date range."""
    date_val = request.args.get("date")
    start = request.args.get("start")
    end = request.args.get("end")

    if not date_val and not (start and end):
        raise ValidationError("date or start+end are required")

    _DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for param, val in (("date", date_val), ("start", start), ("end", end)):
        if val is not None and not _DATE_RE.match(val):
            raise ValidationError(
                f"Invalid {param} format — expected YYYY-MM-DD",
                field=param,
                value=val,
            )

    # Photos use ISO 8601 timestamps (YYYY-MM-DDTHH:MM:SS).  The T separator
    # is intentional — space-delimited bounds silently miss T-delimited dates
    # because ord('T')=84 > ord(' ')=32 in SQLite string comparison.
    if date_val:
        start_dt = date_val + "T00:00:00"
        end_dt = date_val + "T23:59:59"
    else:
        start_dt = start + "T00:00:00"  # type: ignore[operator]
        end_dt = end + "T23:59:59"  # type: ignore[operator]

    ctx = get_ctx()
    conn = ctx.get_conn()
    photos = get_photos_by_date_range(conn, start_dt, end_dt)
    result = [
        {
            "id": p["id"],
            "filepath": p["filepath"],
            "hash": ctx.thumbs.get_hash(p["filepath"]) if ctx.thumbs else "",
            "date": p.get("date"),
            "score": p.get("aggregate_score", 0),
            "filename": os.path.basename(p["filepath"]) if p["filepath"] else "",
        }
        for p in photos
    ]
    return jsonify({"photos": result, "date": date_val, "start": start, "end": end}), 200


@bp.get("/api/v1/on-this-day")
def api_on_this_day() -> tuple[Response, int]:
    """Return photos from this date in past years, grouped by year."""
    month = request.args.get("month", type=int)
    day = request.args.get("day", type=int)
    if month is not None and not (1 <= month <= 12):
        raise ValidationError("Invalid month", month=month)
    if day is not None and not (1 <= day <= 31):
        raise ValidationError("Invalid day", day=day)
    ctx = get_ctx()
    conn = ctx.get_conn()
    years = get_on_this_day(conn, month=month, day=day)
    from datetime import date

    today = date.today()

    # Enrich photos with thumb_hash from thumbnail system
    if ctx.thumbs:
        for yr in years:
            for p in yr.get("photos", []):
                fp = p.get("filepath", "")
                if fp:
                    p["hash"] = ctx.thumbs.get_hash(fp)
            # Update hero_hash from first photo. Use .get() because the
            # hash is only attached above when filepath is truthy — a
            # row with empty filepath would have no "hash" key and a
            # bare [0]["hash"] raises KeyError.
            photos = yr.get("photos", [])
            yr["hero_hash"] = photos[0].get("hash") if photos else None

    return jsonify(
        {
            "years": years,
            "month": month or today.month,
            "day": day or today.day,
        }
    ), 200
