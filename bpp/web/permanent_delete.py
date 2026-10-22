"""The disk half of a permanent delete: remove the files and every
cached variant. Shared by the delete endpoint, its journal replay and
the 30-day auto purge, so it lives outside the endpoint file.
"""

from __future__ import annotations

import os

from bpp.utils.logging import get_logger

log = get_logger(__name__)


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
    # Recovery may run with ctx.thumbs == None (early startup); the
    # cache cleanup is skipped then.
    thumbs = getattr(ctx, "thumbs", None)
    dirs = getattr(ctx, "dirs", None) or {}
    path_hashes: set[str] = set()
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
        if thumbs is not None:
            path_hash = thumbs.get_hash(fp)
            path_hashes.add(path_hash)
            thumbs.remove_for_hash(
                path_hash,
                content_hash=sha256_map.get(fp) if isinstance(sha256_map, dict) else None,
            )
    if thumbs is not None and path_hashes:
        # The crop folders once per purge, not two globs per photo
        # (a purge of thousands used to block startup for minutes).
        thumbs.remove_crops_for_hashes(dirs.get("face_crops"), dirs.get("pet_crops"), path_hashes)
    return removed


def register_permanent_delete_recovery(ctx: object, library_path: str | None = None) -> None:
    """Bind a recovery handler for the 'permanent_delete' journal kind.

    Called once during WebAppState.startup() with the live ctx so the
    handler can reach `ctx.thumbs`, `ctx.dirs`, etc. Idempotent
    registration (the journal module itself rejects different handlers
    for the same kind, so re-running this with the same ctx is safe;
    re-running with a different ctx during library switch picks up
    the new ctx via closure).

    ``library_path`` (optional, but passed by the state lifecycle):
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
