"""Photos management blueprint: delete, restore, hide, edits, rename, inpaint."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, jsonify, request

from bpp.constants import active_photo_sql
from bpp.db.batch_rename import apply_rename, build_rename_map
from bpp.db.edits import (
    get_photo_edits,
    reset_photo_edits,
    save_photo_edits,
)
from bpp.db.photos import (
    PHOTO_COLS_SLIM,
    get_photo_by_path,
    get_photo_id_map_by_paths,
    update_photo_date,
)
from bpp.db.photos_sha import get_sha256_map
from bpp.errors import BppError, NotFoundError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.paging import page_args, page_fields
from bpp.web.photo_cache import invalidate_photo_cache
from bpp.web.photo_dict import build_similar_entry
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("photos_manage", __name__)


@bp.get("/api/v1/duplicates/groups")
def api_duplicates_groups() -> tuple[Response, int]:
    """Return near-duplicate groups for the review flow.

    Groups by dup_cluster_id (assigned by assign_near_duplicate_clusters()).
    Falls back to exact phash grouping if clustering has never run.
    Groups are sorted by score difference descending (most useful first);
    optional ``limit`` / ``offset`` page them.
    """
    from collections import defaultdict

    from bpp.constants import ACTIVE_PHOTO_SQL

    ctx = get_ctx()
    conn = ctx.get_conn()
    thumbs = ctx.thumbs

    # Prefer dup_cluster_id grouping (hamming-distance clusters)
    any_clustered = conn.execute(
        f"SELECT 1 FROM photos WHERE cluster_size > 1 AND {ACTIVE_PHOTO_SQL} LIMIT 1"
    ).fetchone()

    if any_clustered:
        rows = conn.execute(
            f"SELECT id, filepath, original_filename, dup_cluster_id,"
            f" aggregate_score, blur_score, exposure_score,"
            f" face_score, composition_score, date_day"
            f" FROM photos"
            f" WHERE cluster_size > 1 AND {ACTIVE_PHOTO_SQL}"
        ).fetchall()
        group_key_idx = 3  # dup_cluster_id
    else:
        # Fallback: exact phash equality (pre-clustering state)
        rows = conn.execute(
            f"SELECT id, filepath, original_filename, phash,"
            f" aggregate_score, blur_score, exposure_score,"
            f" face_score, composition_score, date_day"
            f" FROM photos"
            f" WHERE phash IN ("
            f"   SELECT phash FROM photos"
            f"   WHERE phash IS NOT NULL AND {ACTIVE_PHOTO_SQL}"
            f"   GROUP BY phash HAVING COUNT(*) > 1"
            f" ) AND {ACTIVE_PHOTO_SQL}"
        ).fetchall()
        group_key_idx = 3  # phash

    limit, offset = page_args()
    if not rows:
        return jsonify({"groups": [], **page_fields([], 0, limit, offset)}), 200

    groups_map: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        key = r[group_key_idx]
        if key is None or key == 0:
            continue
        item = {
            "filepath": r[1],
            "original_filename": r[2],
            "aggregate_score": r[4],
            "blur_score": r[5],
            "exposure_score": r[6],
            "face_score": r[7],
            "composition_score": r[8],
            "date_day": r[9],
        }
        groups_map[key].append(
            {
                "id": r[0],
                "original_filename": r[2],
                **build_similar_entry(item, thumbs, similarity=None),
            }
        )

    result = []
    for photos in groups_map.values():
        if len(photos) < 2:
            continue
        scores = [p["aggregate_score"] for p in photos]
        best_fp = max(photos, key=lambda p: p["aggregate_score"])["filepath"]
        result.append(
            {
                "photos": sorted(photos, key=lambda p: -p["aggregate_score"]),
                "best_filepath": best_fp,
                "score_diff": max(scores) - min(scores),
            }
        )

    result.sort(key=lambda g: -g["score_diff"])
    page = result if limit is None else result[offset : offset + limit]
    return jsonify({"groups": page, **page_fields(page, len(result), limit, offset)}), 200


# --- Delete routes ---


@bp.post("/api/v1/photos/enhance")
@requires_local_app
def api_enhance_photos() -> tuple[Response, int]:
    """Auto-enhance photos and store edit params."""
    from bpp.scoring.enhance import auto_enhance

    ctx = get_ctx()
    conn = ctx.get_conn()
    data = request.get_json(silent=True) or {}
    filepaths = data.get("filepaths", [])
    if not filepaths:
        raise ValidationError("filepaths required", field="filepaths")

    enhanced = 0
    errors: dict[str, str] = {}
    params_map: dict[str, dict] = {}
    id_map = get_photo_id_map_by_paths(conn, filepaths)
    sha256_map = get_sha256_map(conn, filepaths)
    for fp in filepaths:
        photo_id = id_map.get(fp)
        if not photo_id:
            log.warning("Enhance: skipping unknown photo %s", fp)
            continue
        try:
            params = auto_enhance(fp)
        except Exception:
            # don't surface exception text. PIL errors include
            # the absolute file path; serializing that into the API
            # response leaks the owner's library layout to any client
            # holding a valid token (including paired LAN devices).
            # Detail is logged server-side (owner-only) for diagnosis.
            log.error("Enhance failed for %s", fp, exc_info=True)
            errors[fp] = "Enhance failed"
            continue
        params["auto_enhanced"] = True
        save_photo_edits(conn, photo_id, params)
        params_map[fp] = params
        # Invalidate cached full photo and thumbnail
        if ctx.thumbs:
            h = ctx.thumbs.get_hash(fp)
            invalidate_photo_cache(ctx, h, content_hash=sha256_map.get(fp))
        enhanced += 1

    ctx.invalidate_enhanced_cache()
    result: dict = {"enhanced": enhanced, "params": params_map}
    if errors:
        result["errors"] = errors
    return jsonify(result), 200


def _refresh_edit_albums(conn: Any) -> None:
    """The albums that follow a photo's edits (the edit domain)."""
    from bpp.db.smart_albums import get_affected_album_types, refresh_smart_albums

    try:
        refresh_smart_albums(conn, kinds=get_affected_album_types("edit"))
    except Exception:
        log.warning("smart album refresh after an edit failed", exc_info=True)


