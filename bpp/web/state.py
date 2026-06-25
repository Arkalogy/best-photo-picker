"""Shared application state for Flask blueprints."""

from __future__ import annotations

import functools
import os
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from flask import current_app

from bpp.config import DEFAULTS, load_config
from bpp.db.connection import (
    get_db,
    init_db,
)
from bpp.db.library import get_library_dirs, get_library_path
from bpp.utils.logging import get_logger
from bpp.web.analysis_store import AnalysisStore
from bpp.web.model_cache import ModelCache
from bpp.web.state_compat import _LegacyDelegateMixin
from bpp.web.state_helpers import (  # noqa: F401
    AppState,
    clamp_k,
    clamp_weight,
    heic_available,
)
from bpp.web.thumbnails import ThumbnailCache
from bpp.web.worker_pool import WorkerPool
from bpp.web.worker_registry import WorkerRegistry  # noqa: F401

log = get_logger(__name__)


@dataclass(frozen=True)
class LibraryPaths:
    """library path container, extracted from WebAppState.

    ``frozen=True`` is the type-level guard that
    forces every write to go through ``WebAppState`` property setters
    (which keep ``ctx.state["..."]`` in sync).

    ``frozen=True`` only blocks REASSIGNING fields — it does
    NOT prevent mutating their contents. The previous shape stored
    ``dirs: dict[str, str]``, so a caller could still do
    ``ctx.paths.dirs["thumbs"] = "/elsewhere"`` and silently desync
    ``ctx.paths.workdir`` / ``ctx.state["workdir"]``. We now wrap
    dirs in ``MappingProxyType`` at construction time so per-key
    mutation raises ``TypeError`` too. Read-only access via
    ``ctx.dirs["thumbs"]`` keeps working — every mutation must go
    through ``switch_library`` (which builds a fresh dict and
    re-wraps it).

    All four fields can be empty strings during early startup
    (before `library_path` is known).
    """

    input_dir: str = ""
    workdir: str = ""
    library_path: str = ""
    dirs: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Defensive copy + wrap. Accept any Mapping in (dict, another
        # MappingProxyType, etc.) — store as MappingProxyType over a
        # fresh dict so the caller's original reference can't be
        # mutated to alias us. `object.__setattr__` is the documented
        # escape hatch for frozen-dataclass field initialization.
        if not isinstance(self.dirs, MappingProxyType):
            object.__setattr__(self, "dirs", MappingProxyType(dict(self.dirs)))


class WebAppState(_LegacyDelegateMixin):
    """Shared mutable state accessible to all blueprints via get_ctx().

    Most of the back-compat property delegates (``_workers``,
    ``clip_cache``, ``phash_ready``, worker accessors, …) live on
    :class:`_LegacyDelegateMixin` in :mod:`bpp.web.state_compat`. The
    heavy method bodies (``auto_purge``, ``build_photo_dict``,
    ``check_dedup_feedback``, ``_refresh_thumb_map``) live in
    :mod:`bpp.web.state_ops`; this class keeps thin delegate methods
    so monkey-patching ``ctx.<method>`` still works.
    """

    def __init__(
        self,
        input_dir: str | None = None,
        workdir: str | None = None,
        config_path: str | None = None,
        library_path: str | None = None,
    ) -> None:
        config = load_config(config_path)
        lib_path = library_path or get_library_path(config)

        # extensions come from the resolved config, not a
        # hardcoded list. Plugin contributors who add (say) AVIF
        # support can ship a config override rather than patching
        # this constructor + io_scan + the CLI.
        from bpp.config import parse_scan_extensions

        # structured path container. The legacy
        # `self.state["library_path"]` / `self.state["workdir"]` /
        # `self.state["input_dir"]` reads still work via the
        # AppState TypedDict (kept for back-compat) but new code
        # should use the property accessors below
        # (`ctx.library_path`, `ctx.workdir`, `ctx.input_dir`).
        self.paths = LibraryPaths(
            input_dir=os.path.abspath(input_dir) if input_dir else "",
            workdir=os.path.abspath(workdir) if workdir else "",
            library_path=os.path.abspath(lib_path),
            dirs=get_library_dirs(lib_path),
        )

        self.state: AppState = {
            "input_dir": self.paths.input_dir or None,
            "workdir": self.paths.workdir or None,
            "library_path": self.paths.library_path,
            "config": config,
            "analysis": None,
            "extensions": parse_scan_extensions(config.get("scan_extensions")),
        }

        self.dirs = self.paths.dirs
        self.lock = threading.RLock()
        # Server start time, surfaced by /api/health for uptime reporting.
        # Reset on switch_library (a new ctx is constructed) — that's the
        # right semantic since "uptime" means "how long has this library
        # been live" rather than "how long since python started."
        self._created_at: float = time.time()
        # the actual bind host the server opened.
        # `do_serve` overwrites this after host resolution; tests
        # that build a WebAppState directly inherit this fail-closed
        # default ("127.0.0.1") rather than the previous None which
        # silently fell through to "permissive" in the toggle gate.
        # The /api/v1/share/toggle handler reads this to refuse
        # enabling LAN sharing on a loopback-only server (which
        # would persist the flag but leave phones unable to
        # connect).
        self.bound_host: str = "127.0.0.1"
        self.face_op_lock = threading.Lock()
        # T1.3: serializes the entire ``state_lifecycle.switch_library``
        # call so two concurrent library switches (Tauri sidecar +
        # Flask endpoint, or two browser tabs) don't interleave their
        # drain / close-hook phases. ``ctx.lock`` is held only briefly
        # at the end of the switch — not for the long-running cancel +
        # join — so without a dedicated lock the heavy section is
        # unprotected.
        self._switch_library_lock = threading.Lock()
        # M8: signals when the deferred Phase 5 backfill
        # (assign_near_duplicate_clusters + refresh_smart_albums) has
        # completed. Phase 5 runs in a daemon thread so the photo grid
        # paints immediately; tests / switch_library / shutdown wait
        # on this event to observe a consistent post-startup state.
        # Defaults to set() so a freshly constructed ctx (no init_app_db
        # has run) isn't perceived as "pending" by callers that wait.
        self.smart_album_backfill_done: threading.Event = threading.Event()
        self.smart_album_backfill_done.set()
        # M8 followup: surfaces background Phase 5 failures to
        # /api/v1/health so the user / operator sees 'smart album
        # counts may be stale' instead of a silent warning buried in
        # server.log. Set True by the daemon's except block; reset to
        # False on each fresh spawn. Default False — a fresh ctx with
        # no daemon yet is considered healthy.
        self.phase5_failed: bool = False
        # Workers are constructed per-WebAppState (one set per library).
        # The registry holds factories (callables → BackgroundWorker)
        # so a future plugin can register its own worker class without
        # touching this constructor — see WorkerRegistry below.
        # P4: the dict + cancel-and-join-all loops moved into
        # :class:`WorkerPool`. ``self._workers`` is preserved as a
        # property delegate so existing call sites (bp_health,
        # state_lifecycle, tests) keep working unchanged.
        self.workers: WorkerPool = WorkerPool()
        self.thumbs: ThumbnailCache | None = None
        # P4: derived-state caches (face cluster map, enhanced ids,
        # CLIP embeddings) live in :class:`ModelCache`. The bare
        # attributes (``clip_cache``, ``_face_cluster_map``, etc.)
        # are preserved as property delegates so the ~3,800 access
        # sites the audit counted can migrate gradually.
        self.caches: ModelCache = ModelCache()
        # P4b: phash_ready / _phash_generation / _compute_thread /
        # _warm_thread / _cancel_warm now live on :class:`AnalysisStore`.
        # Property delegates preserve legacy access paths.
        self.analysis_store: AnalysisStore = AnalysisStore()
        # P4 finish: LibraryLifecycle facade over state_lifecycle.
        # ``ctx.lifecycle.switch_library(...)`` is the new path;
        # ``ctx.switch_library(...)`` still works via the existing
        # method (which is now a delegate).
        from bpp.web.library_lifecycle import LibraryLifecycle

        self.lifecycle: LibraryLifecycle = LibraryLifecycle(self)
        self.serve_mode: bool = library_path is not None
        # _edited_ids / _auto_enhanced_ids now live on caches.enhanced_ids
        # (P4 ModelCache); property delegates preserve the legacy attribute
        # access for build_photo_dict + invalidate_enhanced_cache callers.
        # Layered config resolver: walks DB → YAML → DEFAULTS. The
        # legacy `load_config()` returns DEFAULTS+YAML merged into a
        # plain dict; we feed the YAML-only delta into Config so it
        # tracks the layers separately. `Config` is dict-compatible
        # (.get/.[]/in/iter/.as_dict) so existing call sites work
        # unchanged. See bpp/config_resolver.py for the resolution
        # contract + future per-user extension hook.
        from bpp.config_resolver import Config

        yaml_overlay = {k: v for k, v in config.items() if v != DEFAULTS.get(k)}
        self.config: Config = Config(yaml_overlay, get_conn=self.get_conn)
        self.auth_token: str = secrets.token_hex(32)
        # Port is set by the serve command after argparse, so endpoints
        # can render the correct share URL. The LAN sharing toggle
        # itself lives in DB settings (see bpp/web/share.py) so it
        # survives restarts.
        self.port: int = 5001

        # P4b: warm_thread / compute_thread / cancel_warm now live on
        # self.analysis_store. Property delegates below keep the
        # legacy attribute access working unchanged.

        self.startup()

    # ------------------------------------------------------------------
    # path accessors. Read library_path / workdir / input_dir
    # via these properties rather than `self.state["..."]`. The legacy
    # dict reads still work (the AppState TypedDict is kept for
    # back-compat) but new code goes through the structured
    # `LibraryPaths` container.
    # ------------------------------------------------------------------
    @property
    def library_path(self) -> str:
        return self.paths.library_path

    @library_path.setter
    def library_path(self, value: str) -> None:
        # writes update both the structured paths and the
        # legacy state dict so callers reading either form see
        # consistent values mid-mutation.
        # LibraryPaths is now frozen, so we replace
        # the instance instead of mutating its fields. Existing
        # references via `ctx.paths` re-bind on the next attribute
        # read because callers always go through `ctx.paths.*`.
        self.paths = replace(self.paths, library_path=value)
        self.state["library_path"] = value

    @property
    def workdir(self) -> str:
        return self.paths.workdir

    @workdir.setter
    def workdir(self, value: str) -> None:
        self.paths = replace(self.paths, workdir=value)
        self.state["workdir"] = value

    @property
    def input_dir(self) -> str:
        return self.paths.input_dir

    @input_dir.setter
    def input_dir(self, value: str) -> None:
        self.paths = replace(self.paths, input_dir=value)
        self.state["input_dir"] = value

    def ensure_workdir(self) -> str:
        if not self.paths.workdir:
            import tempfile

            wd = tempfile.mkdtemp(prefix="bpp_web_")
            self.paths = replace(self.paths, workdir=wd)
            self.state["workdir"] = wd
        os.makedirs(self.paths.workdir, exist_ok=True)
        return self.paths.workdir

    def db_path(self) -> str:
        return os.path.join(self.ensure_workdir(), "photopicker.db")

    def get_conn(self) -> sqlite3.Connection:
        db_p = self.db_path()
        init_db(db_p)
        return get_db(db_p)

    def precompute_phashes(self, data: list[dict[str, Any]]) -> None:
        """Pre-compute perceptual hashes (dHash + aHash) in a background thread.

        Implementation in state_init.precompute_phashes — extracted to keep
        state.py under the 500-LOC soft cap.
        """
        from bpp.web.state_init import precompute_phashes as _impl

        _impl(self, data)

    def load_clip_embeddings(self) -> dict[int, Any]:
        """Load CLIP embeddings from DB into cache. Returns {photo_id: embedding}.

        Implementation in state_init.load_clip_embeddings — extracted to
        keep state.py under the 500-LOC soft cap.
        """
        from bpp.web.state_init import load_clip_embeddings as _impl

        return _impl(self)

    def get_face_cluster_map(self) -> dict[str, list[int]]:
        """Return a cached filepath → [cluster_ids] map, built on first call.

        Invalidated by ``invalidate_face_cluster_map()`` which face_worker
        calls after clustering completes. Cache miss cost: one JOIN query.

        P4: delegates to :class:`ModelCache.face_cluster_map`.
        """
        return self.caches.face_cluster_map.get(self.get_conn())

    def invalidate_face_cluster_map(self) -> None:
        self.caches.face_cluster_map.invalidate()

    def load_analysis_if_needed(self) -> list[dict[str, Any]] | None:
        """Lazy-load the analysis cache from DB / legacy analysis.json.

        Implementation in state_init.load_analysis_if_needed.
        """
        from bpp.web.state_init import load_analysis_if_needed as _impl

        return _impl(self)

    def init_app_db(self) -> None:
        """Initialize DB, run migrations, ensure 'All Photos' album exists.

        Implementation in state_init.init_app_db — extracted to keep
        state.py under the 500-LOC soft cap.
        """
        from bpp.web.state_init import init_app_db as _impl

        _impl(self)

    def invalidate_analysis(self) -> None:
        """Thread-safe invalidation of the analysis cache.

        Also refreshes the thumbnail hash map so newly imported photos
        are immediately servable.
        """
        with self.lock:
            self.state["analysis"] = None
        self._refresh_thumb_map()

    def _refresh_thumb_map(self) -> None:
        """Rebuild thumbnail path→hash map from DB. Body in state_ops."""
        from bpp.web.state_ops import refresh_thumb_map

        refresh_thumb_map(self)

    def auto_purge(self) -> None:
        """Purge photos deleted more than 30 days ago. Body in state_ops."""
        from bpp.web.state_ops import auto_purge

        auto_purge(self)

    def build_photo_dict(
        self, item: dict[str, Any], selected: bool | None = None
    ) -> dict[str, Any]:
        from bpp.web.state_ops import build_photo_dict

        return build_photo_dict(self, item, selected)

    def invalidate_enhanced_cache(self) -> None:
        """Drop the cached `_edited_ids` / `_auto_enhanced_ids` sets so
        the next photo-dict build re-queries the DB. Holds self.lock so
        readers in build_photo_dict() can't observe a partially-cleared
        state.
        """
        with self.lock:
            self.caches.enhanced_ids.invalidate()

    def check_dedup_feedback(
        self,
        conn: sqlite3.Connection,
        photo_id: int,
        filepath: str,
        mode: str | None,
        selected_paths: set[str] | None,
        album_id: int | None = None,
    ) -> bool:
        """Check if an override constitutes dedup feedback. Body in state_ops."""
        from bpp.web.state_ops import check_dedup_feedback

        return check_dedup_feedback(self, conn, photo_id, filepath, mode, selected_paths, album_id)

    def switch_library(self, new_path: str) -> None:
        """Hot-swap to a different library. Implementation in state_lifecycle."""
        from bpp.web.state_lifecycle import switch_library as _impl

        _impl(self, new_path)

    def startup(self) -> None:
        """Run initialization sequence. Implementation in state_lifecycle."""
        from bpp.web.state_lifecycle import startup as _impl

        _impl(self)

    def shutdown(self) -> None:
        """Cancel workers and close DB. Implementation in state_lifecycle."""
        from bpp.web.state_lifecycle import shutdown as _impl

        _impl(self)

    # Thin delegates so tests that monkey-patch / call these via the class
    # (conftest.py patches _start_file_health_checks to a no-op) keep working.
    def _init_thumbs_lightweight(self) -> None:
        from bpp.web.state_lifecycle import _init_thumbs_lightweight as _impl

        _impl(self)

    def _register_journal_recovery_handlers(self) -> None:
        from bpp.web.state_lifecycle import _register_journal_recovery_handlers as _impl

        _impl(self)

    def _recover_pending_journals(self) -> None:
        from bpp.web.state_lifecycle import _recover_pending_journals as _impl

        _impl(self)

    def _start_file_health_checks(self) -> None:
        from bpp.web.state_lifecycle import _start_file_health_checks as _impl

        _impl(self)


def get_ctx() -> WebAppState:
    """Get the shared WebAppState from the current Flask app."""
    return current_app.extensions["bpp"]


def get_ctx_or_none() -> WebAppState | None:
    """Like ``get_ctx()`` but returns None outside an app context.

    Used by recovery handlers and other startup-time hooks that
    may run before/outside a request — recover_pending() runs
    synchronously during ``startup()``, which is inside the app
    context, but a defensive None-return is cheaper than the
    RuntimeError ``current_app`` raises when there's no context.
    """
    try:
        return current_app.extensions.get("bpp")
    except RuntimeError:
        return None


def with_face_lock(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Serialize face-mutating operations via face_op_lock and invalidate
    the face cluster map on exit.

    All endpoints that mutate face_embeddings, photo_person_tags, or
    smart_person albums MUST use this decorator. Two contracts:

    1. ``face_op_lock`` serializes the body so concurrent face mutations
       can't interleave.
    2. ``invalidate_face_cluster_map()`` fires in ``finally`` so the
       grid + recompute paths see fresh ``filepath → [cluster_ids]``
       data on the next read.

    Pre-fix every face-mutation endpoint (create, update_bbox, merge,
    dismiss, split, restore, retry, tag_person, untag_person, reassign,
    purge, recluster) relied on the analyse worker's Phase 7
    invalidation. Between mutations, the cache stayed stale — the
    /api/v1/pick handler boosted by the old cluster IDs and the photo
    grid badged faces against the pre-merge state. Wiring the invalidate
    into the decorator closes the gap at one site instead of 13.

    The invalidate fires even on exception: a face mutation that partly
    committed (e.g. a SAVEPOINT rolled back inside a multi-statement
    handler) still leaves the cluster_id columns in a state the cache
    doesn't reflect, so a stale cache after error is the worst outcome.
    Cheap to invalidate; expensive to debug a "why does Alice show as
    Bob" report.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = get_ctx()
        with ctx.face_op_lock:
            try:
                return fn(*args, **kwargs)
            finally:
                ctx.invalidate_face_cluster_map()

    return wrapper
