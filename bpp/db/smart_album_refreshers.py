"""Refresh routines for the built-in smart album types.

Extracted from :mod:`bpp.db.smart_albums` as part of the 500-LOC
cap enforcement. The smart_albums module had grown to 845 lines —
mostly because each built-in album type owns a ``_refresh_*_album``
function that walks the photos table looking for the rows that
belong in its bucket, then upserts the corresponding album row +
rebuilds its album_photos rows.

The functions don't share any state with the registry / orchestrator
layer beyond the two helpers ``_remove_smart_album_if_exists`` and
``_ensure_smart_album`` (also extracted here) and a small set of
``ALBUM_*`` string constants imported back from smart_albums.

smart_albums re-exports every name defined here so existing
imports (production callers, ~15 tests, the registry's built-in
registration tuple) keep working via the original module path.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from bpp.constants import ACTIVE_PHOTO_SQL, active_photo_sql
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_ACTIVE = ACTIVE_PHOTO_SQL
# Aliased active filters for self-join queries (e.g., duplicates)
_P1_ACTIVE = active_photo_sql("p1")
_P2_ACTIVE = active_photo_sql("p2")

# Screenshot filename patterns (used in refresh + get)
_SCREENSHOT_WHERE = (
    "original_filename LIKE 'Screenshot%' OR "
    "original_filename LIKE 'Screen Shot%' OR "
    "original_filename LIKE 'screen_%' OR "
    "original_filename LIKE 'Capture%' OR "
    "original_filename LIKE 'IMG_%_screenshot%' OR "
    "original_filename LIKE 'Simulator Screen Shot%'"
)


def _refresh_time_albums(conn: sqlite3.Connection) -> None:
    """Create smart albums by year and a 'Last 30 Days' album."""
    # Get distinct years from photos
    rows = conn.execute(
        "SELECT DISTINCT substr(date, 1, 4) AS year FROM photos "
        f"WHERE date IS NOT NULL AND {_ACTIVE} ORDER BY year"
    ).fetchall()

    for row in rows:
        year = row[0]
        if not year or len(year) != 4:
            continue
        _ensure_smart_album(
            conn,
            name=year,
            album_type="smart_time",
            rule={"year": year},
            photo_query=(
                f"SELECT id FROM photos WHERE substr(date,1,4)=? AND {_ACTIVE}",
                (year,),
            ),
        )

    # Last 30 Days
    _ensure_smart_album(
        conn,
        name="Last 30 Days",
        album_type="smart_time",
        rule={"days": 30},
        photo_query=(
            f"SELECT id FROM photos WHERE date >= datetime('now', '-30 days') AND {_ACTIVE}",
            (),
        ),
    )


def _refresh_score_album(conn: sqlite3.Connection) -> None:
    """Create a 'Top Rated' smart album with the highest-scoring photos.

    Applies visual similarity filtering so near-duplicate scenes don't both
    appear. Uses CLIP cosine similarity when available, hash distance as
    fallback.
    """
    from bpp.config import DEFAULTS

    threshold = DEFAULTS["selection_similarity_threshold"]

    # Fetch top 10% candidates with hash data for similarity filtering
    row = conn.execute(f"SELECT COUNT(*) FROM photos WHERE {_ACTIVE}").fetchone()
    total = row[0] if row else 0
    limit = max(1, total // 10)
    # Fetch 3x candidates so we have room to filter and still fill the album
    fetch_limit = limit * 3

    rows = conn.execute(
        "SELECT id, phash, ahash FROM photos "
        f"WHERE aggregate_score IS NOT NULL AND {_ACTIVE} "
        "ORDER BY aggregate_score DESC "
        f"LIMIT {fetch_limit}",
    ).fetchall()

    if not rows:
        _ensure_smart_album(
            conn,
            name="Top Rated",
            album_type="smart_score",
            rule={"top_percent": 10},
            photo_ids=[],
        )
        return

    # Try loading CLIP embeddings for better similarity checking
    clip_embs: dict[int, Any] = {}
    try:
        from bpp.db.clip import get_all_clip_embeddings

        clip_embs = get_all_clip_embeddings(conn)
    except Exception:
        log.warning("CLIP embeddings unavailable for Top Rated album", exc_info=True)

    # Greedy similarity filter
    import numpy as np

    from bpp.dedupe.phash import dual_hash_distance, hamming_distance

    accepted_ids: list[int] = []
    accepted_clip: list[np.ndarray] = []
    accepted_hashes: list[tuple[int | None, int | None]] = []

    for photo_id, phash, ahash in rows:
        if len(accepted_ids) >= limit:
            break

        # CLIP similarity check
        emb = clip_embs.get(photo_id)
        if emb is not None and accepted_clip and threshold > 0:
            too_similar = False
            for acc_emb in accepted_clip:
                if float(np.dot(emb, acc_emb)) >= threshold:
                    too_similar = True
                    break
            if too_similar:
                continue
        elif phash is not None and accepted_hashes and threshold > 0:
            # Hash fallback
            too_similar = False
            for acc_ph, acc_ah in accepted_hashes:
                if acc_ph is None:
                    continue
                if acc_ah is not None and ahash is not None:
                    dist = dual_hash_distance(phash, ahash, acc_ph, acc_ah)
                else:
                    dist = hamming_distance(phash, acc_ph)
                if dist <= 16:
                    too_similar = True
                    break
            if too_similar:
                continue

        accepted_ids.append(photo_id)
        if emb is not None:
            accepted_clip.append(emb)
        accepted_hashes.append((phash, ahash))

    _ensure_smart_album(
        conn,
        name="Top Rated",
        album_type="smart_score",
        rule={"top_percent": 10},
        photo_ids=accepted_ids,
    )


def _refresh_unsorted_album(conn: sqlite3.Connection) -> None:
    """Create a 'Needs Review' smart album with photos not in any manual album."""
    _ensure_smart_album(
        conn,
        name="Needs Review",
        album_type="smart_unsorted",
        rule={"unsorted": True},
        photo_query=(
            "SELECT p.id FROM photos p "
            f"WHERE p.{_ACTIVE} "
            "AND p.id NOT IN ("
            "  SELECT ap.photo_id FROM album_photos ap "
            "  JOIN albums a ON a.id = ap.album_id "
            "  WHERE a.album_type = 'manual'"
            ")",
            (),
        ),
    )


def _refresh_recent_album(conn: sqlite3.Connection) -> None:
    """Create a 'Recently Added' smart album with photos imported in the last 7 days."""
    _ensure_smart_album(
        conn,
        name="Recently Added",
        album_type="smart_recent",
        rule={"days": 7},
        photo_query=(
            f"SELECT id FROM photos WHERE created_at >= datetime('now', '-7 days') AND {_ACTIVE}",
            (),
        ),
    )


def _refresh_hidden_album(conn: sqlite3.Connection) -> None:
    """Create or remove a 'Hidden' smart album based on whether hidden photos exist."""
    import json

    row = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE hidden_at IS NOT NULL AND deleted_at IS NULL"
    ).fetchone()
    count = row[0] if row else 0

    if count > 0:
        _ensure_smart_album(
            conn,
            name="Hidden",
            album_type="smart_hidden",
            rule={"hidden": True},
            photo_query=(
                "SELECT id FROM photos WHERE hidden_at IS NOT NULL AND deleted_at IS NULL",
                (),
            ),
        )
    else:
        # Remove the hidden album if it exists and no hidden photos remain
        rule_json = json.dumps({"hidden": True}, sort_keys=True)
        row = conn.execute(
            "SELECT id FROM albums WHERE album_type='smart_hidden' AND rule_json=?",
            (rule_json,),
        ).fetchone()
        if row:
            conn.execute("DELETE FROM album_photos WHERE album_id=?", (row[0],))
            conn.execute("DELETE FROM albums WHERE id=?", (row[0],))
            conn.commit()


# Person/pet/group refreshers live in their own dedicated modules; the
# smart_albums orchestrator imports them directly. They aren't used by
# any refresh routine in THIS module — the historical home of these
# imports was inside the moved block, but nothing here calls them, so
# they don't need to be re-imported. (smart_albums re-exports them so
# external callers keep working.)


def _refresh_video_album(conn: sqlite3.Connection) -> None:
    """Create or remove a 'Videos' smart album."""

    row = conn.execute(f"SELECT COUNT(*) FROM photos WHERE is_video=1 AND {_ACTIVE}").fetchone()
    count = row[0] if row else 0

    if count > 0:
        _ensure_smart_album(
            conn,
            name="Videos",
            album_type="smart_video",
            rule={"video": True},
            photo_query=(
                f"SELECT id FROM photos WHERE is_video=1 AND {_ACTIVE}",
                (),
            ),
        )
    else:
        _remove_smart_album_if_exists(conn, "smart_video", {"video": True})


def _refresh_screenshot_album(conn: sqlite3.Connection) -> None:
    """Create or remove a 'Screenshots' smart album based on filename patterns."""

    pattern_sql = f"SELECT id FROM photos WHERE {_ACTIVE} AND ({_SCREENSHOT_WHERE})"
    row = conn.execute(f"SELECT COUNT(*) FROM ({pattern_sql})").fetchone()
    count = row[0] if row else 0

    if count > 0:
        _ensure_smart_album(
            conn,
            name="Screenshots",
            album_type="smart_screenshot",
            rule={"screenshot": True},
            photo_query=(pattern_sql, ()),
        )
    else:
        _remove_smart_album_if_exists(conn, "smart_screenshot", {"screenshot": True})


def _refresh_moments_album(conn: sqlite3.Connection) -> None:
    """Create or remove a 'Moments' smart album for visually-similar bursts.

    Uses moment_size > 1 (populated by assign_moment_clusters). No phash
    fallback — Moments require CLIP embeddings, so the album just stays absent
    until clustering has run.
    """
    rows = conn.execute(
        f"SELECT id FROM photos WHERE moment_size > 1 AND {_ACTIVE} "
        f"ORDER BY moment_cluster_id, date, id"
    ).fetchall()
    photo_ids = [r[0] for r in rows]

    if photo_ids:
        _ensure_smart_album(
            conn,
            name="Moments",
            album_type="smart_moments",
            rule={"moments": True},
            photo_ids=photo_ids,
        )
    else:
        _remove_smart_album_if_exists(conn, "smart_moments", {"moments": True})


def _refresh_duplicates_album(conn: sqlite3.Connection) -> None:
    """Create or remove a 'Duplicates' smart album for near-duplicate photos.

    Uses cluster_size > 1 (populated by assign_near_duplicate_clusters) when
    available; falls back to exact phash equality for libraries that haven't
    been clustered yet.
    """
    any_clustered = conn.execute(
        f"SELECT 1 FROM photos WHERE cluster_size > 1 AND {_ACTIVE} LIMIT 1"
    ).fetchone()
    if any_clustered:
        rows = conn.execute(
            f"SELECT id FROM photos WHERE cluster_size > 1 AND {_ACTIVE}"
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT DISTINCT p1.id FROM photos p1 "
            f"INNER JOIN photos p2 ON p1.id != p2.id "
            f"AND p1.phash IS NOT NULL AND p2.phash IS NOT NULL "
            f"AND p1.phash = p2.phash "
            f"WHERE {_P1_ACTIVE} AND {_P2_ACTIVE}"
        ).fetchall()
    photo_ids = [r[0] for r in rows]

    if photo_ids:
        _ensure_smart_album(
            conn,
            name="Duplicates",
            album_type="smart_duplicates",
            rule={"duplicates": True},
            photo_ids=photo_ids,
        )
    else:
        _remove_smart_album_if_exists(conn, "smart_duplicates", {"duplicates": True})


def _refresh_no_faces_album(conn: sqlite3.Connection) -> None:
    """Create or remove a 'No Faces' smart album for photos with no detected faces."""
    sql = f"SELECT id FROM photos WHERE face_count = 0 AND {_ACTIVE}"
    rows = conn.execute(sql).fetchall()
    photo_ids = [r[0] for r in rows]

    if photo_ids:
        _ensure_smart_album(
            conn,
            name="No Faces Detected",
            album_type="smart_no_faces",
            rule={"no_faces": True},
            photo_ids=photo_ids,
        )
    else:
        _remove_smart_album_if_exists(conn, "smart_no_faces", {"no_faces": True})


# CLIP-based document refresher lives in bpp.db.smart_album_document.
# Re-exported so the SmartAlbumRegistry built-in registration block
# keeps importing _refresh_document_album from smart_album_refreshers.
from bpp.db.smart_album_document import _refresh_document_album  # noqa: E402, F401


def _refresh_recently_edited_album(conn: sqlite3.Connection) -> None:
    """Create or remove a 'Recently Edited' smart album for photos with edits."""
    row = conn.execute(
        f"SELECT COUNT(*) FROM photo_edits pe JOIN photos p ON p.id = pe.photo_id WHERE p.{_ACTIVE}"
    ).fetchone()
    count = row[0] if row else 0

    if count > 0:
        _ensure_smart_album(
            conn,
            name="Recently Edited",
            album_type="smart_edited",
            rule={"edited": True},
            photo_query=(
                f"SELECT pe.photo_id FROM photo_edits pe "
                f"JOIN photos p ON p.id = pe.photo_id "
                f"WHERE p.{_ACTIVE} ORDER BY pe.modified_at DESC",
                (),
            ),
        )
    else:
        _remove_smart_album_if_exists(conn, "smart_edited", {"edited": True})


def _get_enhanced_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute(
        f"SELECT pe.photo_id FROM photo_edits pe "
        f"JOIN photos p ON p.id = pe.photo_id "
        f"WHERE pe.auto_enhanced = 1 AND p.{_ACTIVE}"
    ).fetchall()
    return {r[0] for r in rows}


def _refresh_enhanced_album(conn: sqlite3.Connection) -> None:
    """Create or remove the 'Enhanced' smart album (auto-enhanced photos only)."""
    count = conn.execute(
        f"SELECT COUNT(*) FROM photo_edits pe JOIN photos p ON p.id = pe.photo_id "
        f"WHERE pe.auto_enhanced = 1 AND p.{_ACTIVE}"
    ).fetchone()[0]

    if count > 0:
        _ensure_smart_album(
            conn,
            name="Enhanced",
            album_type="smart_enhanced",
            rule={"enhanced": True},
            photo_query=(
                f"SELECT pe.photo_id FROM photo_edits pe "
                f"JOIN photos p ON p.id = pe.photo_id "
                f"WHERE pe.auto_enhanced = 1 AND p.{_ACTIVE} ORDER BY pe.modified_at DESC",
                (),
            ),
        )
        log.debug("Refreshed Enhanced smart album (%d photo(s))", count)
    else:
        _remove_smart_album_if_exists(conn, "smart_enhanced", {"enhanced": True})
        log.debug("Removed Enhanced smart album (no auto-enhanced photos)")


# The create/update/remove primitives moved to bpp.db.smart_album_ensure
# when the LOC gate caught this file over the 500-line cap (2026-06-12).
# Re-exported so the historical import path keeps working.
from bpp.db.smart_album_ensure import (  # noqa: E402
    _ensure_smart_album,
    _remove_smart_album_if_exists,
)
