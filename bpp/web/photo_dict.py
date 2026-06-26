"""Serialize a DB photo row into an API-friendly dict.

P6 adds :class:`PhotoDict` as the typed contract for the canonical
photo response shape. Endpoint handlers that build per-photo dicts
by hand can switch to :class:`PhotoDict` for static type-checking
without changing the wire format — every field is the same string
key and Python type as before.

Score fields (``aggregate_score``, ``blur_score``, etc.) come from
:func:`bpp.scoring.registry.get_api_score_fields` and are
intentionally not pinned to the TypedDict — they're plugin-extensible.
"""

from __future__ import annotations

import os
from typing import Any, TypedDict, cast

from bpp.constants import SENSITIVE_NUDITY_THRESHOLD
from bpp.scoring.registry import get_api_score_fields
from bpp.utils.json_utils import safe_json_loads
from bpp.web.thumbnails import ThumbnailCache


def is_sensitive_item(item: dict[str, Any], threshold: float = SENSITIVE_NUDITY_THRESHOLD) -> bool:
    """The single Python derivation of the sensitive-photo flag.

    User override wins (1 = sensitive, 0 = not); with no override the
    NudeNet score is compared to ``threshold`` (default
    SENSITIVE_NUDITY_THRESHOLD; runtime value comes from the
    ``sensitive_nudity_threshold`` config key). Must stay in agreement
    with ``sensitive_photo_sql(threshold)`` in bpp/constants.py at the
    SAME threshold — tests/test_sensitive.py runs the matrix through both
    layers.
    """
    override = item.get("sensitive_override")
    if override is not None:
        return bool(override)
    return (item.get("nudity_score") or 0) >= threshold


class PhotoDict(TypedDict, total=False):
    """Canonical API shape for a single photo.

    ``total=False`` — every key is technically optional because the
    builder omits some on certain paths (``selected`` only appears
    when the caller passes it; ``tags`` only when present in the
    input ``item``; ``similar_photos`` only on dedup results). The
    docstring of :func:`build_photo_dict` is the authoritative source
    on which fields appear when.

    Score fields added by the scoring registry are NOT listed here —
    they're plugin-extensible and the registry is the source of truth
    for their names + types.
    """

    id: int | None
    filepath: str
    filename: str
    date: str
    date_day: str
    date_month: str
    file_size: int
    thumb_hash: str
    cluster_size: int
    dup_cluster_id: int
    moment_cluster_id: int
    moment_size: int
    deleted_at: str | None
    hidden_at: str | None
    is_video: bool
    video_duration: float | None
    video_width: int | None
    video_height: int | None
    video_fps: float | None
    video_codec: str | None
    is_raw: bool
    is_live_photo_sidecar: bool
    live_photo_sidecar_count: int
    is_sensitive: bool
    sensitive_override: int | None
    exif: dict[str, Any] | None
    selected: bool
    tags: list[str]
    similar_photos: list[dict[str, Any]]


class PhotoMapDict(TypedDict):
    """Minimal projection used by the Map view.

    ``total=True`` (default) — every field is present on every map
    pin. Sparse fields (``gps_lat`` / ``gps_lon``) can still be
    ``None`` but the key is always set.
    """

    id: int | None
    gps_lat: float | None
    gps_lon: float | None
    thumb_hash: str
    filename: str
    date: str
    aggregate_score: float


