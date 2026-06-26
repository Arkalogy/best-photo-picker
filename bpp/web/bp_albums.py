"""Albums blueprint: album CRUD and sub-routes."""

from __future__ import annotations

import json
from typing import Any

from flask import Blueprint, Response, jsonify, request

from bpp.constants import ACTIVE_PHOTO_SQL, active_photo_sql
from bpp.db.albums import (
    add_photos_to_album,
    create_album,
    delete_album,
    get_album,
    get_album_photos,
    remove_photos_from_album,
    update_album,
)
from bpp.db.albums import (
    list_albums as db_list_albums,
)
from bpp.db.photos import (
    get_photo_ids_by_paths,
)
from bpp.errors import NotFoundError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.recompute import (
    RECOMPUTE_WEIGHT_KEYS,
)
from bpp.web.share import requires_local_app
from bpp.web.state import clamp_k, get_ctx

log = get_logger(__name__)

# Keys allowed through the PUT /api/v1/albums/:id config update.
# Scoring weights auto-populate from RECOMPUTE_WEIGHT_KEYS; UI state
# flags are listed explicitly. Unknown keys are silently dropped.
_ALLOWED_CONFIG_KEYS: frozenset[str] = frozenset(RECOMPUTE_WEIGHT_KEYS) | {"k_user_set"}
bp = Blueprint("albums", __name__)


def _attach_cluster_siblings_from_db(
    conn: Any,
    thumbs: Any,
    photos: list[dict[str, Any]],
    raw_data: list[dict[str, Any]],
) -> None:
    """Attach similar_photos siblings for Duplicates album photos.

    Uses dup_cluster_id (set by assign_near_duplicate_clusters) when
    available so burst shots with different-but-close phash values are
    grouped correctly.  Falls back to exact phash matching for libraries
    that haven't been clustered yet.
    """
    from collections import defaultdict

    from bpp.constants import ACTIVE_PHOTO_SQL

    # Check if clustering has run — prefer dup_cluster_id grouping
    use_clusters = (
        conn.execute("SELECT 1 FROM photos WHERE dup_cluster_id > 0 LIMIT 1").fetchone() is not None
    )

    if use_clusters:
        # Gather cluster IDs from this page (skip singletons, cluster_id=0)
        cluster_ids: set[int] = set()
        for raw in raw_data:
            cid = raw.get("dup_cluster_id")
            if cid and cid > 0:
                cluster_ids.add(cid)
        if not cluster_ids:
            return
        placeholders = ",".join("?" for _ in cluster_ids)
        rows = conn.execute(
            f"SELECT filepath, dup_cluster_id, aggregate_score, blur_score,"
            f" exposure_score, face_score, composition_score, date_day,"
            f" original_filename"
            f" FROM photos WHERE dup_cluster_id IN ({placeholders})"
            f" AND {ACTIVE_PHOTO_SQL}",
            list(cluster_ids),
        ).fetchall()
        groups: dict[int, list] = defaultdict(list)
        for row in rows:
            groups[row[1]].append(row)
        group_key = "dup_cluster_id"
    else:
        # Fallback: exact phash grouping
        phashes: set[int] = set()
        for raw in raw_data:
            ph = raw.get("phash")
            if ph is not None:
                phashes.add(ph)
        if not phashes:
            return
        placeholders = ",".join("?" for _ in phashes)
        rows = conn.execute(
            f"SELECT filepath, phash, aggregate_score, blur_score,"
            f" exposure_score, face_score, composition_score, date_day,"
            f" original_filename"
            f" FROM photos WHERE phash IN ({placeholders})"
            f" AND {ACTIVE_PHOTO_SQL}",
            list(phashes),
        ).fetchall()
        groups = defaultdict(list)
        for row in rows:
            groups[row[1]].append(row)
        group_key = "phash"

    for i, raw in enumerate(raw_data):
        key = raw.get(group_key)
        if not key or key not in groups:
            continue
        my_fp = raw.get("filepath", "")
        siblings = [
            {
                "filepath": row[0],
                "thumb_hash": thumbs.get_hash(row[0]) if thumbs else "",
                "similarity": None,  # hamming-distance cluster — not a scored match
                "aggregate_score": row[2] or 0,
                "blur_score": row[3] or 0,
                "exposure_score": row[4] or 0,
                "face_score": row[5] or 0,
                "composition_score": row[6] or 0,
                "date_day": row[7] or "",
                "filename": row[8] or "",
            }
            for row in groups[key]
            if row[0] != my_fp
        ]
        if siblings:
            photos[i]["similar_photos"] = siblings


