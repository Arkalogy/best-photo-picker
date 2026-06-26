"""Per-type photo-id resolvers for smart albums.

Each function takes ``(conn, rule)`` and returns the photo_ids that
currently match the rule, given the live DB state. The
SmartAlbumRegistry pairs each album type with its resolver here.

Adding a new album type:
  1. Write the resolver here (or in an external module + register
     via ``SmartAlbumRegistry.register``).
  2. If the album also has a refresh step, write the refresher in
     smart_albums.py and register the pair.

These functions are deliberately read-only — they answer "what
photos belong in this album right now?" without mutating any
album_photos rows. Mutation happens in the ``_refresh_*`` family.
"""

from __future__ import annotations

import sqlite3

from bpp.constants import ACTIVE_PHOTO_SQL, active_photo_sql
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_ACTIVE = ACTIVE_PHOTO_SQL

# Aliased active filters for self-join queries (e.g., duplicates)
_P1_ACTIVE = active_photo_sql("p1")
_P2_ACTIVE = active_photo_sql("p2")

# Screenshot filename patterns shared with the refresh path.
_SCREENSHOT_WHERE = (
    "original_filename LIKE 'Screenshot%' OR "
    "original_filename LIKE 'Screen Shot%' OR "
    "original_filename LIKE 'screen_%' OR "
    "original_filename LIKE 'Capture%' OR "
    "original_filename LIKE 'IMG_%_screenshot%' OR "
    "original_filename LIKE 'Simulator Screen Shot%'"
)

# Document detection constants — same values used by the document
# refresher in smart_albums.py.
DOCUMENT_PROMPT = (
    "photo of a document, receipt, paper, text page, whiteboard, handwritten note, scan"
)
DOCUMENT_CLIP_THRESHOLD = 0.26


def _get_time_ids(conn: sqlite3.Connection, rule: dict) -> list[int]:
    if "year" in rule:
        try:
            year_str = str(int(rule["year"]))  # ensure integer-valued year
        except (TypeError, ValueError):
            return []
        rows = conn.execute(
            f"SELECT id FROM photos WHERE substr(date,1,4)=? AND {_ACTIVE}",
            (year_str,),
        ).fetchall()
    elif "days" in rule:
        try:
            days_int = max(1, int(rule["days"]))
        except (TypeError, ValueError):
            return []
        rows = conn.execute(
            f"SELECT id FROM photos WHERE date >= datetime('now', ?) AND {_ACTIVE}",
            (f"-{days_int} days",),
        ).fetchall()
    else:
        return []
    return [r[0] for r in rows]


def _get_score_ids(conn: sqlite3.Connection, rule: dict) -> list[int]:
    try:
        pct = max(1, min(100, int(rule.get("top_percent", 10))))
    except (TypeError, ValueError):
        pct = 10
    rows = conn.execute(
        "SELECT id FROM photos "
        f"WHERE aggregate_score IS NOT NULL AND {_ACTIVE} "
        "ORDER BY aggregate_score DESC "
        f"LIMIT MAX(1, (SELECT COUNT(*) FROM photos WHERE {_ACTIVE}) * {pct} / 100)"
    ).fetchall()
    return [r[0] for r in rows]


def _get_all_ids(conn: sqlite3.Connection, _rule: dict) -> list[int]:
    rows = conn.execute(f"SELECT id FROM photos WHERE {_ACTIVE}").fetchall()
    return [r[0] for r in rows]


def _get_unsorted_ids(conn: sqlite3.Connection, _rule: dict) -> list[int]:
    rows = conn.execute(
        "SELECT p.id FROM photos p "
        f"WHERE p.{_ACTIVE} "
        "AND p.id NOT IN ("
        "  SELECT ap.photo_id FROM album_photos ap "
        "  JOIN albums a ON a.id = ap.album_id "
        "  WHERE a.album_type = 'manual'"
        ")"
    ).fetchall()
    return [r[0] for r in rows]