def build_photo_dict(
    item: dict[str, Any],
    thumbs: ThumbnailCache | None,
    selected: bool | None = None,
    sensitive_threshold: float = SENSITIVE_NUDITY_THRESHOLD,
) -> PhotoDict:
    """Convert a DB photo row to a JSON-serializable dict for the API.

    Pure function — depends only on *item*, *thumbs*, and the sensitive
    *sensitive_threshold* (default SENSITIVE_NUDITY_THRESHOLD; the
    ctx.build_photo_dict wrapper injects the configured value), no shared
    state.

    P6: return type is :class:`PhotoDict` for static type-checking.
    Wire-shape is unchanged.
    """
    fp = item["filepath"]
    photo: PhotoDict = {
        "id": item.get("id"),
        "filepath": fp,
        "filename": os.path.basename(fp),
        "date": item.get("date") or "",
        "date_day": item.get("date_day") or "",
        "date_month": item.get("date_month") or "",
        "file_size": item.get("file_size") or 0,
        "thumb_hash": thumbs.get_hash(fp) if thumbs else "",
        "cluster_size": item.get("cluster_size") or 1,
        "dup_cluster_id": item.get("dup_cluster_id") or 0,
        "moment_cluster_id": item.get("moment_cluster_id") or 0,
        "moment_size": item.get("moment_size") or 1,
        "deleted_at": item.get("deleted_at"),
        "hidden_at": item.get("hidden_at"),
        "is_video": bool(item.get("is_video")),
        "video_duration": item.get("video_duration"),
        "video_width": item.get("video_width"),
        "video_height": item.get("video_height"),
        "video_fps": item.get("video_fps"),
        "video_codec": item.get("video_codec"),
        "is_raw": bool(item.get("is_raw")),
        # Live Photo sidecar fields — sidecars are excluded from active
        # queries so is_live_photo_sidecar will always be 0 here, but
        # live_photo_sidecar_count > 0 on a parent triggers the ⊙ badge.
        "is_live_photo_sidecar": bool(item.get("is_live_photo_sidecar")),
        "live_photo_sidecar_count": int(item.get("live_photo_sidecar_count") or 0),
        # Sensitive flag: derived server-side so every consumer (export
        # gate, lightbox chip, grid filters) reads ONE verdict instead of
        # re-deriving with its own threshold.
        "is_sensitive": is_sensitive_item(item, sensitive_threshold),
        "sensitive_override": item.get("sensitive_override"),
    }

    # Add all score fields from registry. The TypedDict deliberately
    # doesn't pin these keys (they're plugin-extensible), so cast to a
    # plain dict for the dynamic-key assignment then cast back.
    photo_dyn = cast(dict[str, Any], photo)
    for field_name, default in get_api_score_fields().items():
        if default is None:
            photo_dyn[field_name] = item.get(field_name)  # Optional field
        elif isinstance(default, bool):
            photo_dyn[field_name] = bool(item.get(field_name))
        else:
            photo_dyn[field_name] = item.get(field_name) or default
    # EXIF metadata (stored as JSON text in DB).
    # cache the parsed result back on the item dict. The
    # `analysis` list is held in WebAppState across requests, so
    # build_photo_dict is invoked many times against the SAME item
    # dict (every /api/v1/photos page render, every recompute, every
    # build_photo_dict in /albums/<id>/photos, ...). Without the
    # cache, each call re-parsed the JSON text — up to 5000 parses
    # per page render at the cap, multiplied across requests.
    # Parsing once and reusing pays for itself on the first refresh.
    exif_raw = item.get("exif_json")
    if exif_raw and len(exif_raw) > 100_000:
        exif_raw = None  # skip oversized blobs — corrupted EXIF, not worth parsing
    if exif_raw and not isinstance(exif_raw, str):
        photo["exif"] = exif_raw
    elif "_exif_parsed" in item:
        photo["exif"] = item["_exif_parsed"]
    else:
        parsed = safe_json_loads(exif_raw, context="exif")
        # Stash the parsed result for next time. Not part of the
        # response shape — keys with leading underscore are
        # filtered out by build_photo_dict callers if needed
        # (in practice nothing iterates `item` keys after this).
        item["_exif_parsed"] = parsed
        photo["exif"] = parsed
    if selected is not None:
        photo["selected"] = selected
    if "tags" in item:
        photo["tags"] = item["tags"]
    if "similar_photos" in item:
        photo["similar_photos"] = [
            {
                "filepath": s["filepath"],
                "thumb_hash": thumbs.get_hash(s["filepath"]) if thumbs else "",
                "similarity": s["similarity"],
                "aggregate_score": s.get("aggregate_score", 0),
                "blur_score": s.get("blur_score", 0),
                "exposure_score": s.get("exposure_score", 0),
                "face_score": s.get("face_score", 0),
                "composition_score": s.get("composition_score", 0),
                "date_day": s.get("date_day", ""),
                "filename": s.get("filename") or os.path.basename(s["filepath"]),
            }
            for s in item["similar_photos"]
        ]
    return photo


def build_photo_dict_map(
    item: dict[str, Any],
    thumbs: ThumbnailCache | None,
) -> PhotoMapDict:
    """Minimal photo-dict projection for the Map view.

    The map renders thousands of pins per page; sending the full
    `build_photo_dict()` payload (EXIF, scores, video metadata, etc.)
    bloats the JSON 5-10x with fields the map UI doesn't use. This
    helper returns just the fields the marker cluster + popup needs.

    Centralizing the shape here means a future field addition (e.g.
    new score) lands in one place and stays in sync with the canonical
    `build_photo_dict`."""
    fp = item["filepath"]
    return {
        "id": item.get("id"),
        "gps_lat": item.get("gps_lat"),
        "gps_lon": item.get("gps_lon"),
        "thumb_hash": (thumbs.get_hash(fp) if thumbs else "") or "",
        "filename": os.path.basename(fp),
        "date": item.get("date") or "",
        "aggregate_score": item.get("aggregate_score") or 0,
    }
