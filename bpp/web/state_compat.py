"""Back-compat property delegates for :class:`bpp.web.state.WebAppState`.

Extracted from :mod:`bpp.web.state` as part of the 500-LOC cap split.

During P4 the canonical storage of derived caches and the worker pool
moved off ``WebAppState`` and onto collaborators (``self.caches``,
``self.workers``, ``self.analysis_store``). Every legacy access site
(blueprints, plugins, tests, the ~3,800 references the audit found
across the tree) still works because the old attributes (``ctx._workers``,
``ctx.clip_cache``, ``ctx._face_cluster_map``, ``ctx.phash_ready``, …)
remain as ``@property`` delegates that read/write the new collaborators.

This module owns those delegates so ``state.py`` itself stays under the
documented 500-LOC cap. The delegates are exposed as a mixin
:class:`_LegacyDelegateMixin` that :class:`WebAppState` inherits — every
property below references ``self.workers`` / ``self.caches`` /
``self.analysis_store``, which the concrete subclass provides during
construction.

When a delegate's call sites all migrate to the new path, drop the
delegate here; ``state.py`` does not need to change.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

from bpp.web._deprecated_attr import deprecated_attr as _deprecated_attr

if TYPE_CHECKING:
    from bpp.web.analyze_worker import AnalyzeWorker
    from bpp.web.base_worker import BackgroundWorker
    from bpp.web.clip_worker import ClipWorker
    from bpp.web.face_worker import FaceWorker
    from bpp.web.import_worker import ImportWorker


class _LegacyDelegateMixin:
    """Deprecated-but-supported property delegates for WebAppState.

    Every property here reads/writes one of the P4 collaborators
    (``self.workers``, ``self.caches``, ``self.analysis_store``). The
    concrete subclass MUST populate those attributes before any delegate
    is touched, otherwise property access raises AttributeError.
    """

    # ------------------------------------------------------------------
    # Worker pool legacy access.
    # ------------------------------------------------------------------
    @property
    @_deprecated_attr("ctx.workers (P4 WorkerPool)")
    def _workers(self) -> dict[str, BackgroundWorker]:
        """Legacy access path. New code should use ``self.workers``
        (the :class:`WorkerPool` collaborator) directly.

        Wrapped in @deprecated_attr so the first access per process
        logs a WARNING with the new path. The other P4-relocated
        attributes (caches.*, analysis_store.*) follow the same
        pattern as their call sites migrate."""
        # Returning the inner dict means assignments like
        # ``ctx._workers["analyze"] = stub`` (used in tests) still
        # mutate the same underlying storage. The migration plan
        # removes these access sites in a follow-up pass.
        return self.workers._workers  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # P4 ModelCache property delegates.
    # ------------------------------------------------------------------
    @property
    def clip_cache(self) -> dict[str, Any]:
        return self.caches.clip_cache  # type: ignore[attr-defined]

    @clip_cache.setter
    def clip_cache(self, value: dict[str, Any]) -> None:
        # Identity swap honors the state_compat module-level contract
        # ("property reads through, never copies"). The previous shape
        # did clear() + update() in place, which (a) left a window
        # where a concurrent reader saw an empty dict, and (b) caused
        # the setter to mutate a dict-shaped object the caller still
        # held a reference to. Reassignment closes both — readers that
        # hit the getter after the setter see the new dict atomically,
        # and the caller's input is treated as data, not a live alias.
        #
        # Only call site today is state_lifecycle.switch_library, which
        # already holds ``_switch_library_lock``; the new shape is safe
        # at that boundary and removes the race window for future
        # mutation sites that may not be serialized.
        self.caches.clip_cache = dict(value)  # type: ignore[attr-defined]

    @property
    def _face_cluster_map(self) -> dict[str, list[int]] | None:
        return self.caches.face_cluster_map._map  # type: ignore[attr-defined]

    @_face_cluster_map.setter
    def _face_cluster_map(self, value: dict[str, list[int]] | None) -> None:
        with self.caches.face_cluster_map._lock:  # type: ignore[attr-defined]
            self.caches.face_cluster_map._map = value  # type: ignore[attr-defined]

    @property
    def _face_cluster_map_lock(self) -> threading.Lock:
        return self.caches.face_cluster_map._lock  # type: ignore[attr-defined]

    @property
    def _edited_ids(self) -> set[int] | None:
        return self.caches.enhanced_ids.edited  # type: ignore[attr-defined]

    @_edited_ids.setter
    def _edited_ids(self, value: set[int] | None) -> None:
        self.caches.enhanced_ids.edited = value  # type: ignore[attr-defined]

    @property
    def _auto_enhanced_ids(self) -> set[int] | None:
        return self.caches.enhanced_ids.auto_enhanced  # type: ignore[attr-defined]

    @_auto_enhanced_ids.setter
    def _auto_enhanced_ids(self, value: set[int] | None) -> None:
        self.caches.enhanced_ids.auto_enhanced = value  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # P4b AnalysisStore property delegates.
    # ------------------------------------------------------------------
    @property
    def phash_ready(self) -> threading.Event:
        return self.analysis_store.phash_ready  # type: ignore[attr-defined]

    @phash_ready.setter
    def phash_ready(self, value: threading.Event) -> None:
        self.analysis_store.phash_ready = value  # type: ignore[attr-defined]

    @property
    def _phash_generation(self) -> int:
        return self.analysis_store.phash_generation  # type: ignore[attr-defined]

    @_phash_generation.setter
    def _phash_generation(self, value: int) -> None:
        self.analysis_store.phash_generation = value  # type: ignore[attr-defined]

    @property
    def _compute_thread(self) -> threading.Thread | None:
        return self.analysis_store.compute_thread  # type: ignore[attr-defined]

    @_compute_thread.setter
    def _compute_thread(self, value: threading.Thread | None) -> None:
        self.analysis_store.compute_thread = value  # type: ignore[attr-defined]

    @property
    def _warm_thread(self) -> threading.Thread | None:
        return self.analysis_store.warm_thread  # type: ignore[attr-defined]

    @_warm_thread.setter
    def _warm_thread(self, value: threading.Thread | None) -> None:
        self.analysis_store.warm_thread = value  # type: ignore[attr-defined]

    @property
    def _cancel_warm(self) -> threading.Event:
        return self.analysis_store.cancel_warm  # type: ignore[attr-defined]

    @_cancel_warm.setter
    def _cancel_warm(self, value: threading.Event) -> None:
        self.analysis_store.cancel_warm = value  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Worker convenience accessors — blueprints use these directly.
    # ------------------------------------------------------------------
    @property
    def worker(self) -> AnalyzeWorker:
        return self.workers["analyze"]  # type: ignore[return-value,attr-defined]

    @property
    def face_worker(self) -> FaceWorker:
        return self.workers["face"]  # type: ignore[return-value,attr-defined]

    @property
    def import_worker(self) -> ImportWorker:
        return self.workers["import"]  # type: ignore[return-value,attr-defined]

    @property
    def clip_worker(self) -> ClipWorker:
        return self.workers["clip"]  # type: ignore[return-value,attr-defined]

    @property
    def export_worker(self) -> Any:
        """L-S3: streaming export worker — see bpp/web/export_worker.py."""
        return self.workers["export"]  # type: ignore[return-value,attr-defined]
