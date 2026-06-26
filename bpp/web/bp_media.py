"""Media blueprint: thumbnails, full photos, video trim."""

from __future__ import annotations

import contextlib
import os

from flask import Blueprint, Response, jsonify, request, send_file

from bpp.constants import (
    PHOTO_CACHE_SUFFIX_EDITED,
    PHOTO_CACHE_SUFFIX_EDITED_THUMB,
    PHOTO_CACHE_SUFFIX_FULL,
    PHOTO_CACHE_SUFFIX_SPRITE,
)
from bpp.errors import BppError, ForbiddenError, NotFoundError, ValidationError
from bpp.utils.logging import get_logger
from bpp.utils.raw import RAW_EXTENSIONS as _RAW_EXTENSIONS
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("media", __name__)


def _safe_send_file(path: str, **kwargs) -> Response | tuple[Response, int]:
    """send_file but turn FileNotFoundError / OSError into a 404 instead
    of a 500. The pre-flight `os.path.isfile()` checks elsewhere in this
    module still catch the common case; this catches the TOCTOU window
    where the file disappears between check and send (e.g., user deletes
    a photo from disk during a request)."""
    try:
        return send_file(path, **kwargs)
    except (FileNotFoundError, OSError) as e:
        raise NotFoundError("File missing", path=path) from e


@bp.get("/thumb/<path_hash>")
def serve_thumbnail(path_hash: str) -> Response | tuple[Response, int]:
    """Serve a JPEG thumbnail keyed by path hash.

    Fast-paths the cached edited variant when it exists and isn't
    stale; otherwise checks for stored edits and regenerates the
    edited thumbnail on demand. Falls back to the unedited cache.
    The unedited response is marked immutable + 1-year cache because
    the URL is content-addressed."""
    ctx = get_ctx()
    if ctx.thumbs is None:
        raise NotFoundError("No thumbnails available")

    filepath = ctx.thumbs.get_filepath(path_hash)
    if filepath is None or not os.path.isfile(filepath):
        raise NotFoundError("Thumbnail not found", path_hash=path_hash)

    # Fast path: if an edited thumbnail cache file exists, serve it without DB query.
    # Only query DB for edits when the cache file is missing or stale.
    cache_dir = ctx.thumbs.cache_dir
    edited_path = os.path.join(cache_dir, f"{path_hash}{PHOTO_CACHE_SUFFIX_EDITED_THUMB}.jpg")
    if os.path.exists(edited_path):
        stale = False
        with contextlib.suppress(OSError):
            stale = os.path.getmtime(filepath) > os.path.getmtime(edited_path)
        if not stale:
            return _safe_send_file(edited_path, mimetype="image/jpeg")
        # Stale — fall through to regenerate below

    edits = _get_edits_for_path(ctx, filepath)

    if edits:
        # Generate edited thumbnail
        ok = _generate_cached_image(filepath, edited_path, edits=edits, thumb_size=True)
        if not ok:
            log.warning("Edited thumbnail failed for %s, serving unedited", path_hash)
            # Fall back to unedited thumbnail
            thumb_path = ctx.thumbs.get_thumbnail(path_hash)
            if thumb_path is None:
                raise NotFoundError("Thumbnail not found", path_hash=path_hash)
            return _safe_send_file(thumb_path, mimetype="image/jpeg")
        return _safe_send_file(edited_path, mimetype="image/jpeg")

    # No edits — serve normal thumbnail
    thumb_path = ctx.thumbs.get_thumbnail(path_hash)
    if thumb_path is None:
        raise NotFoundError("Thumbnail not found", path_hash=path_hash)

    try:
        resp = send_file(thumb_path, mimetype="image/jpeg")
    except (FileNotFoundError, OSError) as e:
        raise NotFoundError("Thumbnail file missing", path_hash=path_hash) from e
    resp.cache_control.public = True
    resp.cache_control.max_age = 31536000  # 1 year — content-addressed by hash
    resp.cache_control.immutable = True
    return resp


@bp.post("/api/v1/thumbnails/clear")
@requires_local_app
def api_thumbnails_clear() -> tuple[Response, int]:
    """Wipe the on-disk thumbnail cache. Returns the number of files
    removed; subsequent thumbnail requests regenerate on demand.

    LOCAL_APP-only — destructive cache wipe + bandwidth/CPU DoS as
    every viewing client re-generates thumbnails."""
    ctx = get_ctx()
    if ctx.thumbs is None:
        raise NotFoundError("No thumbnails")
    count = ctx.thumbs.clear()
    return jsonify({"status": "cleared", "count": count}), 200


