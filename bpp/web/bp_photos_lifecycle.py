"""Photo lifecycle endpoints: delete, restore, hide, listings.

Extracted from bp_photos_manage.py during the v0.1 cleanup. These
endpoints all manage the trash / hidden state of photos and surface
the Recently Deleted / Hidden listing views in the UI.

Sub-clusters:
* Soft delete + restore: api_photos_delete, api_photos_restore
* Permanent delete (with crash-safe journal): api_photos_delete_permanent,
  register_permanent_delete_recovery, _apply_permanent_delete_disk
* Hide / unhide: api_photos_hide, api_photos_unhide
* Listings (paginated): api_photos_deleted, api_photos_hidden

Helpers used here that still live in bp_photos_manage:
  _get_sha256_map (imported from there for the journaled
  permanent-delete payload).
"""

from __future__ import annotations

import os

from flask import Blueprint, Response, jsonify, request

from bpp.db.albums import sync_all_photos_album
from bpp.db.photos import (
    count_deleted_photos,
    count_hidden_photos,
    get_deleted_photos,
    get_hidden_photos,
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
    photo_ids = get_photo_ids_by_paths(conn, filepaths)
    # Grab sha256 before deletion (needed for content-addressed inpaint cache).
    # Helper lives in bp_photos_manage — lazy import avoids any
    # ordering-sensitivity from blueprint registration.
    from bpp.web.bp_photos_manage import _get_sha256_map

    sha256_map = _get_sha256_map(conn, filepaths)

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
            "filepaths": list(filepaths),
            "sha256_map": dict(sha256_map),
            "allowed_dirs": allowed,
        },
    )

    deleted_paths = permanent_delete_photos(conn, photo_ids)
    removed = _apply_permanent_delete_disk(ctx, deleted_paths, sha256_map, allowed)
    journal_complete(conn, journal_id)
    log.info("Permanently deleted %d photos, %d files removed from disk", len(photo_ids), removed)
    ctx.invalidate_analysis()
    return jsonify({"status": "ok", "count": len(photo_ids), "files_removed": removed}), 200


def register_permanent_delete_recovery(ctx: object, library_path: str | None = None) -> None:
    """Bind a recovery handler for the 'permanent_delete' journal kind.

    Called once during WebAppState.startup() with the live ctx so the
    handler can reach `ctx.thumbs`, `ctx.dirs`, etc. Idempotent
    registration (the journal module itself rejects different handlers
    for the same kind, so re-running this with the same ctx is safe;
    re-running with a different ctx during library switch picks up
    the new ctx via closure).

    P1 — ``library_path`` (optional, but passed by the state lifecycle):
    when provided, the handler is wrapped with
    :func:`bpp.db.journal.library_bound_recovery` so a switch_library
    between registration and recovery refuses to delete files in the
    wrong library. Without this, the disk-deletion branch would resolve
    paths against the new library's allowlist and ``ctx.thumbs``, which
    could remove files that share names across libraries.
    """
    from bpp.db.journal import library_bound_recovery, register_recovery_handler

    def _recover(_conn: object, payload: dict) -> bool:
        filepaths = payload.get("filepaths") or []
        sha256_map = payload.get("sha256_map") or {}
        allowed = payload.get("allowed_dirs") or []
        if not filepaths:
            return True  # nothing to do — entry can be deleted
        log.info("Recovering interrupted permanent_delete for %d files", len(filepaths))
        _apply_permanent_delete_disk(ctx, filepaths, sha256_map, allowed)
        return True

    if library_path is not None:

        def _live_library_path() -> str | None:
            paths = getattr(ctx, "paths", None)
            return getattr(paths, "library_path", None) if paths is not None else None

        handler = library_bound_recovery(
            library_path, _recover, library_path_getter=_live_library_path
        )
    else:
        handler = _recover
    register_recovery_handler("permanent_delete", handler, replace=True)


def _apply_permanent_delete_disk(
    ctx: object,
    filepaths: list[str],
    sha256_map: dict,
    allowed_dirs: list[str],
) -> int:
    """Disk-cleanup half of permanent_delete.

    Extracted so the journal recovery handler can call it on its own
    with the journaled payload. Idempotent: missing files / already-
    pruned cache hashes are no-ops.
    """
    # shared allowlist check (matches the open-folder /
    # reveal / export branches).
    from bpp.utils.path_validation import is_path_under_any

    removed = 0
    for fp in filepaths:
        if not is_path_under_any(fp, allowed_dirs):
            log.warning("Skipping file outside library/workdir: %s", fp)
            continue
        try:
            if os.path.isfile(fp):
                os.remove(fp)
                removed += 1
        except OSError as e:
            log.warning("Failed to remove file %s: %s", fp, e)
        # Clean up orphaned cache files (thumbnails, face/pet crops).
        # Recovery may run with ctx.thumbs == None (early startup); skip in that case.
        thumbs = getattr(ctx, "thumbs", None)
        if thumbs is not None:
            path_hash = thumbs.get_hash(fp)
            thumbs.remove_for_hash(
                path_hash,
                face_crop_dir=ctx.dirs.get("face_crops") if hasattr(ctx, "dirs") else None,
                pet_crop_dir=ctx.dirs.get("pet_crops") if hasattr(ctx, "dirs") else None,
                content_hash=sha256_map.get(fp) if isinstance(sha256_map, dict) else None,
            )
    return removed


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
