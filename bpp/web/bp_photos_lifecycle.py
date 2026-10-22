"""Photo lifecycle endpoints: delete, restore, hide, listings.

Extracted from bp_photos_manage.py during the v0.1 cleanup. These
endpoints all manage the trash / hidden state of photos and surface
the Recently Deleted / Hidden listing views in the UI.

Sub-clusters:
* Soft delete + restore: api_photos_delete, api_photos_restore
* Permanent delete (with crash-safe journal): api_photos_delete_permanent,
  (register_permanent_delete_recovery and _apply_permanent_delete_disk
  now live in bpp/web/permanent_delete.py)
* Hide / unhide: api_photos_hide, api_photos_unhide
* Listings (paginated): api_photos_deleted, api_photos_hidden

Helpers used here that still live in bp_photos_manage:
  get_sha256_map (bpp.db.photos_sha, for the journaled
  permanent-delete payload).
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify, request

from bpp.db.albums import sync_all_photos_album
from bpp.db.photos import (
    count_deleted_photos,
    count_hidden_photos,
    get_deleted_photos,
    get_hidden_photos,
    get_photo_id_map_by_paths,
    get_photo_ids_by_paths,
    hide_photos,
    permanent_delete_photos,
    restore_photos,
    soft_delete_photos,
    unhide_photos,
)
from bpp.errors import ValidationError
from bpp.utils.logging import get_logger
from bpp.web.request_validation import field, validate_json
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("photos_lifecycle", __name__)


@bp.post("/api/v1/photos/delete")
@validate_json(filepaths=field())
@requires_local_app
def api_photos_delete(filepaths: list[str]) -> tuple[Response, int]:
    """Soft-delete photos (move to Recently Deleted).

    opted into `@validate_json` — the boilerplate
    `data = request.get_json(...)` + `if not filepaths: 400` is
    now a one-line decorator. Endpoints opt in incrementally;
    other handlers in this file still use the inline form.
    """
    if not filepaths:
        raise ValidationError("filepaths required", field="filepaths")
    ctx = get_ctx()
    conn = ctx.get_conn()
    photo_ids = get_photo_ids_by_paths(conn, filepaths)
    count = soft_delete_photos(conn, photo_ids)
    log.info("Soft-deleted %d photos", count)
    ctx.invalidate_analysis()
    return jsonify({"status": "ok", "count": count}), 200


@bp.post("/api/v1/photos/restore")
@validate_json(filepaths=field())
@requires_local_app
def api_photos_restore(filepaths: list[str]) -> tuple[Response, int]:
    """Restore soft-deleted photos by clearing ``deleted_at`` for the
    given filepaths, resyncing the All Photos album membership, and
    invalidating the cached analysis so the UI sees the photos
    again."""
    if not filepaths:
        raise ValidationError("filepaths required", field="filepaths")
    ctx = get_ctx()
    conn = ctx.get_conn()
    photo_ids = get_photo_ids_by_paths(conn, filepaths)
    count = restore_photos(conn, photo_ids)
    log.info("Restored %d photos from trash", count)
    sync_all_photos_album(conn)
    ctx.invalidate_analysis()
    return jsonify({"status": "ok", "count": count}), 200


@bp.post("/api/v1/photos/delete-permanent")
@requires_local_app
def api_photos_delete_permanent() -> tuple[Response, int]:
    """Permanently delete photos from DB and disk.

    Journals the operation before the DB delete: if we crash between
    DB delete and disk cleanup, startup recovery (in
    bpp/db/journal.py) re-runs the disk cleanup so files don't
    orphan. The DB delete itself is one transaction (atomic via
    SQLite); the at-risk window is the disk-side loop.
    """
    from bpp.db.journal import journal_complete, journal_start

    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    if data.get("confirmation") != "delete":
        raise ValidationError(
            "confirmation='delete' required",
            field="confirmation",
        )
    filepaths = data.get("filepaths", [])
    if not filepaths:
        raise ValidationError("filepaths required", field="filepaths")
    conn = ctx.get_conn()
    path_to_id = get_photo_id_map_by_paths(conn, filepaths)  # rows of this library only
    photo_ids = list(path_to_id.values())
    # Grab sha256 before deletion (needed for content-addressed inpaint cache).
    # Helper lives in bp_photos_manage — lazy import avoids any
    # ordering-sensitivity from blueprint registration.
    from bpp.db.photos_sha import get_sha256_map

    sha256_map = get_sha256_map(conn, filepaths)

    # Build set of allowed parent directories for disk deletion. Done
    # before the journal write so the recovery handler has the same
    # set if it ever needs to replay.
    # shared allowlist builder.
    from bpp.utils.path_validation import build_library_allowlist

    allowed = build_library_allowlist(
        library_path=ctx.state.get("library_path"),
        workdir=ctx.state.get("workdir"),
    )

    # Open journal entry BEFORE the DB delete. If we crash anywhere
    # below before journal_complete, startup recovery sees this entry
    # and re-runs the disk-cleanup half on the same paths.
    journal_id = journal_start(
        conn,
        "permanent_delete",
        {
            # Only paths that are rows in this library: recovery replays
            # the disk half on this list, so it must not be request input.
            "filepaths": sorted(path_to_id),
            "sha256_map": dict(sha256_map),
            "allowed_dirs": allowed,
        },
    )

    deleted_paths = permanent_delete_photos(conn, photo_ids)
    from bpp.web.permanent_delete import _apply_permanent_delete_disk

    removed = _apply_permanent_delete_disk(ctx, deleted_paths, sha256_map, allowed)
    journal_complete(conn, journal_id)
    log.info("Permanently deleted %d photos, %d files removed from disk", len(photo_ids), removed)
    ctx.invalidate_analysis()
    return jsonify({"status": "ok", "count": len(photo_ids), "files_removed": removed}), 200


@bp.get("/api/v1/photos/deleted")
def api_photos_deleted() -> tuple[Response, int]:
    """Return soft-deleted photos (rows with non-null ``deleted_at``).

    Paginated: ``?limit=200&offset=0`` by default. ``total`` is the
    full row count; ``photos`` is the slice. UI advances offset until
    photos.length < limit (or offset+photos.length >= total). Backs
    the Recently Deleted smart album view.
    """
    ctx = get_ctx()
    conn = ctx.get_conn()
    limit = max(1, min(1000, request.args.get("limit", 200, type=int)))
    offset = max(0, request.args.get("offset", 0, type=int))
    deleted = get_deleted_photos(conn, limit=limit, offset=offset)
    photos = [ctx.build_photo_dict(item) for item in deleted]
    total = count_deleted_photos(conn)
    return (
        jsonify({"photos": photos, "total": total, "limit": limit, "offset": offset}),
        200,
    )


@bp.post("/api/v1/photos/hide")
@requires_local_app
def api_photos_hide() -> tuple[Response, int]:
    """Hide photos (remove from normal views)."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    filepaths = data.get("filepaths", [])
    if not filepaths:
        raise ValidationError("filepaths required", field="filepaths")
    conn = ctx.get_conn()
    photo_ids = get_photo_ids_by_paths(conn, filepaths)
    count = hide_photos(conn, photo_ids)
    log.info("Hid %d photos from grid", count)
    ctx.invalidate_analysis()
    return jsonify({"status": "ok", "count": count}), 200