def _get_pet_ids(conn: sqlite3.Connection, rule: dict) -> list[int]:
    cid = rule.get("cluster_id")
    if cid is not None:
        rows = conn.execute(
            "SELECT DISTINCT p.id FROM photos p "
            "JOIN pet_detections pd ON pd.photo_id = p.id "
            f"WHERE pd.cluster_id = ? AND p.{_ACTIVE}",
            (cid,),
        ).fetchall()
        return [r[0] for r in rows]
    # Legacy fallback for class-only rules
    col = {
        "cat": "has_cat",
        "cats": "has_cat",
        "dog": "has_dog",
        "dogs": "has_dog",
    }.get(rule.get("pet_class", ""))
    if col:
        rows = conn.execute(f"SELECT id FROM photos WHERE {col}=1 AND {_ACTIVE}").fetchall()
        return [r[0] for r in rows]
    return []


def _get_group_ids(conn: sqlite3.Connection, rule: dict) -> list[int]:
    members = rule.get("group_members", [])
    if members:
        from bpp.db.groups import get_group_photo_ids

        return get_group_photo_ids(conn, members)
    return []


def _get_recent_ids(conn: sqlite3.Connection, rule: dict) -> list[int]:
    try:
        days = max(1, int(rule.get("days", 7)))
    except (TypeError, ValueError):
        days = 7
    rows = conn.execute(
        f"SELECT id FROM photos WHERE created_at >= datetime('now', ?) AND {_ACTIVE}",
        (f"-{days} days",),
    ).fetchall()
    return [r[0] for r in rows]


def _get_hidden_ids(conn: sqlite3.Connection, _rule: dict) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM photos WHERE hidden_at IS NOT NULL AND deleted_at IS NULL"
    ).fetchall()
    return [r[0] for r in rows]


def _get_person_ids(conn: sqlite3.Connection, rule: dict) -> list[int]:
    cluster_id = rule.get("cluster_id")
    if cluster_id is None:
        return []
    ids: set[int] = set()
    face_rows = conn.execute(
        "SELECT DISTINCT fe.photo_id FROM face_embeddings fe "
        "JOIN photos p ON p.id = fe.photo_id "
        f"WHERE fe.cluster_id=? AND p.{_ACTIVE}",
        (cluster_id,),
    ).fetchall()
    ids.update(r[0] for r in face_rows)
    try:
        tag_rows = conn.execute(
            "SELECT pt.photo_id FROM photo_person_tags pt "
            "JOIN photos p ON p.id = pt.photo_id "
            f"WHERE pt.cluster_id=? AND p.{_ACTIVE}",
            (cluster_id,),
        ).fetchall()
        ids.update(r[0] for r in tag_rows)
    except sqlite3.OperationalError as e:
        log.debug("photo_person_tags table not ready, skipping: %s", e)
    return list(ids)


def _get_deleted_ids(conn: sqlite3.Connection, _rule: dict) -> list[int]:
    rows = conn.execute("SELECT id FROM photos WHERE deleted_at IS NOT NULL").fetchall()
    return [r[0] for r in rows]


def _get_video_ids(conn: sqlite3.Connection, _rule: dict) -> list[int]:
    rows = conn.execute(f"SELECT id FROM photos WHERE is_video=1 AND {_ACTIVE}").fetchall()
    return [r[0] for r in rows]


def _get_screenshot_ids(conn: sqlite3.Connection, _rule: dict) -> list[int]:
    rows = conn.execute(
        f"SELECT id FROM photos WHERE {_ACTIVE} AND ({_SCREENSHOT_WHERE})"
    ).fetchall()
    return [r[0] for r in rows]


