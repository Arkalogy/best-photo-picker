"""Library statistics: counts, sizes, format breakdown."""

from __future__ import annotations

import sqlite3


def get_library_stats(conn: sqlite3.Connection) -> dict:
    """Return library statistics excluding soft-deleted photos.

    Returns dict with: total_count, total_size, avg_file_size,
    photo_count, video_count, raw_count, format_breakdown.

    All aggregates computed in SQL — no O(N) Python loop over all rows.
    """
    agg = conn.execute(
        "SELECT COUNT(*) AS total_count, "
        "COALESCE(SUM(file_size), 0) AS total_size, "
        "COALESCE(SUM(CASE WHEN is_video=1 THEN 1 ELSE 0 END), 0) AS video_count, "
        "COALESCE(SUM(CASE WHEN is_raw=1 THEN 1 ELSE 0 END), 0) AS raw_count, "
        "COALESCE(SUM(CASE WHEN is_video=0 AND is_raw=0 THEN 1 ELSE 0 END), 0) AS photo_count "
        "FROM photos WHERE deleted_at IS NULL AND is_live_photo_sidecar = 0"
    ).fetchone()

    total_count = agg[0]
    total_size = agg[1]
    video_count = agg[2]
    raw_count = agg[3]
    photo_count = agg[4]
    avg_file_size = total_size // total_count if total_count > 0 else 0

    # Format breakdown — extract the last file extension entirely in SQL
    # using SUBSTR with negative (from-end) indices so Python never iterates
    # over all rows. Extensions are 1-4 chars; we probe positions -5 to -2.
    ext_rows = conn.execute(
        "SELECT ext, COUNT(*) as cnt "
        "FROM ("
        "  SELECT CASE"
        "    WHEN SUBSTR(filepath,-5,1)='.' THEN LOWER(SUBSTR(filepath,-5))"
        "    WHEN SUBSTR(filepath,-4,1)='.' THEN LOWER(SUBSTR(filepath,-4))"
        "    WHEN SUBSTR(filepath,-3,1)='.' THEN LOWER(SUBSTR(filepath,-3))"
        "    WHEN SUBSTR(filepath,-2,1)='.' THEN LOWER(SUBSTR(filepath,-2))"
        "    ELSE '' END AS ext"
        "  FROM photos WHERE deleted_at IS NULL AND is_live_photo_sidecar = 0"
        ") WHERE ext != '' GROUP BY ext"
    ).fetchall()
    ext_counts: dict[str, int] = {row[0]: row[1] for row in ext_rows}

    return {
        "total_count": total_count,
        "total_size": total_size,
        "avg_file_size": avg_file_size,
        "photo_count": photo_count,
        "video_count": video_count,
        "raw_count": raw_count,
        "format_breakdown": ext_counts,
    }