@bp.post("/api/v1/photos/unhide")
@requires_local_app
def api_photos_unhide() -> tuple[Response, int]:
    """Unhide photos (restore to normal views)."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    filepaths = data.get("filepaths", [])
    if not filepaths:
        raise ValidationError("filepaths required", field="filepaths")
    conn = ctx.get_conn()
    photo_ids = get_photo_ids_by_paths(conn, filepaths)
    count = unhide_photos(conn, photo_ids)
    log.info("Unhid %d photos", count)
    sync_all_photos_album(conn)
    ctx.invalidate_analysis()
    return jsonify({"status": "ok", "count": count}), 200


@bp.get("/api/v1/photos/hidden")
def api_photos_hidden() -> tuple[Response, int]:
    """Return hidden photos (rows with non-null ``hidden_at`` AND
    null ``deleted_at``).

    Paginated: same ``?limit=200&offset=0`` contract as
    ``/api/v1/photos/deleted``. Backs the Hidden smart album view.
    """
    ctx = get_ctx()
    conn = ctx.get_conn()
    limit = max(1, min(1000, request.args.get("limit", 200, type=int)))
    offset = max(0, request.args.get("offset", 0, type=int))
    hidden = get_hidden_photos(conn, limit=limit, offset=offset)
    photos = [ctx.build_photo_dict(item) for item in hidden]
    total = count_hidden_photos(conn)
    return (
        jsonify({"photos": photos, "total": total, "limit": limit, "offset": offset}),
        200,
    )


# --- Enhance (magic pop) routes ---