@bp.get("/photo/<path_hash>")
def serve_full_photo(path_hash: str) -> Response | tuple[Response, int]:
    """Serve a full-resolution JPEG for a photo, applying stored edits
    unless ``?raw=1`` is passed (the editor uses raw for live preview).

    Caches HEIC/JPG/PNG conversions and EXIF-rotated output to disk;
    RAW files convert through rawpy. The cached file is regenerated
    when the source is newer."""
    ctx = get_ctx()
    if ctx.thumbs is None:
        raise NotFoundError("No photos available")

    filepath = ctx.thumbs.get_filepath(path_hash)
    if filepath is None or not os.path.isfile(filepath):
        raise NotFoundError("Photo not found", path_hash=path_hash)

    # Skip edits when ?raw=1 (editor needs the original for CSS live preview)
    edits = None if request.args.get("raw") else _get_edits_for_path(ctx, filepath)

    ext = os.path.splitext(filepath)[1].lower()

    # Convert and apply EXIF rotation for all image types (cached)
    if ext in (".heic", ".jpg", ".jpeg", ".png"):
        cache_dir = ctx.thumbs.cache_dir if ctx.thumbs else ctx.ensure_workdir()
        cache_suffix = PHOTO_CACHE_SUFFIX_EDITED if edits else PHOTO_CACHE_SUFFIX_FULL
        jpeg_path = os.path.join(cache_dir, f"{path_hash}{cache_suffix}.jpg")
        needs_gen = not os.path.exists(jpeg_path)
        if not needs_gen:
            with contextlib.suppress(OSError):
                needs_gen = os.path.getmtime(filepath) > os.path.getmtime(jpeg_path)
        if needs_gen:
            ok = _generate_cached_image(filepath, jpeg_path, edits=edits)
            if not ok:
                raise BppError(
                    "Cannot convert image",
                    user_message="Cannot convert image",
                    diagnostic_message=f"cached_image generation failed for {filepath}",
                    path_hash=path_hash,
                )
        return _safe_send_file(jpeg_path, mimetype="image/jpeg")

    # RAW files: convert to JPEG via rawpy (cached)
    if ext in _RAW_EXTENSIONS:
        cache_dir = ctx.thumbs.cache_dir if ctx.thumbs else ctx.ensure_workdir()
        jpeg_path = os.path.join(cache_dir, f"{path_hash}{PHOTO_CACHE_SUFFIX_FULL}.jpg")
        needs_gen = not os.path.exists(jpeg_path)
        if not needs_gen:
            with contextlib.suppress(OSError):
                needs_gen = os.path.getmtime(filepath) > os.path.getmtime(jpeg_path)
        if needs_gen:
            from bpp.utils.raw import convert_raw_to_jpeg

            result = convert_raw_to_jpeg(filepath, jpeg_path)
            if result is None:
                raise BppError(
                    "Cannot convert RAW image",
                    user_message="Cannot convert RAW image",
                    diagnostic_message=f"rawpy conversion failed for {filepath}",
                    path_hash=path_hash,
                )
        return _safe_send_file(jpeg_path, mimetype="image/jpeg")

    return _safe_send_file(filepath, mimetype="image/jpeg")


_VIDEO_MIMETYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".m4v": "video/x-m4v",
    ".3gp": "video/3gpp",
    ".wmv": "video/x-ms-wmv",
    ".flv": "video/x-flv",
}


@bp.get("/video/<path_hash>")
def serve_video(path_hash: str) -> Response | tuple[Response, int]:
    """Serve the original video file inline with the appropriate
    mimetype derived from extension. Returns 404 when the path hash
    or file is missing."""
    ctx = get_ctx()
    if ctx.thumbs is None:
        raise NotFoundError("No media available")

    filepath = ctx.thumbs.get_filepath(path_hash)
    if filepath is None or not os.path.isfile(filepath):
        raise NotFoundError("Video not found", path_hash=path_hash)

    ext = os.path.splitext(filepath)[1].lower()
    mimetype = _VIDEO_MIMETYPES.get(ext, "video/mp4")
    return _safe_send_file(filepath, mimetype=mimetype)