@bp.post("/api/v1/photos/reset-edits")
@requires_local_app
def api_reset_edits() -> tuple[Response, int]:
    """Remove all edits for specified photos."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    data = request.get_json(silent=True) or {}
    filepaths = data.get("filepaths", [])
    if not filepaths:
        raise ValidationError("filepaths required", field="filepaths")

    reset = 0
    id_map = get_photo_id_map_by_paths(conn, filepaths)
    sha256_map = get_sha256_map(conn, filepaths)
    for fp in filepaths:
        photo_id = id_map.get(fp)
        if not photo_id:
            log.warning("Reset-edits: skipping unknown photo %s", fp)
            continue
        reset += reset_photo_edits(conn, photo_id)
        if ctx.thumbs:
            h = ctx.thumbs.get_hash(fp)
            invalidate_photo_cache(ctx, h, content_hash=sha256_map.get(fp))

    ctx.invalidate_enhanced_cache()
    _refresh_edit_albums(conn)
    return jsonify({"reset": reset}), 200


def _absolute_library_path(ctx: Any, filepath: str) -> str:
    """A paired device sees device-safe paths (see ``bpp/web/lan_redaction.py``);
    resolve those so its read-only lookups still find the row."""
    from bpp.web.lan_redaction import resolve_device_path

    return resolve_device_path(filepath, ctx.library_path)


@bp.get("/api/v1/photos/enhance-preview")
def api_enhance_preview() -> tuple[Response, int]:
    """Return auto-enhance parameters without saving (for per-section AUTO)."""
    from bpp.scoring.enhance import auto_enhance

    ctx = get_ctx()
    conn = ctx.get_conn()
    filepath = _absolute_library_path(ctx, request.args.get("filepath", ""))
    if not filepath:
        raise ValidationError("filepath required", field="filepath")
    if not get_photo_by_path(conn, filepath):
        raise NotFoundError("photo not found", filepath=filepath)
    try:
        params = auto_enhance(filepath)
    except Exception as e:
        # diagnostic_message carries the original exception text to
        # server.log (via the handler's exc_info); user_message stays
        # generic so the wire payload doesn't leak PIL / opencv internals.
        raise BppError(
            "Enhance preview failed",
            user_message="Enhance preview failed",
            diagnostic_message=f"enhance preview error for {filepath}: {e!s}",
            filepath=filepath,
        ) from e
    return jsonify({"params": params}), 200


@bp.get("/api/v1/photos/edits")
def api_get_edits() -> tuple[Response, int]:
    """Get current edits for a photo."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    filepath = _absolute_library_path(ctx, request.args.get("filepath", ""))
    if not filepath:
        raise ValidationError("filepath required", field="filepath")

    photo = get_photo_by_path(conn, filepath)
    if not photo:
        return jsonify({"edits": None}), 200

    edits = get_photo_edits(conn, photo["id"])
    return jsonify({"edits": edits}), 200


_VALID_ROTATIONS = {0, 90, 180, 270}


