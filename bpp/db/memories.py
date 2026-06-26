"""Auto-generate memories (event-based photo stories)."""

from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from bpp.constants import ACTIVE_PHOTO_SQL
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_ACTIVE = ACTIVE_PHOTO_SQL
_MIN_PHOTOS = 3  # Minimum photos to form a memory


def generate_memories(
    conn: sqlite3.Connection,
    gap_hours: float = 4,
    min_photos: int = _MIN_PHOTOS,
) -> list[dict[str, Any]]:
    """Generate memories from photo events and persist to DB.

    1. Fetch all active photos with dates, sorted chronologically.
    2. Cluster by time gaps (> gap_hours = new event).
    3. Filter out events with < min_photos.
    4. Score and rank events.
    5. Store in memories + memory_photos tables.
    """
    # Fetch active photos with dates
    rows = conn.execute(
        "SELECT id, filepath, date, date_day, aggregate_score, face_count "
        f"FROM photos WHERE date IS NOT NULL AND {_ACTIVE} "
        "ORDER BY date"
    ).fetchall()

    photos = [
        {
            "id": row["id"],
            "filepath": row["filepath"],
            "date": row["date"],
            "date_day": row["date_day"],
            "aggregate_score": row["aggregate_score"] or 0.0,
            "face_count": row["face_count"] or 0,
        }
        for row in rows
    ]

    # Cluster into events
    clusters = _cluster_events(photos, gap_hours=gap_hours)

    # Filter small clusters
    clusters = [c for c in clusters if len(c) >= min_photos]

    if not clusters:
        # Clear old memories
        conn.execute("DELETE FROM memory_photos")
        conn.execute("DELETE FROM memories")
        conn.commit()
        return []

    # Build memory entries
    memories = []
    for cluster in clusters:
        if not cluster:
            continue
        title = _title_for_cluster(cluster)
        date_start = cluster[0]["date"]
        date_end = cluster[-1]["date"]
        # Hero = highest scored photo
        hero = max(cluster, key=lambda p: p["aggregate_score"])
        score = _score_memory(cluster)
        photo_ids = [p["id"] for p in cluster]

        memories.append(
            {
                "title": title,
                "date_start": date_start,
                "date_end": date_end,
                "hero_photo_id": hero["id"],
                "photo_count": len(cluster),
                "score": score,
                "photo_ids": photo_ids,
            }
        )

    # Sort by date descending (most recent first)
    memories.sort(key=lambda m: m["date_start"], reverse=True)

    # Persist: clear old, insert new
    conn.execute("DELETE FROM memory_photos")
    conn.execute("DELETE FROM memories")

    result: list[dict[str, Any]] = []
    if not memories:
        conn.commit()
        return result

    # Batch insert all memories in one round-trip. We need the new rowids
    # back to populate memory_photos — sqlite3 guarantees that rowids
    # from a single executemany on an INTEGER PRIMARY KEY column are
    # consecutive and end at sqlite_last_insert_rowid(). Python's
    # cursor.lastrowid is unset after executemany, so query it directly.
    conn.executemany(
        "INSERT INTO memories (title, date_start, date_end, hero_photo_id, "
        "photo_count, score) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (
                m["title"],
                m["date_start"],
                m["date_end"],
                m["hero_photo_id"],
                m["photo_count"],
                m["score"],
            )
            for m in memories
        ],
    )
    last_id = (conn.execute("SELECT last_insert_rowid()").fetchone() or (0,))[0]
    first_id = last_id - len(memories) + 1

    # Build a single flat list of (memory_id, photo_id) pairs so the
    # memory_photos insert is one round-trip too.
    photo_pairs: list[tuple[int, int]] = []
    for offset, m in enumerate(memories):
        mem_id = first_id + offset
        m["id"] = mem_id
        photo_pairs.extend((mem_id, pid) for pid in m["photo_ids"])
        result.append(m)
    if photo_pairs:
        conn.executemany(
            "INSERT OR IGNORE INTO memory_photos (memory_id, photo_id) VALUES (?, ?)",
            photo_pairs,
        )

    conn.commit()
    log.info("Generated %d memories from %d photos", len(result), len(photos))
    return result


def list_memories(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """List all memories with hero hash for thumbnails."""
    rows = conn.execute(
        "SELECT m.id, m.title, m.date_start, m.date_end, m.hero_photo_id, "
        "m.photo_count, m.score, m.generated_at, p.sha256 AS hero_hash "
        "FROM memories m "
        "LEFT JOIN photos p ON p.id = m.hero_photo_id "
        "ORDER BY m.date_start DESC"
    ).fetchall()
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "date_start": r["date_start"],
            "date_end": r["date_end"],
            "hero_photo_id": r["hero_photo_id"],
            "photo_count": r["photo_count"],
            "score": r["score"],
            "generated_at": r["generated_at"],
            "hero_hash": r["hero_hash"],
        }
        for r in rows
    ]


