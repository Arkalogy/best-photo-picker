"""Library management blueprint: list, add, remove, rename, switch libraries."""

from __future__ import annotations

import os

from flask import Blueprint, Response, jsonify, request

from bpp.db.registry import (
    add_library,
    get_active_library,
    list_libraries,
    remove_library,
    rename_library,
    rename_library_path,
    set_active_library,
)
from bpp.errors import ConflictError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("library", __name__)


@bp.get("/api/v1/libraries")
@requires_local_app
def api_libraries_list() -> tuple[Response, int]:
    """List all known libraries from the global registry plus an
    ``exists`` flag per entry and the currently-active path.

    LOCAL_APP-only — the registry is admin/config state. Listing
    every library path leaks owner filesystem layout (multiple drive
    mounts, hostname-derived defaults). LAN clients have no use for
    it."""
    libs = list_libraries()
    for lib in libs:
        lib["exists"] = os.path.isdir(lib["path"])
    active = get_active_library()
    return jsonify({"libraries": libs, "active": active}), 200


@bp.post("/api/v1/libraries")
@requires_local_app
def api_libraries_add() -> tuple[Response, int]:
    """Register a new library folder in the global registry.

    Creates the folder if it doesn't exist. Body params: ``path``
    (required) and optional display ``name``.

    LOCAL_APP-only — registering a library creates a host-side
    directory and pins it in the registry. A LAN device should
    not be able to mutate the registry."""
    data = request.get_json(silent=True) or {}
    path = data.get("path", "").strip()
    if not path:
        raise ValidationError("path is required", field="path")
    path = os.path.abspath(path)
    name = data.get("name")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        raise ValidationError(
            "Cannot create folder",
            user_message="Cannot create folder",
            diagnostic_message=f"makedirs({path!r}) failed: {e!s}",
            path=path,
        ) from e
    entry = add_library(path, name)
    return jsonify(entry), 201


@bp.delete("/api/v1/libraries")
@requires_local_app
def api_libraries_remove() -> tuple[Response, int]:
    """Forget a library entry from the global registry. The library
    folder on disk and its contents are NOT touched.

    LOCAL_APP-only — registry mutation."""
    data = request.get_json(silent=True) or {}
    path = data.get("path", "").strip()
    if not path:
        raise ValidationError("path is required", field="path")
    remove_library(path)
    return jsonify({"status": "removed"}), 200


@bp.put("/api/v1/libraries/rename")
@requires_local_app
def api_libraries_rename() -> tuple[Response, int]:
    """Rename a library's display name and optionally its folder on
    disk. When ``rename_folder`` is true the folder is moved and the
    registry/active-library pointers are updated; the running ctx is
    redirected if the renamed library is the current one.

    LOCAL_APP-only — host filesystem rename."""
    data = request.get_json(silent=True) or {}
    path = os.path.abspath(data.get("path", "").strip())
    name = data.get("name", "").strip()
    rename_folder = data.get("rename_folder", False)
    if not path or not name:
        raise ValidationError("path and name are required")
    if "/" in name or "\\" in name or ".." in name or os.sep in name:
        raise ValidationError(
            "Invalid name: must not contain path separators",
            field="name",
            value=name,
        )

    # Ensure the library is in the registry even if it was started via
    # `bpp serve --library` without going through the picker/switch flow.
    add_library(path)

    if rename_folder and os.path.isdir(path):
        new_path = os.path.join(os.path.dirname(path), name)
        try:
            os.rename(path, new_path)
        except FileExistsError as e:
            raise ValidationError(
                "A folder with that name already exists",
                field="name",
                new_path=new_path,
            ) from e
        except OSError as e:
            raise ValidationError(
                "Cannot rename folder",
                user_message="Cannot rename folder",
                diagnostic_message=f"rename({path!r}, {new_path!r}) failed: {e!s}",
                path=path,
                new_path=new_path,
            ) from e
        rename_library(path, name)
        # Update registry path
        rename_library_path(path, new_path, name)
        # Update active library pointer
        if get_active_library() == path:
            set_active_library(new_path)
        # Update server state if this is the current library.
        # workdir must point at <library>/data (the DB lives
        # there), NOT the library root. Previously this set
        # workdir = new_path, which made every subsequent DB path
        # computation wrong (DB writes attempted in the library
        # root instead of data/). Use get_library_dirs to compute
        # the canonical layout, same way switch_library does.
        ctx = get_ctx()
        if ctx.library_path == path:
            from bpp.db.library import get_library_dirs

            new_dirs = get_library_dirs(new_path)
            with ctx.lock:
                ctx.library_path = new_path
                ctx.workdir = new_dirs["data"]
                ctx.dirs = new_dirs
        return jsonify({"status": "renamed", "new_path": new_path}), 200

    rename_library(path, name)
    return jsonify({"status": "renamed"}), 200


@bp.post("/api/v1/libraries/switch")
@requires_local_app
def api_libraries_switch() -> tuple[Response, int]:
    """Switch the running server to a different library.

    Joins active workers, swaps DB connections, rebuilds caches, and
    auto-registers the new path. Returns 409 when workers fail to
    drain in time.

    LOCAL_APP-only — switches what library the active server points
    at; a LAN device must not redirect the owner's session."""
    data = request.get_json(silent=True) or {}
    path = data.get("path", "").strip()
    if not path:
        raise ValidationError("path is required", field="path")
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        raise ValidationError("Directory does not exist", path=path)
    ctx = get_ctx()
    log.info("Library switch requested: -> %s", path)
    try:
        ctx.switch_library(path)
    except RuntimeError as e:
        raise ConflictError(
            "Failed to switch library",
            user_message="Failed to switch library",
            diagnostic_message=f"switch_library({path!r}) raised: {e!s}",
            path=path,
        ) from e
    set_active_library(path)
    add_library(path)
    log.info("Library switch complete: now serving from %s", path)
    return jsonify({"status": "switched", "path": path}), 200


@bp.get("/api/v1/libraries/active")
@requires_local_app
def api_libraries_active() -> tuple[Response, int]:
    """Return the active library's ``{path, name}``. The display name
    falls back to the folder basename when the registry has no
    matching entry.

    LOCAL_APP-only — the absolute library path discloses owner
    filesystem layout. LAN clients can identify their session
    without it."""
    ctx = get_ctx()
    path = ctx.library_path
    name = os.path.basename(path)
    # Try to get name from registry
    for lib in list_libraries():
        if lib["path"] == path:
            name = lib["name"]
            break
    return jsonify({"path": path, "name": name}), 200