@bp.get("/api/v1/albums")
def api_albums_list() -> tuple[Response, int]:
    """List all albums in the active library, manual + smart, in display
    order. Each entry includes id, name, album_type, photo_count, k,
    parent_id, and config."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    albums = db_list_albums(conn)
    return jsonify({"albums": albums}), 200


@bp.post("/api/v1/albums")
@requires_local_app
def api_albums_create() -> tuple[Response, int]:
    """Create a manual album from a JSON body containing ``name`` and
    optional ``config``, ``k``, and ``parent_id``. Returns the new album
    id with HTTP 201."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        raise ValidationError("Name is required", field="name")
    if len(name) > 255:
        raise ValidationError(
            "Name too long (max 255 characters)",
            field="name",
            max_length=255,
            actual_length=len(name),
        )
    conn = ctx.get_conn()
    config = data.get("config")
    k = clamp_k(data.get("k", 50))
    parent_id = data.get("parent_id")
    if parent_id is not None:
        parent_id = int(parent_id)
    album_id = create_album(conn, name, config=config, k=k, parent_id=parent_id)
    return jsonify({"status": "created", "id": album_id}), 201


@bp.get("/api/v1/albums/<int:album_id>")
def api_album_get(album_id: int) -> tuple[Response, int]:
    """Return a single album record by id, or 404 if not found."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    album = get_album(conn, album_id)
    if not album:
        raise NotFoundError("Album not found", album_id=album_id)
    return jsonify({"album": album}), 200


@bp.put("/api/v1/albums/<int:album_id>")
@requires_local_app
def api_album_update(album_id: int) -> tuple[Response, int]:
    """Update an album's mutable fields (name, config, k, parent_id).

    Renaming a smart album fires the registry's ``on_rename`` callback
    so subordinate state (e.g. cluster identity labels) stays in sync.
    """
    ctx = get_ctx()
    conn = ctx.get_conn()
    album = get_album(conn, album_id)
    if not album:
        raise NotFoundError("Album not found", album_id=album_id)
    data = request.get_json(silent=True) or {}
    kwargs: dict[str, Any] = {}
    if "name" in data:
        n = str(data["name"]).strip()
        if len(n) > 255:
            raise ValidationError(
                "Name too long (max 255 characters)",
                field="name",
                max_length=255,
                actual_length=len(n),
            )
        kwargs["name"] = n
    if "config" in data:
        cfg_update = data["config"]
        if cfg_update is not None:
            cfg_update = {k: v for k, v in cfg_update.items() if k in _ALLOWED_CONFIG_KEYS}
            existing = album.get("config") or {}
            kwargs["config"] = {**existing, **cfg_update}
    if "k" in data:
        kwargs["k"] = clamp_k(data["k"])
    if "parent_id" in data:
        pid = data["parent_id"]
        kwargs["parent_id"] = int(pid) if pid is not None else None
    if kwargs:
        update_album(conn, album_id, **kwargs)

    # Smart album rename hook — registry-driven so a new album type
    # with cascading rename logic registers its callback in
    # SmartAlbumRegistry instead of being hard-coded here.
    if "name" in kwargs:
        from bpp.db.smart_albums import SmartAlbumRegistry

        on_rename = SmartAlbumRegistry.get_on_rename(album.get("album_type", ""))
        if on_rename is not None:
            on_rename(conn, album, kwargs["name"])

    return jsonify({"status": "updated"}), 200


@bp.delete("/api/v1/albums/<int:album_id>")
@requires_local_app
def api_album_delete(album_id: int) -> tuple[Response, int]:
    """Delete an album. Smart albums are also recorded in
    ``dismissed_smart_albums`` so the next refresh doesn't recreate
    them. Refuses to delete the built-in All Photos album."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    album = get_album(conn, album_id)
    if not album:
        raise NotFoundError("Album not found", album_id=album_id)
    # L1 / review 2026-05-31: registry-driven undeletable check so
    # plugins can mark their own album types undeletable without
    # editing this site. SmartAlbumRegistry.is_undeletable("all")
    # returns True for the built-in.
    from bpp.db.smart_albums import SmartAlbumRegistry

    if SmartAlbumRegistry.is_undeletable(album["album_type"]):
        raise ValidationError(
            f"Cannot delete album type {album['album_type']!r}",
            album_id=album_id,
            album_type=album["album_type"],
        )
    # Permanently dismiss smart albums so they aren't recreated on next analysis
    if album["album_type"].startswith("smart_") and album.get("rule"):
        conn.execute(
            "INSERT OR IGNORE INTO dismissed_smart_albums (album_type, rule_json) VALUES (?, ?)",
            (album["album_type"], json.dumps(album["rule"])),
        )
        conn.commit()
    delete_album(conn, album_id)
    return jsonify({"status": "deleted"}), 200


