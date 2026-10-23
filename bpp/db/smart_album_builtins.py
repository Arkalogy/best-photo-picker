"""The built-in smart album types, registered on SmartAlbumRegistry.

Split out of :mod:`bpp.db.smart_albums` (500-LOC cap). It is imported
by that module after the registry class is defined, so the refreshers,
getters and presentation table are read off the module as attributes.
"""

from __future__ import annotations

from typing import Any

from bpp.db import smart_albums as _sa

_BUILTIN_REGISTRATIONS: tuple[tuple[str, Any, Any], ...] = (
    ("all", None, _sa._get_all_ids),
    ("smart_time", _sa._refresh_time_albums, _sa._get_time_ids),
    ("smart_score", _sa._refresh_score_album, _sa._get_score_ids),
    ("smart_unsorted", _sa._refresh_unsorted_album, _sa._get_unsorted_ids),
    ("smart_recent", _sa._refresh_recent_album, _sa._get_recent_ids),
    ("smart_hidden", _sa._refresh_hidden_album, _sa._get_hidden_ids),
    ("smart_person", _sa._refresh_person_albums, _sa._get_person_ids),
    ("smart_pet", _sa._refresh_pet_albums, _sa._get_pet_ids),
    ("smart_group", _sa._refresh_group_albums, _sa._get_group_ids),
    ("smart_video", _sa._refresh_video_album, _sa._get_video_ids),
    ("smart_screenshot", _sa._refresh_screenshot_album, _sa._get_screenshot_ids),
    ("smart_moments", _sa._refresh_moments_album, _sa._get_moments_ids),
    ("smart_duplicates", _sa._refresh_duplicates_album, _sa._get_duplicates_ids),
    ("smart_sensitive", _sa._refresh_sensitive_album, _sa._get_sensitive_ids),
    ("smart_no_faces", _sa._refresh_no_faces_album, _sa._get_no_faces_ids),
    ("smart_document", _sa._refresh_document_album, _sa._get_document_ids),
    ("smart_deleted", None, _sa._get_deleted_ids),
    ("smart_edited", _sa._refresh_recently_edited_album, _sa._get_edited_ids),
    ("smart_enhanced", _sa._refresh_enhanced_album, _sa._get_enhanced_ids),
    ("smart_tag", _sa._refresh_tag_albums, _sa._get_tag_ids),
)
for _kind, _refresh, _get_ids in _BUILTIN_REGISTRATIONS:
    _icon, _tip, _order = _sa._BUILTIN_PRESENTATION.get(_kind, ("", "", 5))
    _sa.SmartAlbumRegistry.register(
        _kind, _refresh, _get_ids, icon=_icon, tooltip=_tip, order=_order
    )
# Hook registrations: smart_person is the only built-in with a rename hook.
_sa.SmartAlbumRegistry.register(
    "smart_person",
    _sa._refresh_person_albums,
    _sa._get_person_ids,
    on_rename=_sa._on_rename_smart_person,
    user_renameable=True,
    result_bucket="people",  # search routes person hits to the People list
    replace=True,
)
# Review 2026-06-12: user-renameable types were a hardcoded set inside
# _ensure_smart_album; expose the flag on the registry (mirrors
# undeletable) so plugin album types can opt in without a core edit.
_sa.SmartAlbumRegistry.register(
    "smart_pet", _sa._refresh_pet_albums, _sa._get_pet_ids, user_renameable=True, replace=True
)
_sa.SmartAlbumRegistry.register(
    "smart_group",
    _sa._refresh_group_albums,
    _sa._get_group_ids,
    user_renameable=True,
    replace=True,
)
# The "all" album is undeletable. Previously
# bp_albums.py hardcoded this check; expose it on the registry so a
# plugin can mark its own type undeletable.
_sa.SmartAlbumRegistry.register(
    "all", None, _sa._get_all_ids, undeletable=True, searchable=False, replace=True
)
# Internal/system albums: never surface in name search (Review 2026-06-17:
# was a hardcoded _SKIP_TYPES set in bp_search; now a registry flag so a
# plugin can hide its own album type from search).
_sa.SmartAlbumRegistry.register(
    "smart_deleted", None, _sa._get_deleted_ids, searchable=False, replace=True
)
_sa.SmartAlbumRegistry.register(
    "smart_hidden", _sa._refresh_hidden_album, _sa._get_hidden_ids, searchable=False, replace=True
)


# smart_duplicates' UI-metadata hook (the cluster-siblings sidebar) is
# registered by bpp.web.album_siblings: it shapes API responses, which
# is the web layer's job, and the DB layer never imports bpp.web.


_sa.SmartAlbumRegistry.register(
    "smart_duplicates",
    _sa._refresh_duplicates_album,
    _sa._get_duplicates_ids,
    replace=True,
)

_sa.SmartAlbumRegistry._builtin_keys = frozenset(k for k, _, _ in _BUILTIN_REGISTRATIONS)