@bp.post("/api/v1/photos/save-edits")
@requires_local_app
def api_save_edits() -> tuple[Response, int]:
    """Save manual edit parameters for a single photo."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    data = request.get_json(silent=True) or {}
    filepath = data.get("filepath", "")
    edits = data.get("edits", {})

    if not filepath:
        raise ValidationError("filepath required", field="filepath")

    # Validate rotation
    rotation = edits.get("rotation", 0)
    if rotation not in _VALID_ROTATIONS:
        raise ValidationError(
            f"rotation must be one of {sorted(_VALID_ROTATIONS)}",
            field="rotation",
            allowed=sorted(_VALID_ROTATIONS),
        )

    from bpp.db.edits import valid_redeye_points

    points = edits.get("redeye_points")
    if points is not None and valid_redeye_points(points) != points:
        raise ValidationError(
            "redeye_points must be a list of {x, y, radius?} with finite numbers",
            field="redeye_points",
        )

    photo = get_photo_by_path(conn, filepath)
    if not photo:
        raise NotFoundError("Photo not found", filepath=filepath)

    save_photo_edits(conn, photo["id"], edits)
    ctx.invalidate_enhanced_cache()
    _refresh_edit_albums(conn)

    # Invalidate cache
    if ctx.thumbs:
        from bpp.constants import HASH_PREFIX_LEN

        h = ctx.thumbs.get_hash(filepath)
        sha = photo.get("sha256") or ""
        c_hash = sha[:HASH_PREFIX_LEN] if sha else None
        invalidate_photo_cache(ctx, h, content_hash=c_hash)

    return jsonify({"status": "ok"}), 200


# --- Batch rename ---


@bp.post("/api/v1/batch/rename/preview")
@requires_local_app
def api_batch_rename_preview() -> tuple[Response, int]:
    """Preview batch rename results without applying."""
    data = request.get_json(silent=True) or {}
    pattern = data.get("pattern", "").strip()
    if not pattern:
        raise ValidationError("pattern is required", field="pattern")
    if len(pattern) > 1000:
        raise ValidationError(
            "pattern too long (max 1000 chars)",
            field="pattern",
            max_length=1000,
            actual_length=len(pattern),
        )

    ctx = get_ctx()
    conn = ctx.get_conn()
    photo_ids = data.get("photo_ids", [])

    if photo_ids:
        placeholders = ",".join("?" * len(photo_ids))
        rows = conn.execute(
            f"SELECT {PHOTO_COLS_SLIM} FROM photos WHERE id IN ({placeholders})",
            photo_ids,
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT {PHOTO_COLS_SLIM} FROM photos WHERE {active_photo_sql()} LIMIT 50"
        ).fetchall()

    photos = [dict(r) for r in rows]
    mapping = build_rename_map(photos, pattern)
    return jsonify({"mapping": mapping}), 200


@bp.post("/api/v1/batch/rename/apply")
@requires_local_app
def api_batch_rename_apply() -> tuple[Response, int]:
    """Apply a previewed batch-rename ``mapping`` of (old_path,
    new_filename) pairs. Disk renames are journaled so a crash mid-
    operation can be reverted on next startup. Returns a per-photo
    result list.

    LOCAL_APP-only — physical disk renames of owner files.
    A LAN device must not rename photo files on the host."""
    data = request.get_json(silent=True) or {}
    mapping = data.get("mapping", [])

    ctx = get_ctx()
    conn = ctx.get_conn()
    log.info("Batch rename apply: %d photo(s)", len(mapping))
    results = apply_rename(conn, mapping, library_path=ctx.state.get("library_path"))
    failed = sum(1 for r in results if not r.get("success"))
    if failed:
        log.warning("Batch rename: %d/%d failed", failed, len(results))
    return jsonify({"results": results}), 200


@bp.post("/api/v1/photos/<int:photo_id>/date")
@requires_local_app
def api_photo_date(photo_id: int) -> tuple[Response, int]:
    """Override the stored capture date for a single photo and
    invalidate the analysis cache so timeline / On This Day views
    pick up the change. Returns the new ``date``, ``date_day``, and
    ``date_month`` strings."""
    data = request.get_json(silent=True) or {}
    new_date = data.get("date", "").strip()
    if not new_date:
        raise ValidationError("Missing date", field="date")

    ctx = get_ctx()
    conn = ctx.get_conn()
    try:
        update_photo_date(conn, photo_id, new_date)
    except ValueError as e:
        raise ValidationError(
            "Invalid date format",
            user_message="Invalid date format",
            diagnostic_message=f"invalid date for photo {photo_id}: {new_date!r} ({e!s})",
            field="date",
            photo_id=photo_id,
        ) from e

    # Reload analysis so UI picks up the new date
    ctx.invalidate_analysis()

    return jsonify({"date": new_date, "date_day": new_date[:10], "date_month": new_date[:7]}), 200


# register_permanent_delete_recovery moved to bp_photos_lifecycle.
# Re-exported here because WebAppState.startup imports it from
# bpp.web.bp_photos_manage. Keep callers stable across the v0.1 split.
from bpp.web.bp_photos_lifecycle import (  # noqa: E402, F401
    register_permanent_delete_recovery,
)
