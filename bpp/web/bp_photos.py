"""Photos blueprint: CRUD, recompute, optimize, export, overrides, favorites."""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from bpp.db.albums import (
    ensure_all_photos_album,
    get_album_overrides_and_favorites,
)
from bpp.db.albums import (
    set_favorites_bulk as db_set_favorites_bulk,
)
from bpp.db.albums import (
    set_override as db_set_override,
)
from bpp.db.albums import (
    set_overrides_bulk as db_set_overrides_bulk,
)
from bpp.db.albums import (
    toggle_favorite as db_toggle_favorite,
)
from bpp.db.photos import (
    get_all_photos as db_get_all_photos,
)
from bpp.db.photos import (
    get_date_distribution,
    get_photo,
    get_photo_by_path,
    get_photo_ids_by_paths,
    get_photos_page,
    get_photos_with_gps,
    set_sensitive_override,
)
from bpp.errors import NotFoundError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.photo_dict import build_photo_dict_map
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx
from bpp.web.thumbnails import ThumbnailCache

log = get_logger(__name__)

bp = Blueprint("photos", __name__)


@bp.get("/api/v1/photos")
def api_photos() -> tuple[Response, int]:
    """List analyzed photos with metadata + scores.

    Pagination: a 50k+ photo library would otherwise be serialized
    in a single response (5-10s, 10-50MB JSON). Capped at `limit`
    rows per request with `offset` for paging. Defaults preserve
    historical behavior for libraries up to 5k photos and bound the
    worst case for everyone else.

    Query params:
        limit:  rows to return (default 5000, max 50000, min 1)
        offset: rows to skip (default 0)

    Response:
        {
            "photos": [...],
            "count":  <returned_rows>,         # backward-compat
            "total":  <library_size>,
            "limit":  <effective_limit>,
            "offset": <effective_offset>,
            "has_more": bool,
        }

    `count` is preserved for callers that don't paginate.
    """
    ctx = get_ctx()
    analysis = ctx.load_analysis_if_needed()
    if analysis is None:
        raise NotFoundError("No analysis data. Run analyze first.")

    # Parse + clamp pagination args. Bad input (negative offset,
    # non-int) gets the default rather than a 400 — REST listing
    # endpoints are usually forgiving.
    try:
        limit = int(request.args.get("limit", 5000))
    except (TypeError, ValueError):
        limit = 5000
    try:
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    limit = max(1, min(limit, 50000))
    offset = max(0, offset)

    # total from in-memory list (already loaded for thumbnails/CLIP side effects)
    # page rows from DB so Python never iterates N items for a small page window
    total = len(analysis)
    page = get_photos_page(ctx.get_conn(), limit=limit, offset=offset, include_deleted=True)
    photos = [ctx.build_photo_dict(item) for item in page]
    return (
        jsonify(
            {
                "photos": photos,
                "count": len(photos),
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(photos) < total,
            }
        ),
        200,
    )


@bp.get("/api/v1/photos/timeline")
def api_photos_timeline() -> tuple[Response, int]:
    """Return photo count distribution grouped by month."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    album_id = request.args.get("album_id", type=int)
    months = get_date_distribution(conn, album_id=album_id)
    return jsonify({"months": months}), 200


@bp.get("/api/v1/photos/map")
def api_photos_map() -> tuple[Response, int]:
    """Return photos with GPS coordinates for map view (lightweight).

    Pagination: mirrors `/api/v1/photos`. A 100k-photo library could
    otherwise produce a multi-megabyte map payload that stalls the
    browser on parse and pins the DB cursor open during serialize.
    Defaults preserve historical behavior for small libraries.

    Query params:
        album_id: optional album filter
        limit:    rows to return (default 5000, max 50000, min 1)
        offset:   rows to skip (default 0)

    Response:
        {
            "photos": [...],
            "count":  <returned_rows>,   # backward-compat
            "total":  <gps_photo_count>,
            "limit":  <effective_limit>,
            "offset": <effective_offset>,
            "has_more": bool,
        }
    """
    ctx = get_ctx()
    conn = ctx.get_conn()
    album_id = request.args.get("album_id", type=int)

    try:
        limit = int(request.args.get("limit", 5000))
    except (TypeError, ValueError):
        limit = 5000
    try:
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        offset = 0
    limit = max(1, min(limit, 50000))
    offset = max(0, offset)

    from bpp.db.photos import count_photos_with_gps

    total = count_photos_with_gps(conn, album_id=album_id)
    page = get_photos_with_gps(conn, album_id=album_id, limit=limit, offset=offset)
    # Minimal map-projection (~7 fields) instead of full build_photo_dict
    # to keep the response light at thousands of pins per page.
    photos_out = [build_photo_dict_map(r, ctx.thumbs) for r in page]
    return (
        jsonify(
            {
                "photos": photos_out,
                "count": len(photos_out),
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(photos_out) < total,
            }
        ),
        200,
    )


@bp.get("/api/v1/photos/<int:photo_id>")
def api_photo_detail(photo_id: int) -> tuple[Response, int]:
    """Return full photo dict for a single photo by ID.

    P6/P7: uses the BppError handler for the 404 (consistent error
    envelope) and the PhotoDict-typed builder for the success body.
    Migration template — copy this shape for other endpoints as you
    touch them.
    """
    ctx = get_ctx()
    conn = ctx.get_conn()
    row = get_photo(conn, photo_id)
    if not row:
        raise NotFoundError("Photo not found", photo_id=photo_id)
    return jsonify(ctx.build_photo_dict(row)), 200


@bp.get("/api/v1/photos/preview")
def api_photos_preview() -> tuple[Response, int]:
    """Return photos from DB for preview before analysis is complete."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    data = db_get_all_photos(conn)
    if not data:
        return jsonify({"photos": [], "count": 0}), 200
    if ctx.thumbs is None:
        ctx.thumbs = ThumbnailCache(ctx.dirs["thumbs"])
    ctx.thumbs.build_map(data)
    photos_out = []
    for item in data:
        d = ctx.build_photo_dict(item)
        d["analyzed"] = item.get("aggregate_score") is not None
        photos_out.append(d)
    return jsonify({"photos": photos_out, "count": len(photos_out)}), 200


