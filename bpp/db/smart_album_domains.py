"""Album-type string constants + the mutation-domain → album-types map.

Split out of smart_albums.py (2026-06-17) to keep that file under the
500-LOC cap. This is the "which album types does a given mutation
invalidate" concern — distinct from the SmartAlbumRegistry (which owns
refresh/get-ids dispatch). Re-exported from smart_albums.py so existing
`from bpp.db.smart_albums import get_affected_album_types, ...` callers
are unchanged.
"""

from __future__ import annotations

# ── Album type string constants ──────────────────────────────────────
# Use these instead of bare string literals so a rename or new type
# is a one-line change in one file rather than a grep-and-replace.

ALBUM_ALL = "all"
ALBUM_PERSON = "smart_person"
ALBUM_PET = "smart_pet"
ALBUM_GROUP = "smart_group"
ALBUM_UNSORTED = "smart_unsorted"
ALBUM_NO_FACES = "smart_no_faces"
ALBUM_TIME = "smart_time"
ALBUM_SCORE = "smart_score"
ALBUM_DUPLICATES = "smart_duplicates"
ALBUM_MOMENTS = "smart_moments"
ALBUM_VIDEO = "smart_video"
ALBUM_SCREENSHOT = "smart_screenshot"
ALBUM_DOCUMENT = "smart_document"
ALBUM_DELETED = "smart_deleted"
ALBUM_HIDDEN = "smart_hidden"
ALBUM_RECENT = "smart_recent"
ALBUM_EDITED = "smart_edited"

# Mutation-domain → affected album types mapping.
# Callers use get_affected_album_types(domain) instead of hard-coding
# tuples at each refresh_smart_albums() call site.
_DOMAIN_ALBUM_MAP: dict[str, tuple[str, ...]] = {
    "face_extract": (ALBUM_PERSON,),
    "face_cluster": (ALBUM_PERSON, ALBUM_UNSORTED, ALBUM_GROUP, ALBUM_NO_FACES),
    "face_tag": (ALBUM_PERSON, ALBUM_UNSORTED, ALBUM_GROUP),
    "pet_detect": (ALBUM_PET,),
    "import": (ALBUM_PET,),
    "dedup": (ALBUM_DUPLICATES, ALBUM_MOMENTS),
    "edit": (ALBUM_EDITED,),
}


def get_affected_album_types(mutation_domain: str) -> tuple[str, ...]:
    """Return album types invalidated by a mutation in the given domain.

    Used by callers that need to refresh_smart_albums() after a mutation,
    so the set of affected types lives in one place instead of being
    duplicated at each call site.
    """
    return _DOMAIN_ALBUM_MAP.get(mutation_domain, ())


def register_album_domain(
    mutation_domain: str,
    album_types: tuple[str, ...],
    *,
    extend: bool = False,
) -> None:
    """T3 — open the domain → album_types mapping to plugins.

    A plugin that introduces a new smart-album type can call this so
    its album refreshes alongside the built-ins when the relevant
    domain mutates. Example for a plugin that adds ``smart_my_kind``
    and wants it refreshed on every face_cluster mutation::

        register_album_domain(
            "face_cluster",
            ("smart_my_kind",),
            extend=True,
        )

    Args:
        mutation_domain: The domain key, e.g. ``"face_cluster"``,
            ``"pet_detect"``. New domain keys are allowed for plugins
            that own their own mutation surface.
        album_types: Tuple of album-type strings to associate with the
            domain. Each should already be registered via
            :class:`SmartAlbumRegistry` (this method doesn't validate
            because plugins commonly register the album type and the
            domain mapping in the same setup pass — checking would
            re-order the requirement awkwardly).
        extend: When True and the domain already has a mapping, append
            ``album_types`` to the existing tuple (deduped, order
            preserved). Default False replaces the existing entry
            outright — same collision-safety behavior as
            :meth:`SmartAlbumRegistry.register`'s ``replace=True``.
    """
    if extend and mutation_domain in _DOMAIN_ALBUM_MAP:
        existing = _DOMAIN_ALBUM_MAP[mutation_domain]
        # Preserve order, drop dupes.
        seen: set[str] = set(existing)
        merged = list(existing)
        for t in album_types:
            if t not in seen:
                merged.append(t)
                seen.add(t)
        _DOMAIN_ALBUM_MAP[mutation_domain] = tuple(merged)
    else:
        _DOMAIN_ALBUM_MAP[mutation_domain] = tuple(album_types)


def _reset_album_domain_for_tests() -> None:
    """Roll back to the built-in domain map. Test-only hook."""
    _DOMAIN_ALBUM_MAP.clear()
    _DOMAIN_ALBUM_MAP.update(_BUILTIN_DOMAIN_ALBUM_MAP)


# Snapshot the built-in mapping so _reset_album_domain_for_tests can
# restore it after plugin / test registrations.
_BUILTIN_DOMAIN_ALBUM_MAP: dict[str, tuple[str, ...]] = dict(_DOMAIN_ALBUM_MAP)
