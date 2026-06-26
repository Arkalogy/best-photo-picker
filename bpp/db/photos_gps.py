"""GPS validation + GPS-keyed photo queries.

Extracted from :mod:`bpp.db.photos` as part of the 500-LOC cap split.
GPS coords are stored in dedicated ``gps_lat REAL`` / ``gps_lon REAL``
columns (schema v30); the partial index ``idx_photos_gps`` covers the
WHERE clause used by the map view and album-stats queries. Older
callers that only know about the ``exif_json`` blob get lifted into
the dedicated columns via ``_maybe_lift_gps_from_exif``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from bpp.constants import active_photo_sql


def _valid_gps_pair(lat: Any, lon: Any) -> bool:
    """validate a GPS coordinate pair before writing it
    to the indexed gps_lat/gps_lon columns.

    SQLite's REAL column accepts any numeric input verbatim — a
    photo with corrupt EXIF reporting `gps_lat: 999.0` would land
    in the indexed column and show up on the map view at a
    nonsense location. Reject:

      * non-numeric types (string GPS that the EXIF parser failed
        on, or callers that passed a sentinel like "unknown")
      * NaN / +/-Inf — SQLite stores these but every consumer that
        does float math on them produces garbage
      * lat outside [-90, 90] or lon outside [-180, 180] — there's
        no legitimate physical coordinate outside this range, so
        anything that lands here is corrupt or attacker-supplied

    Both sides must validate together; partial coords (one valid,
    one bad) are still corrupt for the indexed-pair invariant.
    """
    import math

    for v in (lat, lon):
        if v is None:
            return False
        if isinstance(v, bool):  # bool is a subclass of int; reject early
            return False
        if not isinstance(v, (int, float)):
            return False
        if math.isnan(v) or math.isinf(v):
            return False
    return -90.0 <= float(lat) <= 90.0 and -180.0 <= float(lon) <= 180.0


def _maybe_lift_gps_from_exif(values: dict[str, Any]) -> None:
    """if the writer passed `exif_json` but not the dedicated
    gps_lat / gps_lon columns, parse the blob and lift the coords up.

    Lets older call sites (CLI, ad-hoc tools, tests) that only know
    about the JSON blob participate in the indexed columns without
    forcing every writer to read the JSON twice. Mutates `values`
    in place — does nothing when columns are already set.

    validates the pair via ``_valid_gps_pair`` before
    writing. A corrupt EXIF with bogus / out-of-range coords is
    silently dropped rather than landing in the indexed column.
    """
    if values.get("gps_lat") is not None or values.get("gps_lon") is not None:
        return
    blob = values.get("exif_json")
    if not blob:
        return
    from bpp.utils.json_utils import safe_json_loads

    data = safe_json_loads(blob, default=None, context="exif_json gps lift")
    if not isinstance(data, dict):
        return
    lat = data.get("gps_lat")
    lon = data.get("gps_lon")
    if _valid_gps_pair(lat, lon):
        values["gps_lat"] = lat
        values["gps_lon"] = lon


def get_photos_with_gps(
    conn: sqlite3.Connection,
    album_id: int | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return photos that have GPS coordinates.

    reads the dedicated gps_lat / gps_lon columns instead of
    json_extract'ing them from exif_json. The partial index
    `idx_photos_gps` covers the WHERE clause, so the planner does
    an index range scan instead of a full table scan + per-row JSON
    parse. On a 50k-photo library that's tens of milliseconds
    instead of multiple seconds.

    Excludes deleted and missing photos. Returns dicts with id,
    filepath, gps_lat, gps_lon, date, thumb fields.

    ``limit`` and ``offset`` push pagination to the DB layer so
    callers don't have to materialise all rows to slice a page.
    """
    base_select = (
        "SELECT p.id, p.filepath, p.date, p.date_month, p.aggregate_score, "
        "p.gps_lat, p.gps_lon "
        "FROM photos p "
    )
    conditions = f"{active_photo_sql('p')} AND p.gps_lat IS NOT NULL AND p.gps_lon IS NOT NULL"
    pagination = ""
    if limit is not None:
        pagination = f" LIMIT {int(limit)} OFFSET {int(offset)}"
    if album_id is not None:
        rows = conn.execute(
            base_select
            + "JOIN album_photos ap ON ap.photo_id = p.id "
            + "WHERE ap.album_id = ? AND "
            + conditions
            + pagination,
            (album_id,),
        ).fetchall()
    else:
        rows = conn.execute(base_select + "WHERE " + conditions + pagination).fetchall()
    return [dict(r) for r in rows]


def count_photos_with_gps(conn: sqlite3.Connection, album_id: int | None = None) -> int:
    """Return the total count of photos with GPS coordinates."""
    conditions = f"{active_photo_sql('p')} AND p.gps_lat IS NOT NULL AND p.gps_lon IS NOT NULL"
    if album_id is not None:
        row = conn.execute(
            "SELECT COUNT(*) FROM photos p "
            "JOIN album_photos ap ON ap.photo_id = p.id "
            "WHERE ap.album_id = ? AND " + conditions,
            (album_id,),
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM photos p WHERE " + conditions).fetchone()
    return row[0] if row else 0