def _get_moments_ids(conn: sqlite3.Connection, _rule: dict) -> list[int]:
    """Return IDs of photos in a Moment (moment_size > 1).

    assign_moment_clusters() (bpp/db/moments.py) populates moment_size from the
    CLIP+time clustering. Unlike the Duplicates fallback there's no phash
    fallback — a Moment requires CLIP embeddings, so until clustering runs the
    album is simply empty.
    """
    rows = conn.execute(
        f"SELECT id FROM photos WHERE moment_size > 1 AND {_ACTIVE} "
        f"ORDER BY moment_cluster_id, date, id"
    ).fetchall()
    return [r[0] for r in rows]


def _get_duplicates_ids(conn: sqlite3.Connection, _rule: dict) -> list[int]:
    """Return IDs of photos in near-duplicate clusters (cluster_size > 1).

    assign_near_duplicate_clusters() (bpp/db/dedupe.py) must have run first;
    if cluster_size is still 1 for all photos (never clustered), falls back to
    exact phash equality so the album is never silently empty.
    """
    # Fast path: cluster_size populated → use it directly (O(1) index scan)
    any_clustered = conn.execute(
        f"SELECT 1 FROM photos WHERE cluster_size > 1 AND {_ACTIVE} LIMIT 1"
    ).fetchone()
    if any_clustered:
        rows = conn.execute(
            f"SELECT id FROM photos WHERE cluster_size > 1 AND {_ACTIVE}"
        ).fetchall()
        return [r[0] for r in rows]

    # Fallback: exact phash equality (pre-clustering state)
    rows = conn.execute(
        f"SELECT DISTINCT p1.id FROM photos p1 "
        f"INNER JOIN photos p2 ON p1.id != p2.id "
        f"AND p1.phash IS NOT NULL AND p2.phash IS NOT NULL "
        f"AND p1.phash = p2.phash "
        f"WHERE {_P1_ACTIVE} AND {_P2_ACTIVE}"
    ).fetchall()
    return [r[0] for r in rows]


def _get_no_faces_ids(conn: sqlite3.Connection, _rule: dict) -> list[int]:
    rows = conn.execute(f"SELECT id FROM photos WHERE face_count = 0 AND {_ACTIVE}").fetchall()
    return [r[0] for r in rows]


def _get_document_ids(conn: sqlite3.Connection, _rule: dict) -> list[int]:
    import numpy as np

    try:
        from bpp.db.clip import get_all_clip_embeddings
        from bpp.scoring.clip_embed import compute_text_embedding
    except ImportError:
        return []
    embeddings = get_all_clip_embeddings(conn)
    if not embeddings:
        return []
    text_emb = compute_text_embedding(DOCUMENT_PROMPT)
    if text_emb is None:
        return []
    photo_ids_list = list(embeddings.keys())
    emb_matrix = np.stack([embeddings[pid] for pid in photo_ids_list])
    similarities = emb_matrix @ text_emb
    matching = [
        photo_ids_list[i]
        for i in range(len(photo_ids_list))
        if similarities[i] >= DOCUMENT_CLIP_THRESHOLD
    ]
    if not matching:
        return []
    placeholders = ", ".join(["?"] * len(matching))
    rows = conn.execute(
        f"SELECT id FROM photos WHERE id IN ({placeholders}) AND {_ACTIVE}",
        matching,
    ).fetchall()
    return [r[0] for r in rows]


def _get_edited_ids(conn: sqlite3.Connection, _rule: dict) -> list[int]:
    rows = conn.execute(
        f"SELECT pe.photo_id FROM photo_edits pe "
        f"JOIN photos p ON p.id = pe.photo_id "
        f"WHERE p.{_ACTIVE} ORDER BY pe.modified_at DESC"
    ).fetchall()
    return [r[0] for r in rows]


def _get_tag_ids(conn: sqlite3.Connection, rule: dict) -> list[int]:
    tid = rule.get("tag_id")
    if tid is None:
        return []
    rows = conn.execute(
        "SELECT DISTINCT p.id FROM photos p "
        "JOIN photo_tags pt ON pt.photo_id = p.id "
        f"WHERE pt.tag_id = ? AND p.{_ACTIVE}",
        (tid,),
    ).fetchall()
    return [r[0] for r in rows]
