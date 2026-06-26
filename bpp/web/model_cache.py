"""P4 — WebAppState collaborator: derived-state caches.

Pre-P4, ``WebAppState`` held four conceptually unrelated caches as
bare attributes:

* ``self._face_cluster_map`` + ``self._face_cluster_map_lock`` —
  filepath → cluster_id list, invalidated after face clustering.
* ``self._edited_ids`` / ``self._auto_enhanced_ids`` — sets of photo
  ids that have edits applied or auto-enhance applied. Lazy-loaded
  together inside ``build_photo_dict``, invalidated when an edit lands.
* ``self.clip_cache`` — embeddings dict + ready flag for semantic
  search.

Three independent caches with independent invalidation triggers. Their
"liveness" only matters across reads of WebAppState — nothing else in
the codebase needs to see them. They make a natural collaborator.

This module defines:

* :class:`FaceClusterMapCache` — owns the lock, the dict, and the
  invalidation call.
* :class:`EnhancedIdsCache` — owns both id sets and their joint
  invalidation. The two sets are always loaded together (same DB
  conn, two queries) and always invalidated together.
* :class:`ModelCache` — facade holding both caches plus the
  ``clip_cache`` dict (a minimal scope for now; the CLIP loader
  itself lives in ``state_init`` and stays there).

``WebAppState`` constructs one :class:`ModelCache` at start. Existing
property accesses (``ctx.clip_cache``, ``ctx._edited_ids``, etc.)
delegate to the cache via @property — a deprecation-window measure
so the ~3,800 access sites the audit counted can migrate gradually.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from typing import Any

from bpp.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class FaceClusterMapCache:
    """Cached filepath → [cluster_ids] map.

    Loaded on first :meth:`get` after a fresh ``WebAppState`` or after
    :meth:`invalidate`. Backing query lives in
    ``bpp.db.face_queries.load_face_cluster_map``.

    Locked so a concurrent ``invalidate()`` (fired from the face
    worker's post-cluster phase) can't null the field between a
    reader's ``is None`` check and its use of the cached dict.
    """

    _map: dict[str, list[int]] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, conn: sqlite3.Connection) -> dict[str, list[int]]:
        with self._lock:
            if self._map is not None:
                return self._map
        # Load outside the lock — the query is heavy enough that
        # holding the lock across it would block the face worker's
        # invalidate call. Worst case under contention: two readers
        # both compute the map and the second one wins the assignment;
        # the map content is deterministic so the race is benign.
        from bpp.db.face_queries import load_face_cluster_map

        result = load_face_cluster_map(conn)
        with self._lock:
            self._map = result
        return result

    def invalidate(self) -> None:
        with self._lock:
            self._map = None


@dataclass
class EnhancedIdsCache:
    """Cached ``(edited_ids, auto_enhanced_ids)`` sets.

    The two are queried + invalidated together because every
    ``build_photo_dict`` call needs both. Keeping them as one cache
    means one round trip + one lock acquisition per cache miss.
    """

    edited: set[int] | None = None
    auto_enhanced: set[int] | None = None

    def both_loaded(self) -> bool:
        return self.edited is not None and self.auto_enhanced is not None

    def load(self, conn: sqlite3.Connection) -> None:
        """Populate both sets from DB. Caller must hold the WebAppState
        lock during load + read so a concurrent ``invalidate`` doesn't
        null the field between the check and the membership test in
        ``build_photo_dict``."""
        from bpp.db.edits import get_auto_enhanced_photo_ids, get_edited_photo_ids

        self.edited = get_edited_photo_ids(conn)
        self.auto_enhanced = get_auto_enhanced_photo_ids(conn)

    def invalidate(self) -> None:
        self.edited = None
        self.auto_enhanced = None


@dataclass
class ModelCache:
    """Bundle of derived-state caches owned by one ``WebAppState``.

    Holds independent caches so callers can request a specific one
    without coupling to the others. Each sub-cache exposes its own
    ``invalidate()`` so triggers stay narrow — invalidating after a
    photo edit doesn't drop the face cluster map.

    ``clip_cache`` is kept as a plain dict for back-compat with the
    CLIP loader in ``bpp.web.state_init``. P4's scope is to *bundle*
    the caches into a coherent collaborator; moving the loader is a
    follow-up so the deprecation window for the bare-attr access
    stays short.
    """

    face_cluster_map: FaceClusterMapCache = field(default_factory=FaceClusterMapCache)
    enhanced_ids: EnhancedIdsCache = field(default_factory=EnhancedIdsCache)
    clip_cache: dict[str, Any] = field(default_factory=lambda: {"embeddings": {}, "ready": False})

    def invalidate_all(self) -> None:
        """Drop every cache. Called on library switch — the new
        library's caches must not start with the previous library's
        state. Currently switch_library replaces the whole WebAppState
        (and therefore the whole ModelCache) so this method is the
        belt-and-suspenders path; it exists for completeness and as
        the explicit hook for in-place library swap (P4 follow-up)."""
        self.face_cluster_map.invalidate()
        self.enhanced_ids.invalidate()
        self.clip_cache.clear()
        self.clip_cache.update({"embeddings": {}, "ready": False})
