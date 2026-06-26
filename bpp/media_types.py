"""Single-source-of-truth media type discriminator.

The codebase used to bare-check `photo["is_video"]` / `photo["is_raw"]`
(or call `is_video_file(path)` / `is_raw_file(path)`) at every dispatch
site. That worked for three types but every new media type
(Live Photos, motion stills, HDR bursts, 360°, …) would need touching
the same conditional pattern across filters, scoring, output, and
endpoints.

This module is the named seam:

    from bpp.media_types import MediaKind, media_kind_from_dict

    kind = media_kind_from_dict(photo)
    if kind is MediaKind.VIDEO:
        ...

To add a new kind:
1. Append to `MediaKind`.
2. Update `media_kind_from_dict` and/or `media_kind_from_path` to
   recognize it (e.g., a new `is_live_photo` column or a new file
   signature).
3. Existing call sites that branch on `MediaKind.X` get a new
   match-case arm — no signature change.

The underlying `is_video` / `is_raw` storage columns stay; this is
just how callers consume them.
"""

from __future__ import annotations

import enum
from typing import Any


class MediaKind(enum.Enum):
    """The kind of media a photo entry represents.

    Order matches the priority used when multiple flags are set in
    pathological data: VIDEO > RAW > PHOTO. (Real data should never
    have both, but the priority makes the dispatch deterministic.)
    """

    PHOTO = "photo"
    RAW = "raw"
    VIDEO = "video"


def media_kind_from_dict(photo: dict[str, Any]) -> MediaKind:
    """Classify a photo dict (typically a DB row).

    Reads the `is_video` and `is_raw` flags. Falls back to PHOTO if
    neither is set or the dict is missing both keys.
    """
    if photo.get("is_video"):
        return MediaKind.VIDEO
    if photo.get("is_raw"):
        return MediaKind.RAW
    return MediaKind.PHOTO


def media_kind_from_path(filepath: str) -> MediaKind:
    """Classify a media file by its filesystem path.

    Uses extension-based detection from `bpp.utils.video.is_video_file`
    and `bpp.utils.raw.is_raw_file`. For paths-only contexts that don't
    have a DB row to query (e.g., import-time scanning, gallery export).
    """
    # Local imports avoid circulars: utils.video and utils.raw don't
    # need the dispatcher themselves.
    from bpp.utils.raw import is_raw_file
    from bpp.utils.video import is_video_file

    if is_video_file(filepath):
        return MediaKind.VIDEO
    if is_raw_file(filepath):
        return MediaKind.RAW
    return MediaKind.PHOTO
