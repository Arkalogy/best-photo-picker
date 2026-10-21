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
