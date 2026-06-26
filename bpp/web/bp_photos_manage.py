"""Photos management blueprint: delete, restore, hide, edits, rename, inpaint."""

from __future__ import annotations

import os
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
from bpp.errors import BppError, NotFoundError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("photos_manage", __name__)


@bp.get("/api/v1/duplicates/groups")
def api_duplicates_groups() -> tuple[Response, int]:
    """Return near-duplicate groups for the review flow.

    Groups by dup_cluster_id (assigned by assign_near_duplicate_clusters()).
    Falls back to exact phash grouping if clustering has never run.
    Groups are sorted by score difference descending (most useful first).
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

    if not rows:
        return jsonify({"groups": [], "total": 0}), 200

    groups_map: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        key = r[group_key_idx]
        if key is None or key == 0:
            continue
        th = thumbs.get_hash(r[1]) if thumbs else ""
        groups_map[key].append(
            {
                "id": r[0],
                "filepath": r[1],
                "original_filename": r[2],
                "thumb_hash": th,
                "aggregate_score": r[4] or 0,
                "blur_score": r[5] or 0,
                "exposure_score": r[6] or 0,
                "face_score": r[7] or 0,
                "composition_score": r[8] or 0,
                "date_day": r[9] or "",
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
    return jsonify({"groups": result, "total": len(result)}), 200


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
    sha256_map = _get_sha256_map(conn, filepaths)
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
            _invalidate_photo_cache(ctx, h, content_hash=sha256_map.get(fp))
        enhanced += 1

    ctx.invalidate_enhanced_cache()
    result: dict = {"enhanced": enhanced, "params": params_map}
    if errors:
        result["errors"] = errors
    return jsonify(result), 200


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
    sha256_map = _get_sha256_map(conn, filepaths)
    for fp in filepaths:
        photo_id = id_map.get(fp)
        if not photo_id:
            log.warning("Reset-edits: skipping unknown photo %s", fp)
            continue
        reset += reset_photo_edits(conn, photo_id)
        if ctx.thumbs:
            h = ctx.thumbs.get_hash(fp)
            _invalidate_photo_cache(ctx, h, content_hash=sha256_map.get(fp))

    ctx.invalidate_enhanced_cache()
    return jsonify({"reset": reset}), 200


@bp.get("/api/v1/photos/enhance-preview")
def api_enhance_preview() -> tuple[Response, int]:
    """Return auto-enhance parameters without saving (for per-section AUTO)."""
    from bpp.scoring.enhance import auto_enhance

    ctx = get_ctx()
    conn = ctx.get_conn()
    filepath = request.args.get("filepath", "")
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
    filepath = request.args.get("filepath", "")
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

    photo = get_photo_by_path(conn, filepath)
    if not photo:
        raise NotFoundError("Photo not found", filepath=filepath)

    save_photo_edits(conn, photo["id"], edits)
    ctx.invalidate_enhanced_cache()

    # Invalidate cache
    if ctx.thumbs:
        from bpp.constants import HASH_PREFIX_LEN

        h = ctx.thumbs.get_hash(filepath)
        sha = photo.get("sha256") or ""
        c_hash = sha[:HASH_PREFIX_LEN] if sha else None
        _invalidate_photo_cache(ctx, h, content_hash=c_hash)

    return jsonify({"status": "ok"}), 200


def _get_sha256_map(conn: Any, filepaths: list[str]) -> dict[str, str]:
    """Return {filepath: truncated_sha256} for inpaint cache key lookup."""
    if not filepaths:
        return {}
    from bpp.constants import HASH_PREFIX_LEN, SQL_BATCH_SIZE

    result: dict[str, str] = {}
    for i in range(0, len(filepaths), SQL_BATCH_SIZE):
        batch = filepaths[i : i + SQL_BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        rows = conn.execute(
            f"SELECT filepath, sha256 FROM photos WHERE filepath IN ({placeholders})",
            batch,
        ).fetchall()
        for r in rows:
            if r[1]:
                result[r[0]] = r[1][:HASH_PREFIX_LEN]
    return result


def _invalidate_photo_cache(ctx: Any, path_hash: str, *, content_hash: str | None = None) -> None:
    """Remove cached full photo and thumbnail variants for a hash.

    Glob-based cleanup: matches `{path_hash}*.jpg` and `{path_hash}*.png`
    so any current OR future suffix variant is caught automatically.
    The `PHOTO_CACHE_SUFFIXES` registry in bpp.constants stays as
    documentation but is no longer load-bearing for cleanup correctness.

    Args:
        path_hash: hash of the filepath (used for all variants whose
            cache key is filepath-derived).
        content_hash: truncated SHA-256 of file content. The inpaint
            cache uses content hash because edits are content-addressed.
            When provided, both content_hash and path_hash are swept
            for backward compatibility.
    """
    if not ctx.thumbs:
        return
    import contextlib
    import glob

    ctx.thumbs.invalidate(path_hash)
    cache_dir = ctx.thumbs.cache_dir

    hashes = {path_hash}
    if content_hash:
        hashes.add(content_hash)
    for h in hashes:
        # Glob matches the base thumb ({h}.jpg) and every suffix variant
        # ({h}_full.jpg, {h}_edited.jpg, {h}_inpainted.png, …).
        for ext in (".jpg", ".png"):
            for cached in glob.glob(os.path.join(cache_dir, f"{h}*{ext}")):
                with contextlib.suppress(OSError):
                    os.remove(cached)


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
