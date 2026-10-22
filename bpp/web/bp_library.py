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
from bpp.errors import BppError, ConflictError, NotFoundError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.bp_install import no_install_during
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

    # Only a library bpp knows (registered, or the one open now) can be
    # renamed: this moves a folder on disk, so an arbitrary path from the
    # request body must not reach os.rename.
    ctx = get_ctx()
    known = {lib["path"] for lib in list_libraries()}
    if path not in known and path != ctx.library_path:
        raise NotFoundError("Not a known library", path=path)
    if rename_folder and not os.path.isdir(path):
        # Renaming only the registry entry while the folder keeps its
        # name would split the two the next time the volume is mounted.
        raise NotFoundError(
            "The library folder isn't reachable (unmounted or moved); "
            "reconnect it before renaming the folder",
            path=path,
        )
    # Ensure the library is in the registry even if it was started via
    # `bpp serve --library` without going through the picker/switch flow.
    add_library(path)

    if rename_folder:
        new_path = os.path.join(os.path.dirname(path), name)
        ctx = get_ctx()
        moved = False

        def _move() -> None:
            nonlocal moved
            os.rename(path, new_path)
            moved = True

        reopen_error: Exception | None = None
        if ctx.library_path == path and any(
            getattr(w, "is_alive", False) for w in ctx.workers.values()
        ):
            raise ConflictError(
                "Finish or cancel the running import or analysis before renaming the library"
            )
        try:
            if ctx.library_path == path:
                # The open library: drain, move, rewrite the photo paths,
                # start up again (bpp/web/state_lifecycle.py). Reopening
                # starts recovery work that loads packages: not while pip
                # is rewriting them.
                from bpp.web.state_lifecycle import rename_open_library

                with no_install_during("the library to finish reopening"):
                    rename_open_library(ctx, new_path, _move)
            else:
                _move()
                # The folder moved; the rows inside its database still
                # name the old folder. Rewrite them and rename the cached
                # derivatives (hashed by path) so the library opens whole.
                from bpp.db.library import get_library_dirs
                from bpp.web.state_lifecycle import (
                    RenameIncompleteError,
                    _move_cache_files,
                    _rewrite_photo_paths,
                )

                new_dirs = get_library_dirs(new_path)
                try:
                    pairs = _rewrite_photo_paths(new_dirs["data"], path, new_path)
                    _move_cache_files(new_dirs, pairs)
                except Exception as e:  # sqlite "database is locked", a cache OSError
                    raise RenameIncompleteError(
                        "The folder was renamed, but its photo paths couldn't be updated "
                        "(is that library open in another app?). Close it, rename the "
                        "folder back, and try again."
                    ) from e
        except FileExistsError as e:
            raise ValidationError(
                "A folder with that name already exists",
                field="name",
                new_path=new_path,
            ) from e
        except OSError as e:
            if moved:
                reopen_error = e
            else:
                raise ValidationError(
                    "Cannot rename folder",
                    user_message="Cannot rename folder",
                    diagnostic_message=f"rename({path!r}, {new_path!r}) failed: {e!s}",
                    path=path,
                    new_path=new_path,
                ) from e
        except Exception as e:
            if not moved:
                raise
            reopen_error = e
        # The folder is renamed on disk: the registry must say so even if
        # reopening the library failed.
        rename_library(path, name)
        rename_library_path(path, new_path, name)
        if get_active_library() == path:
            set_active_library(new_path)
        if reopen_error is not None:
            from bpp.web.state_lifecycle import RenameIncompleteError

            log.error("Library renamed on disk but could not be reopened", exc_info=reopen_error)
            raise BppError(
                str(reopen_error)
                if isinstance(reopen_error, RenameIncompleteError)
                else (
                    "The folder was renamed, but the library couldn't be reopened; restart the app"
                ),
                diagnostic_message=(
                    f"reopen after rename({path!r}, {new_path!r}) failed: {reopen_error!s}"
                ),
                new_path=new_path,
            ) from reopen_error
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
        # Opening a library starts recovery work (face extraction) that
        # loads packages: not while pip is rewriting them.
        with no_install_during("the library to finish opening"):
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
