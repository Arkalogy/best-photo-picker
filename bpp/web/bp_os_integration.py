"""OS file-manager integration: open-folder + reveal-file.

Extracted from bp_photos.py during the v0.1 cleanup. These two
endpoints (and their three private helpers) launch a host process
that pops native UI on the owner's desktop — Finder on macOS,
Explorer on Windows, xdg-open on Linux.

LOCAL_APP-only — a LAN client must not be able to drive the file
manager. Path is sanitized (null-byte rejection, realpath, dir/file
existence) and constrained to the library root or user home via the
shared `build_library_allowlist` / `is_path_under_any` helpers.
"""

from __future__ import annotations

import os
import subprocess
import sys

from flask import Blueprint, Response, jsonify, request

from bpp.errors import BppError, ForbiddenError, NotFoundError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("os_integration", __name__)


@bp.post("/api/v1/open-folder")
@requires_local_app
def api_open_folder() -> tuple[Response, int]:
    """Open a folder in the OS file manager (Finder, Explorer, or
    xdg-open). Path must resolve under the library root or user home;
    rejects null bytes and non-existent paths.

    LOCAL_APP-only — spawns a host process that pops native UI on
    the owner's desktop. A LAN client must not be able to drive
    the file manager."""
    params = request.get_json(silent=True) or {}
    path = params.get("path")
    if not path:
        raise ValidationError("path is required", field="path")

    if "\x00" in path:
        raise ValidationError("Invalid path", reason="null_byte")
    path = os.path.realpath(path)
    if not os.path.isdir(path):
        raise NotFoundError("Directory not found", path=path)

    # Restrict to library root or user home (defense-in-depth).
    # shared allowlist helper.
    from bpp.utils.path_validation import build_library_allowlist, is_path_under_any

    ctx = get_ctx()
    allowed = build_library_allowlist(library_path=ctx.library_path, include_home=True)
    if not is_path_under_any(path, allowed):
        raise ForbiddenError("Path outside allowed directories", reason="outside_allowlist")

    log.info("Opening folder: %s", path)
    err, code = _launch_os_handler(_open_folder_cmd(path))
    if err:
        # _launch_os_handler returns a (str, int) where int is the
        # HTTP status. Preserve the original status via instance-level
        # ``http_status`` override on the BppError so the response
        # stays equivalent to the pre-T2 behavior.
        exc = BppError(
            err,
            user_message=err,
            diagnostic_message=f"_launch_os_handler failed for folder {path!r}: {err}",
            path=path,
        )
        exc.http_status = code  # type: ignore[misc]
        raise exc
    return jsonify({"status": "ok"}), 200


@bp.post("/api/v1/reveal-file")
@requires_local_app
def api_reveal_file() -> tuple[Response, int]:
    """Reveal a photo file in the OS file manager (Finder on macOS).

    LOCAL_APP-only — same threat as open-folder."""
    params = request.get_json(silent=True) or {}
    filepath = params.get("filepath")
    if not filepath:
        raise ValidationError("filepath is required", field="filepath")

    if "\x00" in filepath:
        raise ValidationError("Invalid path", reason="null_byte")
    filepath = os.path.realpath(filepath)
    if not os.path.isfile(filepath):
        raise NotFoundError("File not found", filepath=filepath)

    # shared allowlist helper.
    from bpp.utils.path_validation import build_library_allowlist, is_path_under_any

    ctx = get_ctx()
    allowed = build_library_allowlist(library_path=ctx.library_path, include_home=True)
    if not is_path_under_any(filepath, allowed):
        raise ForbiddenError("Path outside allowed directories", reason="outside_allowlist")

    log.info("Revealing file: %s", filepath)
    err, code = _launch_os_handler(_reveal_file_cmd(filepath))
    if err:
        exc = BppError(
            err,
            user_message=err,
            diagnostic_message=f"_launch_os_handler failed for file {filepath!r}: {err}",
            filepath=filepath,
        )
        exc.http_status = code  # type: ignore[misc]
        raise exc
    return jsonify({"status": "ok"}), 200


def _open_folder_cmd(path: str) -> list[str] | None:
    """Return the OS-specific argv for opening a folder, or None if
    the current platform isn't supported (e.g., headless Linux without
    a display)."""
    if sys.platform == "darwin":
        return ["open", path]
    if sys.platform == "win32":
        return ["explorer", path]
    if sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
        return ["xdg-open", path]
    return None


def _reveal_file_cmd(filepath: str) -> list[str] | None:
    """OS-specific argv for revealing a file in its parent folder."""
    if sys.platform == "darwin":
        return ["open", "-R", filepath]
    if sys.platform == "win32":
        return ["explorer", "/select,", filepath]
    if sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
        return ["xdg-open", os.path.dirname(filepath)]
    return None


_LAUNCH_TIMEOUT_S = 30


def _launch_os_handler(argv: list[str] | None) -> tuple[str | None, int]:
    """Run a short-lived OS-launcher subprocess and translate the
    outcome into (error_message, http_status). Returns (None, 200)
    on success.

    Failure modes:
    - argv is None (unsupported platform / headless Linux) → 501
    - binary not on PATH → 502
    - subprocess exits non-zero → 502 with the exit code
    - timeout → 504

    timeout bumped from 5s to 30s. The original comment said
    "rare; OS launchers should return immediately" — but Finder /
    Explorer launches that wait on a slow disk, sleeping HD spin-up,
    SMB remount, or a busy GUI session can sit at "starting" for
    >5 seconds before completing. Users hit spurious 504s on
    `/api/v1/reveal-file` despite the underlying op succeeding.
    30s is generous enough that any real launcher finishes; a real
    hang at 30s is genuinely stuck and worth surfacing.
    """
    import shutil

    if argv is None:
        return ("OS folder/file open isn't supported on this platform.", 501)
    if shutil.which(argv[0]) is None:
        return (f"Required command not found: {argv[0]}", 502)
    try:
        result = subprocess.run(argv, check=False, timeout=_LAUNCH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return ("OS launcher timed out", 504)
    except OSError as e:
        return (f"Launch failed: {e}", 502)
    if result.returncode != 0:
        return (
            f"{argv[0]} exited with code {result.returncode}",
            502,
        )
    return (None, 200)


# --- Override/Favorite routes ---
