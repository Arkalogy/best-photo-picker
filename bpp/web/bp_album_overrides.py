"""Per-photo override + favorite endpoints scoped to an album.

Extracted from bp_albums.py during the v0.1 cleanup. Four endpoints
mutate the album_photos row for a single photo (or batch of photos)
to set the include/exclude override or the favorite flag:

* ``/api/v1/albums/<id>/override`` — set one photo's override mode
* ``/api/v1/albums/<id>/favorite`` — toggle one photo's favorite
* ``/api/v1/albums/<id>/batch/override`` — set N photos' override
* ``/api/v1/albums/<id>/batch/favorite`` — set N photos' favorite

The single-photo override path also records dedup feedback (which
tunes the adaptive CLIP similarity threshold) via
``ctx.check_dedup_feedback``.
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

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
from bpp.db.photos import get_photo_by_path, get_photo_ids_by_paths
from bpp.errors import NotFoundError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("album_overrides", __name__)


@bp.post("/api/v1/albums/<int:album_id>/override")
@requires_local_app
def api_album_override(album_id: int) -> tuple[Response, int]:
    """Set a per-album include/exclude override on a photo (by filepath).

    Also records dedup feedback when the override changes the active
    selection — used to tune the adaptive CLIP similarity threshold.
    """
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
    db_set_override(conn, album_id, photo["id"], mode)

    selected_paths = set(data.get("selected_paths", []))
    feedback_recorded = ctx.check_dedup_feedback(
        conn, photo["id"], filepath, mode, selected_paths, album_id
    )
    return jsonify({"status": "ok", "feedback_recorded": feedback_recorded}), 200


@bp.post("/api/v1/albums/<int:album_id>/favorite")
@requires_local_app
def api_album_favorite(album_id: int) -> tuple[Response, int]:
    """Toggle the favorite flag on a single photo (by filepath) within
    an album. Returns the new ``favorite`` boolean state."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    filepath = data.get("filepath")
    if not filepath:
        raise ValidationError("filepath required", field="filepath")
    conn = ctx.get_conn()
    photo = get_photo_by_path(conn, filepath)
    if not photo:
        raise NotFoundError("Photo not found", filepath=filepath)
    new_state = db_toggle_favorite(conn, album_id, photo["id"])
    return jsonify({"status": "ok", "favorite": new_state}), 200


@bp.post("/api/v1/albums/<int:album_id>/batch/override")
@requires_local_app
def api_album_batch_override(album_id: int) -> tuple[Response, int]:
    """Bulk-set the include/exclude override on a list of filepaths
    within an album. Returns the number of rows updated."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    filepaths = data.get("filepaths", [])
    mode = data.get("mode")
    if not filepaths:
        raise ValidationError("filepaths required", field="filepaths")
    conn = ctx.get_conn()
    photo_ids = get_photo_ids_by_paths(conn, filepaths)
    count = db_set_overrides_bulk(conn, album_id, photo_ids, mode)
    return jsonify({"status": "ok", "count": count}), 200


@bp.post("/api/v1/albums/<int:album_id>/batch/favorite")
@requires_local_app
def api_album_batch_favorite(album_id: int) -> tuple[Response, int]:
    """Bulk-set the favorite flag (default true) on a list of filepaths
    within an album. Returns the number of rows updated."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    filepaths = data.get("filepaths", [])
    favorite = data.get("favorite", True)
    if not filepaths:
        raise ValidationError("filepaths required", field="filepaths")
    conn = ctx.get_conn()
    photo_ids = get_photo_ids_by_paths(conn, filepaths)
    count = db_set_favorites_bulk(conn, album_id, photo_ids, bool(favorite))
    return jsonify({"status": "ok", "count": count}), 200
