"""Auto-generate and refresh smart albums based on rules."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any, ClassVar

from bpp.constants import ACTIVE_PHOTO_SQL, active_photo_sql
from bpp.utils.logging import get_logger

log = get_logger(__name__)

# Album-type constants + the mutation-domain → album-types map live in
# smart_album_domains (split out 2026-06-17 for the 500-LOC cap).
# Re-exported so existing `from bpp.db.smart_albums import ALBUM_*,
# get_affected_album_types, register_album_domain` callers are unchanged.
from bpp.db.smart_album_domains import (  # noqa: E402, F401
    _BUILTIN_DOMAIN_ALBUM_MAP,
    _DOMAIN_ALBUM_MAP,
    ALBUM_ALL,
    ALBUM_DELETED,
    ALBUM_DOCUMENT,
    ALBUM_DUPLICATES,
    ALBUM_EDITED,
    ALBUM_GROUP,
    ALBUM_HIDDEN,
    ALBUM_MOMENTS,
    ALBUM_NO_FACES,
    ALBUM_PERSON,
    ALBUM_PET,
    ALBUM_RECENT,
    ALBUM_SCORE,
    ALBUM_SCREENSHOT,
    ALBUM_TIME,
    ALBUM_UNSORTED,
    ALBUM_VIDEO,
    _reset_album_domain_for_tests,
    get_affected_album_types,
    register_album_domain,
)

_ACTIVE = ACTIVE_PHOTO_SQL

# Aliased active filters for self-join queries (e.g., duplicates)
_P1_ACTIVE = active_photo_sql("p1")
_P2_ACTIVE = active_photo_sql("p2")

# Screenshot filename patterns (used in refresh + get)
_SCREENSHOT_WHERE = (
    "original_filename LIKE 'Screenshot%' OR "
    "original_filename LIKE 'Screen Shot%' OR "
    "original_filename LIKE 'screen_%' OR "
    "original_filename LIKE 'Capture%' OR "
    "original_filename LIKE 'IMG_%_screenshot%' OR "
    "original_filename LIKE 'Simulator Screen Shot%'"
)

# Document detection constants live in bpp.db.smart_album_queries and
# are re-imported via the shim block below — that's the canonical home.
# Keeping a duplicate copy here triggers F811.


def refresh_smart_albums(
    conn: sqlite3.Connection,
    *,
    kinds: Iterable[str] | None = None,
) -> None:
    """Create or update smart albums.

    L5: when ``kinds`` is given, only refresh those album types. Use
    this from HTTP handlers + worker callers that mutated a specific
    domain (e.g., a face merge only affects ``smart_person``; no need
    to re-walk every video / screenshot / document query). When
    ``kinds`` is ``None`` (default), every registered type runs —
    same behavior as before, used by the explicit "Refresh smart
    albums" endpoint and the periodic full sweeps.

    Unknown kinds are silently ignored; a typo on a caller's side
    means nothing refreshes for that key, which is preferable to a
    runtime exception breaking the surrounding mutation.
    """
    selected = set(kinds) if kinds is not None else None
    for album_type, entry in SmartAlbumRegistry.items():
        if selected is not None and album_type not in selected:
            continue
        refresh_fn = entry[0]
        if refresh_fn is not None:
            try:
                refresh_fn(conn)
            except Exception:
                log.warning("Failed to refresh %s albums", album_type, exc_info=True)


# The refresh routines for the built-in album types + the two
# helpers used by them (_remove_smart_album_if_exists,
# _ensure_smart_album) live in bpp.db.smart_album_refreshers.
# Re-exported here so existing imports keep working.
# Person/pet/group refreshers live in their own dedicated modules.
# Imported here for the SmartAlbumRegistry built-in registration block
# below + re-exported for production callers that historically reached
# them via the smart_albums namespace.
from bpp.db.smart_album_groups import _refresh_group_albums  # noqa: E402
from bpp.db.smart_album_people import (  # noqa: E402, F401
    _is_default_person_name,
    _on_rename_smart_person,
    _refresh_person_albums,
)
from bpp.db.smart_album_pets import _refresh_pet_albums  # noqa: E402

# ── Per-type handlers for get_smart_album_photo_ids ──
# Implementations live in bpp.db.smart_album_queries.
# Re-exported here so the registrations below + any external
# imports keep working.
from bpp.db.smart_album_queries import (  # noqa: E402
    _get_all_ids,
    _get_deleted_ids,
    _get_document_ids,
    _get_duplicates_ids,
    _get_edited_ids,
    _get_group_ids,
    _get_hidden_ids,
    _get_moments_ids,
    _get_no_faces_ids,
    _get_person_ids,
    _get_pet_ids,
    _get_recent_ids,
    _get_score_ids,
    _get_screenshot_ids,
    _get_tag_ids,
    _get_time_ids,
    _get_unsorted_ids,
    _get_video_ids,
)
from bpp.db.smart_album_refreshers import (  # noqa: E402, F401
    _ensure_smart_album,
    _get_enhanced_ids,
    _refresh_document_album,
    _refresh_duplicates_album,
    _refresh_enhanced_album,
    _refresh_hidden_album,
    _refresh_moments_album,
    _refresh_no_faces_album,
    _refresh_recent_album,
    _refresh_recently_edited_album,
    _refresh_score_album,
    _refresh_screenshot_album,
    _refresh_time_albums,
    _refresh_unsorted_album,
    _refresh_video_album,
    _remove_smart_album_if_exists,
)
from bpp.db.smart_album_sensitive import (  # noqa: E402
    _get_sensitive_ids,
    _refresh_sensitive_album,
)

# Tag refresher lives in bpp.db.smart_album_tags.
from bpp.db.smart_album_tags import _refresh_tag_albums  # noqa: E402


# ---------------------------------------------------------------------------
# Unified smart album registry: album_type → (refresh_fn | None, get_ids_fn)
#
# Adding a new smart album type? Add ONE entry here. That's it.
#   - refresh_fn(conn): creates/updates the album(s) during refresh_smart_albums()
#   - get_ids_fn(conn, rule): re-evaluates the rule and returns matching photo IDs
# Types with no refresh_fn (None) are query-only (e.g. "all", "smart_deleted").
# ---------------------------------------------------------------------------
class SmartAlbumRegistry:
    """Mutable registry of smart-album types.

    Each entry is a `(refresh_fn, get_ids_fn)` tuple — `refresh_fn`
    may be None for query-only album types (`all`, `smart_deleted`).

    Optional lifecycle hooks live in `_hooks[album_type]` so existing
    code that unpacks entry tuples as `(refresh, get_ids)` doesn't
    break. Available hooks:
      - on_rename(conn, album, new_name): called after the album row
        is updated by PATCH /api/albums/<id>. Used by smart_person to
        propagate the identity label onto face_embeddings. Default None.

    Plugins can register new album types via
    `SmartAlbumRegistry.register("smart_my_kind", refresh_fn,
    get_ids_fn, on_rename=...)`. The refresh loop in
    `refresh_smart_albums()`, the rule evaluator in
    `get_smart_album_photo_ids()`, and the rename hook in the
    PATCH /api/albums/<id> route all consult the registry, so a new
    type works end-to-end with no edits to those sites.

    Tests use `_reset_for_tests()` to roll back to the built-in set.
    """

    _types: ClassVar[dict[str, tuple[Any, Any]]] = {}
    _hooks: ClassVar[dict[str, dict[str, Any]]] = {}
    _builtin_keys: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def register(
        cls,
        album_type: str,
        refresh_fn: Any,
        get_ids_fn: Any,
        *,
        on_rename: Any = None,
        undeletable: bool = False,
        user_renameable: bool = False,
        ui_metadata_fn: Any = None,
        searchable: bool = True,
        result_bucket: str = "album",
        replace: bool = False,
    ) -> None:
        """Register a smart-album type.

        Default is collision-safe: re-registering an existing type
        with a different impl raises (catches accidental shadowing).
        Pass `replace=True` to override unconditionally.

        Optional registration extras:

        * ``on_rename(conn, album, new_name)`` — fires after the album
          row is updated by PATCH /api/albums/<id>. Built-in usage:
          ``smart_person`` propagates identity labels onto
          ``face_embeddings``.
        * ``undeletable`` — when True, ``DELETE /api/albums/<id>`` for
          this album type returns 400. Built-in usage: the ``"all"``
          album. Plugins can register their own undeletable types
          without editing the deletion endpoint.
        * ``user_renameable`` — when True, the smart-album refresh
          preserves a user-set album name instead of forcing it back
          to the code-generated default. Built-in usage:
          ``smart_person`` / ``smart_pet`` / ``smart_group``. Plugins
          can declare their own renameable types without editing
          ``_ensure_smart_album``.
        * ``ui_metadata_fn(conn, ctx, photos, photos_data)`` — fires
          after the album-photos endpoint built ``photos_data`` but
          before responding. The hook may mutate ``photos_data`` in
          place to attach extra fields (e.g. cluster siblings for
          lightbox compare). Built-in usage: ``smart_duplicates``.
        """
        new_entry = (refresh_fn, get_ids_fn)
        existing = cls._types.get(album_type)
        if existing is not None and existing != new_entry and not replace:
            raise ValueError(
                f"Smart album type {album_type!r} already registered "
                "with a different implementation (pass replace=True if intentional)"
            )
        cls._types[album_type] = new_entry
        # Hooks live in a parallel dict so the entry tuple stays
        # 2-element and existing callers (test suites that unpack
        # `(refresh, get_ids)`) keep working.
        bucket = cls._hooks.setdefault(album_type, {})
        if on_rename is not None:
            bucket["on_rename"] = on_rename
        if undeletable:
            bucket["undeletable"] = True
        if user_renameable:
            bucket["user_renameable"] = True
        if ui_metadata_fn is not None:
            bucket["ui_metadata_fn"] = ui_metadata_fn
        # searchable defaults True; only store when opting OUT so the
        # accessor's default-True covers every unregistered/plain type.
        if not searchable:
            bucket["searchable"] = False
        # result_bucket defaults "album"; store any non-default so search
        # can route the hit (e.g. "people") without a hardcoded type check.
        if result_bucket != "album":
            bucket["result_bucket"] = result_bucket

    @classmethod
    def get_on_rename(cls, album_type: str) -> Any:
        """Return the `on_rename(conn, album, new_name)` callback for
        an album type, or None if the type has no rename hook."""
        return cls._hooks.get(album_type, {}).get("on_rename")

    @classmethod
    def is_undeletable(cls, album_type: str) -> bool:
        """Return True if this album type was registered with
        ``undeletable=True``. Used by the album-delete endpoint to
        decide whether to refuse the request without hardcoding the
        ``"all"`` check."""
        return bool(cls._hooks.get(album_type, {}).get("undeletable"))

    @classmethod
    def is_user_renameable(cls, album_type: str) -> bool:
        """Return True if this album type was registered with
        ``user_renameable=True``. Used by ``_ensure_smart_album`` to
        decide whether a refresh may overwrite the album's name with
        the code-generated default."""
        return bool(cls._hooks.get(album_type, {}).get("user_renameable"))

    @classmethod
    def get_ui_metadata_fn(cls, album_type: str) -> Any:
        """Return the optional UI-metadata hook for an album type, or
        None. Called from the album-photos endpoint to let registry
        entries attach type-specific extras to the response."""
        return cls._hooks.get(album_type, {}).get("ui_metadata_fn")

    @classmethod
    def is_searchable(cls, album_type: str) -> bool:
        """Return True unless the type was registered ``searchable=False``.
        Used by the search endpoint to skip internal/system albums
        (all, smart_deleted, smart_hidden) without hardcoding the set —
        a plugin can hide its own album type from search the same way."""
        return cls._hooks.get(album_type, {}).get("searchable", True)

    @classmethod
    def get_result_bucket(cls, album_type: str) -> str:
        """Search result bucket for an album type ("album" default, or
        e.g. "people"). Lets the search endpoint route a hit to a
        dedicated UI list without a hardcoded ``== "smart_person"`` check;
        a plugin can declare its own first-class bucket at registration."""
        return cls._hooks.get(album_type, {}).get("result_bucket", "album")

    @classmethod
    def get(cls, album_type: str) -> tuple[Any, Any] | None:
        return cls._types.get(album_type)

    @classmethod
    def items(cls) -> Any:
        return cls._types.items()

    @classmethod
    def keys(cls) -> Any:
        return cls._types.keys()

    @classmethod
    def _reset_for_tests(cls) -> None:
        """Roll back to the built-in set. Test-only hook."""
        cls._types = {k: v for k, v in cls._types.items() if k in cls._builtin_keys}
        cls._hooks = {k: v for k, v in cls._hooks.items() if k in cls._builtin_keys}


# Built-in registrations. Calling .register() directly here lets the
# registry's collision-safety apply to even the built-ins, which means
# if a future contributor accidentally re-registers `smart_video` with
# a different impl during early test setup, it gets caught.


_BUILTIN_REGISTRATIONS: tuple[tuple[str, Any, Any], ...] = (
    ("all", None, _get_all_ids),
    ("smart_time", _refresh_time_albums, _get_time_ids),
    ("smart_score", _refresh_score_album, _get_score_ids),
    ("smart_unsorted", _refresh_unsorted_album, _get_unsorted_ids),
    ("smart_recent", _refresh_recent_album, _get_recent_ids),
    ("smart_hidden", _refresh_hidden_album, _get_hidden_ids),
    ("smart_person", _refresh_person_albums, _get_person_ids),
    ("smart_pet", _refresh_pet_albums, _get_pet_ids),
    ("smart_group", _refresh_group_albums, _get_group_ids),
    ("smart_video", _refresh_video_album, _get_video_ids),
    ("smart_screenshot", _refresh_screenshot_album, _get_screenshot_ids),
    ("smart_moments", _refresh_moments_album, _get_moments_ids),
    ("smart_duplicates", _refresh_duplicates_album, _get_duplicates_ids),
    ("smart_sensitive", _refresh_sensitive_album, _get_sensitive_ids),
    ("smart_no_faces", _refresh_no_faces_album, _get_no_faces_ids),
    ("smart_document", _refresh_document_album, _get_document_ids),
    ("smart_deleted", None, _get_deleted_ids),
    ("smart_edited", _refresh_recently_edited_album, _get_edited_ids),
    ("smart_enhanced", _refresh_enhanced_album, _get_enhanced_ids),
    ("smart_tag", _refresh_tag_albums, _get_tag_ids),
)
for _kind, _refresh, _get_ids in _BUILTIN_REGISTRATIONS:
    SmartAlbumRegistry.register(_kind, _refresh, _get_ids)
# Hook registrations: smart_person is the only built-in with a rename hook.
SmartAlbumRegistry.register(
    "smart_person",
    _refresh_person_albums,
    _get_person_ids,
    on_rename=_on_rename_smart_person,
    user_renameable=True,
    result_bucket="people",  # search routes person hits to the People list
    replace=True,
)
# Review 2026-06-12: user-renameable types were a hardcoded set inside
# _ensure_smart_album; expose the flag on the registry (mirrors
# undeletable) so plugin album types can opt in without a core edit.
SmartAlbumRegistry.register(
    "smart_pet", _refresh_pet_albums, _get_pet_ids, user_renameable=True, replace=True
)
SmartAlbumRegistry.register(
    "smart_group",
    _refresh_group_albums,
    _get_group_ids,
    user_renameable=True,
    replace=True,
)
# L1 / review 2026-05-31: the "all" album is undeletable. Previously
# bp_albums.py hardcoded this check; expose it on the registry so a
# plugin can mark its own type undeletable.
SmartAlbumRegistry.register(
    "all", None, _get_all_ids, undeletable=True, searchable=False, replace=True
)
# Internal/system albums: never surface in name search (Review 2026-06-17:
# was a hardcoded _SKIP_TYPES set in bp_search; now a registry flag so a
# plugin can hide its own album type from search).
SmartAlbumRegistry.register("smart_deleted", None, _get_deleted_ids, searchable=False, replace=True)
SmartAlbumRegistry.register(
    "smart_hidden", _refresh_hidden_album, _get_hidden_ids, searchable=False, replace=True
)


# M6 / review 2026-05-31: smart_duplicates uses a UI-metadata hook for
# the cluster-siblings sidebar; bp_albums.py used to hardcode the
# dispatch. The hook receives (conn, ctx, photos, photos_data) and
# mutates photos_data in place.
def _smart_duplicates_ui_metadata(conn: Any, ctx: Any, photos: Any, photos_data: Any) -> None:
    from bpp.web.bp_albums import _attach_cluster_siblings_from_db

    _attach_cluster_siblings_from_db(conn, ctx.thumbs, photos, photos_data)


SmartAlbumRegistry.register(
    "smart_duplicates",
    _refresh_duplicates_album,
    _get_duplicates_ids,
    ui_metadata_fn=_smart_duplicates_ui_metadata,
    replace=True,
)

SmartAlbumRegistry._builtin_keys = frozenset(k for k, _, _ in _BUILTIN_REGISTRATIONS)


# Back-compat alias for code/tests that still reference the dict
# directly. New code should use `SmartAlbumRegistry.get(...)` /
# `.items()`. Reads against this attribute reflect the live registry
# (it's the same dict object).
_SMART_ALBUM_TYPES = SmartAlbumRegistry._types


def get_smart_album_photo_ids(conn: sqlite3.Connection, album: dict[str, Any]) -> list[int]:
    """Re-evaluate a smart album's rule and return matching photo IDs."""
    rule = album.get("rule") or {}
    # Fallback for raw DB rows passed directly (rule_json not yet parsed into rule).
    if not rule and album.get("rule_json"):
        from bpp.utils.json_utils import safe_json_loads

        rule = safe_json_loads(album["rule_json"], {}, context=f"album {album.get('id', '?')}")
    entry = SmartAlbumRegistry.get(album.get("album_type", ""))
    if entry:
        return entry[1](conn, rule)
    return []
