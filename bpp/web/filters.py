"""Shared filter and sort helpers for photo lists."""

from __future__ import annotations

from typing import Any

from bpp.media_types import MediaKind, media_kind_from_dict

# UI media-filter strings → MediaKind (or None for "all"). Adding a
# new media kind only requires extending this map plus MediaKind itself
# — no other branch elsewhere.
_FILTER_TO_KIND = {
    "videos": MediaKind.VIDEO,
    "raw": MediaKind.RAW,
    "photos": MediaKind.PHOTO,
}


def apply_media_filter(items: list[dict[str, Any]], media_type: str) -> list[dict[str, Any]]:
    """Filter items by media type.

    media_type: 'all', 'photos', 'videos', 'raw'
    """
    target = _FILTER_TO_KIND.get(media_type)
    if target is None:
        return items
    return [p for p in items if media_kind_from_dict(p) is target]


# Sort dispatch table. Adding a new sort key = one entry below. Each
# value is (key_fn, reverse). key_fn returns the sort key for an item;
# reverse flips ascending → descending.
_SORT_KEYS: dict[str, tuple[Any, bool]] = {
    "size-desc": (lambda p: p.get("file_size") or 0, True),
    "size-asc": (lambda p: p.get("file_size") or 0, False),
    "faces-desc": (lambda p: p.get("face_count") or 0, True),
    "score-desc": (lambda p: p.get("aggregate_score") or 0, True),
    "score-asc": (lambda p: p.get("aggregate_score") or 0, False),
    "date-asc": (lambda p: p.get("date") or "", False),
    "date-desc": (lambda p: p.get("date") or "", True),
    "name": (lambda p: p.get("filename") or p.get("filepath", ""), False),
}


def apply_sort(items: list[dict[str, Any]], sort_key: str) -> list[dict[str, Any]]:
    """Sort items by the given key. Returns a new sorted list.

    Supported keys: date-asc, date-desc, score-asc, score-desc, name,
    size-asc, size-desc, faces-desc. Unknown keys leave the order
    unchanged (caller-friendly default).
    """
    result = list(items)
    spec = _SORT_KEYS.get(sort_key)
    if spec is not None:
        key_fn, reverse = spec
        result.sort(key=key_fn, reverse=reverse)
    return result
