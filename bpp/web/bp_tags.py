"""Tags blueprint: CRUD, photo tagging, search, batch operations."""

from __future__ import annotations

import sqlite3

from flask import Blueprint, Response, jsonify, request

from bpp.db.smart_albums import _refresh_tag_albums
from bpp.db.tags import (
    add_tag_to_photo,
    bulk_tag_photos,
    bulk_untag_photos,
    create_tag,
    delete_tag,
    get_photo_tags,
    get_photos_by_tag,
    get_tag,
    list_tags_with_counts,
    merge_tags,
    remove_tag_from_photo,
    rename_tag,
    search_tags,
)
from bpp.errors import ConflictError, NotFoundError, ValidationError
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

bp = Blueprint("tags", __name__)


@bp.get("/api/v1/tags")
def api_list_tags() -> tuple[Response, int]:
    """List all tags with photo counts + a cover thumb hash per tag."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    tags = list_tags_with_counts(conn)
    thumbs = ctx.thumbs
    for t in tags:
        fp = t.pop("cover_filepath", None)
        t["cover_thumb_hash"] = thumbs.get_hash(fp) if fp and thumbs else None
    return jsonify({"tags": tags}), 200


@bp.get("/api/v1/tags/<int:tag_id>/photos")
def api_tag_photos(tag_id: int) -> tuple[Response, int]:
    """All active photos carrying a tag — feeds the Tags browse view's
    click-through grid (standard photo dicts, lightbox-compatible)."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    tag = get_tag(conn, tag_id)
    if tag is None:
        raise NotFoundError("Tag not found", tag_id=tag_id)
    photos = [ctx.build_photo_dict(item) for item in get_photos_by_tag(conn, tag_id)]
    return jsonify({"tag": tag, "photos": photos, "count": len(photos)}), 200


@bp.post("/api/v1/tags/<int:tag_id>/merge")
@requires_local_app
def api_merge_tags(tag_id: int) -> tuple[Response, int]:
    """Merge this tag INTO ``target_tag_id`` (JSON body): every photo
    tagged with this tag becomes tagged with the target, then this tag
    is deleted. Duplicate links collapse."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    target_id = data.get("target_tag_id")
    if not isinstance(target_id, int):
        raise ValidationError("target_tag_id (int) required", field="target_tag_id")
    if target_id == tag_id:
        raise ValidationError("cannot merge a tag into itself", field="target_tag_id")
    conn = ctx.get_conn()
    if get_tag(conn, tag_id) is None:
        raise NotFoundError("Tag not found", tag_id=tag_id)
    if get_tag(conn, target_id) is None:
        raise NotFoundError("Target tag not found", tag_id=target_id)
    moved = merge_tags(conn, tag_id, target_id)
    _refresh_tag_albums(conn)
    return jsonify({"moved": moved, "target_tag_id": target_id}), 200


@bp.post("/api/v1/tags")
@requires_local_app
def api_create_tag() -> tuple[Response, int]:
    """Create a new tag from a JSON body containing ``name``. Names are
    lowercased on insert. Returns the new tag id and normalized name."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        raise ValidationError("name required", field="name")
    conn = ctx.get_conn()
    tag_id = create_tag(conn, name)
    return jsonify({"id": tag_id, "name": name.lower()}), 200


@bp.put("/api/v1/tags/<int:tag_id>")
@requires_local_app
def api_rename_tag(tag_id: int) -> tuple[Response, int]:
    """Rename an existing tag. Returns 409 when the new name collides
    with another tag (the unique index raises IntegrityError)."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        raise ValidationError("name required", field="name")
    conn = ctx.get_conn()
    try:
        rename_tag(conn, tag_id, name)
    except sqlite3.IntegrityError as e:
        raise ConflictError(
            "A tag with that name already exists",
            field="name",
            value=name,
        ) from e
    return jsonify({"id": tag_id, "name": name.lower()}), 200


@bp.delete("/api/v1/tags/<int:tag_id>")
@requires_local_app
def api_delete_tag(tag_id: int) -> tuple[Response, int]:
    """Delete a tag and detach it from every photo, then refresh the
    tag-driven smart albums so the deleted tag's album disappears."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    delete_tag(conn, tag_id)
    _refresh_tag_albums(conn)
    return jsonify({"status": "ok"}), 200


@bp.get("/api/v1/tags/search")
def api_search_tags() -> tuple[Response, int]:
    """Return tags whose names match ``q`` (prefix match, case-
    insensitive). Empty query returns an empty list — used for
    autocomplete in the tag picker."""
    ctx = get_ctx()
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"tags": []}), 200
    conn = ctx.get_conn()
    tags = search_tags(conn, q)
    return jsonify({"tags": tags}), 200


# ── Photo-tag routes ──


@bp.get("/api/v1/photos/<int:photo_id>/tags")
def api_get_photo_tags(photo_id: int) -> tuple[Response, int]:
    """Get tags for a specific photo."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    tags = get_photo_tags(conn, photo_id)
    return jsonify({"tags": tags}), 200


@bp.post("/api/v1/photos/<int:photo_id>/tags")
@requires_local_app
def api_add_photo_tag(photo_id: int) -> tuple[Response, int]:
    """Add a tag to a photo. Accepts tag_id or name (creates if needed)."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    conn = ctx.get_conn()

    tag_id = data.get("tag_id")
    if tag_id is None:
        name = data.get("name", "").strip()
        if not name:
            raise ValidationError("tag_id or name required")
        tag_id = create_tag(conn, name)

    add_tag_to_photo(conn, photo_id, tag_id)
    _refresh_tag_albums(conn)
    return jsonify({"status": "ok", "tag_id": tag_id}), 200


@bp.delete("/api/v1/photos/<int:photo_id>/tags/<int:tag_id>")
@requires_local_app
def api_remove_photo_tag(photo_id: int, tag_id: int) -> tuple[Response, int]:
    """Detach a tag from a single photo and refresh the tag-driven
    smart albums so empty tag albums disappear."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    remove_tag_from_photo(conn, photo_id, tag_id)
    _refresh_tag_albums(conn)
    return jsonify({"status": "ok"}), 200


# ── Batch operations ──


@bp.post("/api/v1/tags/batch")
@requires_local_app
def api_batch_tag() -> tuple[Response, int]:
    """Attach a single ``tag_id`` to many ``photo_ids`` in one
    transaction. Refreshes the tag-driven smart albums and returns
    the number of new attachments inserted."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    photo_ids = data.get("photo_ids", [])
    tag_id = data.get("tag_id")
    if not photo_ids or tag_id is None:
        raise ValidationError("photo_ids and tag_id required")
    conn = ctx.get_conn()
    count = bulk_tag_photos(conn, photo_ids, tag_id)
    _refresh_tag_albums(conn)
    return jsonify({"status": "ok", "count": count}), 200


@bp.post("/api/v1/tags/batch/remove")
@requires_local_app
def api_batch_untag() -> tuple[Response, int]:
    """Remove a tag from multiple photos."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    photo_ids = data.get("photo_ids", [])
    tag_id = data.get("tag_id")
    if not photo_ids or tag_id is None:
        raise ValidationError("photo_ids and tag_id required")
    conn = ctx.get_conn()
    count = bulk_untag_photos(conn, photo_ids, tag_id)
    _refresh_tag_albums(conn)
    return jsonify({"status": "ok", "count": count}), 200