@bp.get("/api/v1/albums/<int:album_id>/photos")
def api_album_photos(album_id: int) -> tuple[Response, int]:
    """Return paginated photos in an album, including soft-deleted rows.

    Query params: ``limit`` (1..5000), ``offset``, ``slim`` (1 returns a
    minimal row set). For the Duplicates smart album the response
    additionally attaches ``similar_photos`` siblings keyed by phash.
    """
    ctx = get_ctx()
    conn = ctx.get_conn()
    album = get_album(conn, album_id)
    if not album:
        raise NotFoundError("Album not found", album_id=album_id)
    limit = request.args.get("limit", type=int)
    offset = request.args.get("offset", default=0, type=int)
    slim = request.args.get("slim", default="0") == "1"
    if limit is not None:
        limit = max(1, min(limit, 5000))
    offset = max(0, offset)
    photos_data = get_album_photos(
        conn, album_id, include_deleted=True, limit=limit, offset=offset, slim=slim
    )
    photos = [
        ctx.build_photo_dict(item, selected=bool(item.get("selected"))) for item in photos_data
    ]
    for photo, raw in zip(photos, photos_data, strict=True):
        photo["override"] = raw.get("override")
        photo["favorite"] = bool(raw.get("favorite"))

    # M6 / review 2026-05-31: registry-driven UI metadata. The
    # Duplicates album registered _smart_duplicates_ui_metadata which
    # delegates to _attach_cluster_siblings_from_db; plugins can ship
    # their own ``ui_metadata_fn`` for new types without editing here.
    from bpp.db.smart_albums import SmartAlbumRegistry

    ui_fn = SmartAlbumRegistry.get_ui_metadata_fn(album.get("album_type", ""))
    if ui_fn is not None:
        ui_fn(conn, ctx, photos, photos_data)
    total = album.get("photo_count", 0)
    active_count = sum(1 for p in photos if not p.get("deleted_at"))
    return (
        jsonify(
            {
                "photos": photos,
                "count": active_count,
                "total": total,
                "album": album,
                "limit": limit,
                "offset": offset,
                "has_more": limit is not None and len(photos_data) == limit,
            }
        ),
        200,
    )


@bp.get("/api/v1/albums/<int:album_id>/stats")
def api_album_stats(album_id: int) -> tuple[Response, int]:
    """Return enriched stats for an album (date range, avg score, people, etc.)."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    album = get_album(conn, album_id)
    if not album:
        raise NotFoundError("Album not found", album_id=album_id)

    # Single combined stats query (date range, avg score, GPS, size, video, count)
    # read p.gps_lat directly instead of json_extract on exif_json
    # — same answer, but the partial idx_photos_gps index covers the
    # CASE expression's predicate.
    _active = f"{active_photo_sql('p')}"
    row = conn.execute(
        "SELECT MIN(p.date), MAX(p.date), AVG(p.aggregate_score), "
        "SUM(CASE WHEN p.gps_lat IS NOT NULL THEN 1 ELSE 0 END), "
        "COUNT(*), "
        "COALESCE(SUM(p.file_size), 0), "
        "SUM(CASE WHEN p.is_video = 1 THEN 1 ELSE 0 END) "
        "FROM album_photos ap "
        "JOIN photos p ON p.id = ap.photo_id "
        f"WHERE ap.album_id = ? AND {_active}",
        (album_id,),
    ).fetchone()
    min_date, max_date, avg_score, gps_count, total, disk_size, video_count = (
        row if row else (None, None, None, 0, 0, 0, 0)
    )

    # People count (distinct face clusters, separate table)
    people_count = 0
    try:
        people_row = conn.execute(
            "SELECT COUNT(DISTINCT fe.cluster_id) "
            "FROM face_embeddings fe "
            "JOIN album_photos ap ON ap.photo_id = fe.photo_id "
            "JOIN photos p ON p.id = fe.photo_id "
            f"WHERE ap.album_id = ? AND fe.cluster_id >= 0 AND {_active}",
            (album_id,),
        ).fetchone()
        people_count = people_row[0] if people_row else 0
    except Exception:
        log.warning("Failed to count face clusters for album %d", album_id, exc_info=True)

    return jsonify(
        {
            "total": total,
            "date_min": min_date,
            "date_max": max_date,
            "avg_score": round(avg_score, 3) if avg_score else None,
            "gps_count": gps_count or 0,
            "people_count": people_count,
            "disk_size": disk_size,
            "video_count": video_count,
        }
    ), 200


@bp.post("/api/v1/albums/<int:album_id>/add-photos")
@requires_local_app
def api_album_add_photos(album_id: int) -> tuple[Response, int]:
    """Attach the given filepaths to a manual album. New rows are
    appended; existing memberships are skipped silently."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    filepaths = data.get("filepaths", [])
    if not filepaths:
        raise ValidationError("filepaths required", field="filepaths")
    conn = ctx.get_conn()
    album = get_album(conn, album_id)
    if not album:
        raise NotFoundError("Album not found", album_id=album_id)
    photo_ids = get_photo_ids_by_paths(conn, filepaths)
    count = add_photos_to_album(conn, album_id, photo_ids)
    return jsonify({"status": "ok", "count": count}), 200