def get_memory(conn: sqlite3.Connection, memory_id: int) -> dict[str, Any] | None:
    """Get a single memory with its photo IDs."""
    row = conn.execute(
        "SELECT m.id, m.title, m.date_start, m.date_end, m.hero_photo_id, "
        "m.photo_count, m.score, m.generated_at, p.sha256 AS hero_hash "
        "FROM memories m "
        "LEFT JOIN photos p ON p.id = m.hero_photo_id "
        "WHERE m.id = ?",
        (memory_id,),
    ).fetchone()
    if row is None:
        return None

    photo_ids = [
        r["photo_id"]
        for r in conn.execute(
            "SELECT photo_id FROM memory_photos WHERE memory_id = ?",
            (memory_id,),
        ).fetchall()
    ]

    return {
        "id": row["id"],
        "title": row["title"],
        "date_start": row["date_start"],
        "date_end": row["date_end"],
        "hero_photo_id": row["hero_photo_id"],
        "photo_count": row["photo_count"],
        "score": row["score"],
        "generated_at": row["generated_at"],
        "hero_hash": row["hero_hash"],
        "photo_ids": photo_ids,
    }


def _cluster_events(
    photos: list[dict[str, Any]],
    gap_hours: float = 4,
) -> list[list[dict[str, Any]]]:
    """Cluster photos into events by time gaps.

    Photos must be sorted by date. Photos without dates are skipped.
    """
    if not photos:
        return []

    # Filter out photos without parseable dates
    dated = []
    for p in photos:
        if not p.get("date"):
            continue
        try:
            dt = datetime.fromisoformat(p["date"])
            dated.append((dt, p))
        except (ValueError, TypeError):
            continue

    if not dated:
        return []

    # Sort by date (should already be sorted, but ensure)
    dated.sort(key=lambda x: x[0])

    gap_td = timedelta(hours=gap_hours)
    clusters: list[list[dict[str, Any]]] = []
    current_cluster: list[dict[str, Any]] = [dated[0][1]]
    prev_dt = dated[0][0]

    for dt, photo in dated[1:]:
        if dt - prev_dt > gap_td:
            clusters.append(current_cluster)
            current_cluster = [photo]
        else:
            current_cluster.append(photo)
        prev_dt = dt

    if current_cluster:
        clusters.append(current_cluster)

    return clusters


def _title_for_cluster(photos: list[dict[str, Any]]) -> str:
    """Generate a human-readable title for a memory cluster."""
    if not photos:
        return "Memory"

    dates = []
    for p in photos:
        d = p.get("date")
        if d:
            try:
                dates.append(datetime.fromisoformat(d))
            except (ValueError, TypeError):
                continue

    if not dates:
        return "Memory"

    first = min(dates)
    last = max(dates)

    months = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]

    if first.date() == last.date():
        # Single day: "June 15, 2024"
        return f"{months[first.month - 1]} {first.day}, {first.year}"

    if first.month == last.month and first.year == last.year:
        # Same month: "June 15-17, 2024"
        return f"{months[first.month - 1]} {first.day}\u2013{last.day}, {first.year}"

    if first.year == last.year:
        # Different months, same year: "Jun 28 - Jul 2, 2024"
        short_months = [m[:3] for m in months]
        return (
            f"{short_months[first.month - 1]} {first.day} \u2013 "
            f"{short_months[last.month - 1]} {last.day}, {first.year}"
        )

    # Different years: "Dec 2023 - Jan 2024"
    short_months = [m[:3] for m in months]
    return (
        f"{short_months[first.month - 1]} {first.year} \u2013 "
        f"{short_months[last.month - 1]} {last.year}"
    )


def _score_memory(photos: list[dict[str, Any]]) -> float:
    """Score a memory for ranking. Higher = more interesting."""
    if not photos:
        return 0.0

    n = len(photos)
    avg_score = sum(p.get("aggregate_score", 0) or 0 for p in photos) / n
    face_ratio = sum(1 for p in photos if (p.get("face_count") or 0) > 0) / n

    # Base: photo count (log scale) * quality
    count_factor = math.log2(max(n, 1) + 1)
    return count_factor * avg_score * (1 + 0.3 * face_ratio)