@bp.post("/api/v1/override")
@requires_local_app
def api_set_override() -> tuple[Response, int]:
    """Set the include/exclude override for a single photo on the All
    Photos album. Records dedup feedback when the override implies a
    user disagreement with the auto-selected duplicate."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    filepath = data.get("filepath")
    mode = data.get("mode")
    if not filepath:
        raise ValidationError("filepath required", field="filepath")
    conn = ctx.get_conn()
    photo = get_photo_by_path(conn, filepath)
    if not photo:
        raise NotFoundError("Photo not found", filepath=filepath)
    album_id = ensure_all_photos_album(conn)
    db_set_override(conn, album_id, photo["id"], mode)

    selected_paths = set(data.get("selected_paths", []))
    feedback_recorded = ctx.check_dedup_feedback(
        conn, photo["id"], filepath, mode, selected_paths, album_id
    )
    return jsonify({"status": "ok", "feedback_recorded": feedback_recorded}), 200


@bp.post("/api/v1/favorite")
@requires_local_app
def api_toggle_favorite() -> tuple[Response, int]:
    """Toggle the global favorite flag on a photo (stored against the
    All Photos album). Returns the new boolean state."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    filepath = data.get("filepath")
    if not filepath:
        raise ValidationError("filepath required", field="filepath")
    conn = ctx.get_conn()
    photo = get_photo_by_path(conn, filepath)
    if not photo:
        raise NotFoundError("Photo not found", filepath=filepath)
    album_id = ensure_all_photos_album(conn)
    new_state = db_toggle_favorite(conn, album_id, photo["id"])
    return jsonify({"status": "ok", "favorite": new_state}), 200


@bp.post("/api/v1/photos/sensitive")
@requires_local_app
def api_set_sensitive_override() -> tuple[Response, int]:
    """Set the user's sensitive-photo override on a single photo.

    Body: ``{filepath, override}`` where override is ``1`` (sensitive),
    ``0`` (not sensitive), or ``null`` (clear — follow the model).
    Returns the new derived ``is_sensitive`` verdict so the UI can
    update the chip without a refetch.
    """
    from bpp.web.photo_dict import is_sensitive_item

    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    filepath = data.get("filepath")
    if not filepath:
        raise ValidationError("filepath required", field="filepath")
    override = data.get("override")
    if override not in (None, 0, 1):
        raise ValidationError("override must be 1, 0, or null", field="override")
    conn = ctx.get_conn()
    photo = get_photo_by_path(conn, filepath)
    if not photo:
        raise NotFoundError("Photo not found", filepath=filepath)
    set_sensitive_override(conn, photo["id"], override)

    # Patch the in-memory analysis item so the grid/lightbox see the
    # change without a full reload.
    new_item = dict(photo, sensitive_override=override)
    with ctx.lock:
        analysis = ctx.state.get("analysis")
        if analysis:
            for item in analysis:
                if item.get("filepath") == filepath:
                    item["sensitive_override"] = override
                    break

    # Membership of the Sensitive album follows the override immediately.
    from bpp.db.smart_albums import refresh_smart_albums

    refresh_smart_albums(conn, kinds={"smart_sensitive"})
    from bpp.constants import SENSITIVE_NUDITY_THRESHOLD

    threshold = ctx.config.get("sensitive_nudity_threshold", SENSITIVE_NUDITY_THRESHOLD)
    return jsonify({"status": "ok", "is_sensitive": is_sensitive_item(new_item, threshold)}), 200


@bp.post("/api/v1/batch/override")
@requires_local_app
def api_batch_override() -> tuple[Response, int]:
    """Bulk-set the include/exclude override on a list of filepaths
    against the All Photos album. Returns the row count updated."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    filepaths = data.get("filepaths", [])
    mode = data.get("mode")
    if not filepaths:
        raise ValidationError("filepaths required", field="filepaths")
    conn = ctx.get_conn()
    album_id = ensure_all_photos_album(conn)
    photo_ids = get_photo_ids_by_paths(conn, filepaths)
    count = db_set_overrides_bulk(conn, album_id, photo_ids, mode)
    return jsonify({"status": "ok", "count": count}), 200


@bp.post("/api/v1/batch/favorite")
@requires_local_app
def api_batch_favorite() -> tuple[Response, int]:
    """Bulk-set the global favorite flag (default true) on a list of
    filepaths against the All Photos album."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    filepaths = data.get("filepaths", [])
    favorite = data.get("favorite", True)
    if not filepaths:
        raise ValidationError("filepaths required", field="filepaths")
    conn = ctx.get_conn()
    album_id = ensure_all_photos_album(conn)
    photo_ids = get_photo_ids_by_paths(conn, filepaths)
    count = db_set_favorites_bulk(conn, album_id, photo_ids, bool(favorite))
    return jsonify({"status": "ok", "count": count}), 200


@bp.get("/api/v1/overrides")
def api_get_overrides() -> tuple[Response, int]:
    """Return the include/exclude override map and favorites set for
    the All Photos album, used by the SPA on initial load."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    album_id = ensure_all_photos_album(conn)
    overrides, favorites = get_album_overrides_and_favorites(conn, album_id)
    return jsonify({"overrides": overrides, "favorites": favorites}), 200