@bp.get("/api/v1/video/preview/<path_hash>")
def serve_video_preview(path_hash: str) -> Response | tuple[Response, int]:
    """Serve a sprite-sheet preview image for a video."""
    from bpp.utils.video import generate_video_sprite

    ctx = get_ctx()
    if ctx.thumbs is None:
        raise NotFoundError("No media available")

    filepath = ctx.thumbs.get_filepath(path_hash)
    if filepath is None or not os.path.isfile(filepath):
        raise NotFoundError("Video not found", path_hash=path_hash)

    cache_dir = ctx.thumbs.cache_dir if ctx.thumbs else ctx.ensure_workdir()
    sprite_path = os.path.join(cache_dir, f"{path_hash}{PHOTO_CACHE_SUFFIX_SPRITE}.jpg")

    if not os.path.exists(sprite_path):
        ok = generate_video_sprite(filepath, sprite_path)
        if not ok:
            raise BppError(
                "Failed to generate preview",
                user_message="Failed to generate preview",
                diagnostic_message=f"sprite generation failed for {filepath}",
                path_hash=path_hash,
            )

    resp = send_file(sprite_path, mimetype="image/jpeg")
    resp.cache_control.public = True
    resp.cache_control.max_age = 86400
    return resp


@bp.post("/api/v1/video/trim")
@requires_local_app
def api_video_trim() -> tuple[Response, int]:
    """Trim a video file using ffmpeg.

    LOCAL_APP-only — overwrites the original video file on disk
    via ffmpeg subprocess. A LAN device must not be able to
    destructively edit owner files."""
    from bpp.utils.video import ffmpeg_available, trim_video

    data = request.get_json(silent=True) or {}
    filepath = data.get("filepath")
    start = data.get("start")
    end = data.get("end")

    if not filepath or start is None or end is None:
        raise ValidationError(
            "Missing filepath, start, or end",
            filepath=filepath,
            start=start,
            end=end,
        )

    # Validate filepath is within the library directory.
    # shared allowlist helper — same realpath-resolve +
    # is-under-allowed semantics used for open-folder / reveal /
    # export / permanent-delete.
    from bpp.utils.path_validation import build_library_allowlist, is_path_under_any

    ctx = get_ctx()
    allowed = build_library_allowlist(library_path=ctx.library_path)
    if not is_path_under_any(filepath, allowed):
        raise ForbiddenError("Invalid filepath", reason="outside_library")

    if not os.path.isfile(filepath):
        raise NotFoundError("File not found", filepath=filepath)
    if not ffmpeg_available():
        raise ValidationError("ffmpeg is not installed", reason="ffmpeg_missing")

    # Build output path: insert _trimmed before extension. The output
    # also has to live inside the library — ffmpeg writes here, so a
    # symlinked base could otherwise let it write outside the root.
    base, ext = os.path.splitext(filepath)
    out_path = f"{base}_trimmed{ext}"
    if not is_path_under_any(out_path, allowed):
        raise ForbiddenError("Invalid filepath", reason="output_outside_library")

    result = trim_video(filepath, out_path, start=float(start), end=float(end))
    if not result["ok"]:
        raise ValidationError(result["error"], reason="trim_failed")

    # Re-resolve immediately before the destructive write — between the
    # initial validation above and this replace, a symlink could be
    # swapped to point outside the library (TOCTOU). Using the resolved
    # target ensures we write to the path we already cleared.
    if not is_path_under_any(filepath, allowed):
        raise ForbiddenError("Path moved out of library", reason="toctou")
    final_target = os.path.realpath(filepath)
    os.replace(out_path, final_target)

    # Update video duration in DB
    from bpp.utils.video import extract_video_metadata

    conn = ctx.get_conn()
    vmeta = extract_video_metadata(filepath)
    if vmeta:
        conn.execute(
            "UPDATE photos SET video_duration=? WHERE filepath=?",
            (vmeta["duration"], filepath),
        )
        conn.commit()

    # Cache-invalidation audit (2026-06): the trim overwrites the
    # underlying file but the cached sprite + thumbnail are content-
    # addressed against the OLD bytes. Without the invalidate, the
    # next /video/sprite or /thumb request serves a sprite generated
    # from frames that are no longer in the trimmed clip — the user
    # sees frames that were chopped off the head/tail.
    if ctx.thumbs is not None:
        path_hash = ctx.thumbs.get_hash(filepath)
        if path_hash:
            from bpp.web.bp_photos_manage import _invalidate_photo_cache

            _invalidate_photo_cache(ctx, path_hash)

    return jsonify({"status": "ok", "duration": vmeta["duration"] if vmeta else None}), 200


# Photo edits engine moved to bpp.web.photo_edits. Re-exported here
# so the project rule that names bpp.web.bp_media as the canonical
# entry-point for cached image generation keeps holding. New callers
# can import directly from bpp.web.photo_edits.
from bpp.web.photo_edits import (  # noqa: E402, F401
    _apply_advanced,
    _apply_basic_color,
    _apply_crop,
    _apply_edits,
    _apply_orientation,
    _apply_perspective,
    _apply_redeye,
    _apply_redeye_fix,
    _generate_cached_image,
    _get_edits_for_path,
    _perspective_coefficients,
)