@bp.post("/api/v1/albums/<int:album_id>/remove-photos")
@requires_local_app
def api_album_remove_photos(album_id: int) -> tuple[Response, int]:
    """Detach the given filepaths from an album. Photos themselves are
    not deleted; only the album_photos membership is removed."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    filepaths = data.get("filepaths", [])
    if not filepaths:
        raise ValidationError("filepaths required", field="filepaths")
    conn = ctx.get_conn()
    album = get_album(conn, album_id)
    if not album:
        raise NotFoundError("Album not found", album_id=album_id)
    photo_ids = get_photo_ids_by_paths(conn, filepaths)
    remove_photos_from_album(conn, album_id, photo_ids)
    return jsonify({"status": "ok", "count": len(photo_ids)}), 200


@bp.get("/api/v1/albums/<int:album_id>/faces")
def api_album_faces(album_id: int) -> tuple[Response, int]:
    """Return face cluster IDs present in an album's photos."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    album = get_album(conn, album_id)
    if not album:
        raise NotFoundError("Album not found", album_id=album_id)
    rows = conn.execute(
        "SELECT DISTINCT fe.cluster_id "
        "FROM face_embeddings fe "
        "JOIN album_photos ap ON ap.photo_id = fe.photo_id "
        "JOIN photos p ON p.id = fe.photo_id "
        f"WHERE ap.album_id = ? AND fe.cluster_id >= 0 AND {active_photo_sql('p')}",
        (album_id,),
    ).fetchall()
    cluster_ids = [r[0] for r in rows]
    return jsonify({"cluster_ids": cluster_ids}), 200


@bp.get("/api/v1/albums/time/months")
def api_time_months() -> tuple[Response, int]:
    """Return month-level photo counts for a given year."""
    year = request.args.get("year", "")
    if not year or len(year) != 4 or not year.isdigit():
        raise ValidationError(
            "Valid 4-digit year required",
            field="year",
            value=year,
        )
    ctx = get_ctx()
    conn = ctx.get_conn()
    rows = conn.execute(
        "SELECT substr(date,6,2) AS month, COUNT(*) AS cnt "
        "FROM photos WHERE substr(date,1,4)=? "
        f"AND {ACTIVE_PHOTO_SQL} "
        "GROUP BY month ORDER BY month",
        (year,),
    ).fetchall()
    months = [{"month": int(r[0]), "count": r[1]} for r in rows if r[0]]
    return jsonify({"year": year, "months": months}), 200


@bp.post("/api/v1/albums/refresh-smart")
@requires_local_app
def api_refresh_smart_albums() -> tuple[Response, int]:
    """Re-run all smart album generators against current photo data and
    return the refreshed album list. Used after large mutations (face
    merges, tag edits, batch deletes) to resync derived collections."""
    from bpp.db.smart_albums import refresh_smart_albums

    ctx = get_ctx()
    conn = ctx.get_conn()
    refresh_smart_albums(conn)
    albums = db_list_albums(conn)
    return jsonify({"status": "refreshed", "albums": albums}), 200
