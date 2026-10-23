"""Helpers the media, face-crop, pet-crop and inpaint routes share: where
a camera RAW's full-size conversion is cached, and the errors a crop or
RAW preview failure turns into for the viewer."""

from __future__ import annotations

import os
from typing import Any

from bpp.constants import PHOTO_CACHE_SUFFIX_FULL
from bpp.errors import BppError


def raw_full_jpeg_path(ctx: Any, path_hash: str) -> str:
    """Cache path of a camera RAW's full-size JPEG conversion: made by
    the viewer or the first face / pet crop, reused by the others."""
    cache_dir = ctx.thumbs.cache_dir if ctx.thumbs else ctx.ensure_workdir()
    return os.path.join(cache_dir, f"{path_hash}{PHOTO_CACHE_SUFFIX_FULL}.jpg")


def crop_failure_error(path_hash: str, diagnostic: str, **context: Any) -> BppError:
    """A face / pet crop couldn't be made from a readable photo (the box
    fell outside it, or the image couldn't be decoded): what happened
    and what to do, for the toast. A camera RAW that couldn't be read
    raises ``box_crop.RawUnreadable`` instead; see :func:`raw_preview_error`."""
    from bpp.utils.raw import CHECK_FILE_RECOVERY

    return BppError(
        "Failed to generate crop",
        user_message="the photo couldn't be read for the crop",
        diagnostic_message=diagnostic,
        path_hash=path_hash,
        recovery=CHECK_FILE_RECOVERY,
        **context,
    )


def raw_preview_error(path_hash: str, filepath: str = "", **context: Any) -> BppError:
    """The RAW-preview failure as an error the viewer can explain: the
    reason as the message, the fix as ``context.recovery`` (the viewer
    toasts both; see ``explainPhotoFailure`` in api-client.mjs)."""
    from bpp.utils.raw import preview_failure

    reason, recovery = preview_failure()
    return BppError(
        "Cannot convert RAW image",
        user_message=reason,
        diagnostic_message=f"RAW conversion failed for {filepath}",
        path_hash=path_hash,
        recovery=recovery,
        **context,
    )
