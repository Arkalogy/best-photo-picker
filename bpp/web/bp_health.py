"""Health blueprint: aggregate health snapshot + storage probe.

extracted from `bp_core` to keep the core blueprint focused
on app surface (index, status, pick, presets, settings) and put
liveness / probing concerns in their own module. The endpoints
themselves are unchanged — same routes, same response shapes,
same auth / path-filter contract.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from typing import Any

from flask import Blueprint, Response, jsonify

from bpp.db.dialect import dialect
from bpp.db.journal import pending_journals
from bpp.utils.logging import get_logger
from bpp.utils.retry import check_storage_accessible
from bpp.web.e2e_fixture import is_e2e_fixture_library
from bpp.web.share import principal_is_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("health", __name__)


@bp.get("/api/v1/health/storage")
def api_storage_health() -> tuple[Response, int]:
    """Check if the library storage path is accessible (NAS/network drives).

    Path is included in the response only for LOCAL_APP — LAN
    devices get accessibility flag without the absolute filesystem
    path (don't leak owner's username / drive layout)."""
    ctx = get_ctx()
    library_path = ctx.state.get("library_path") or ctx.state.get("workdir")
    if not library_path:
        return jsonify({"accessible": False, "error": "No library path"}), 200
    result = check_storage_accessible(library_path)
    if not principal_is_local_app():
        # Filter any path-shaped fields. check_storage_accessible
        # currently only returns accessible/error/latency_ms, but
        # future-proof against new path fields slipping in.
        # The OSError text names the absolute library path.
        result = {
            "accessible": result.get("accessible", False),
            "error": None if result.get("accessible") else "storage unreachable",
            "latency_ms": result.get("latency_ms"),
        }
    return jsonify(result), 200


@bp.get("/api/v1/_diag/is_e2e_fixture")
def api_is_e2e_fixture() -> tuple[Response, int]:
    """Report whether the active library is a synthetic e2e fixture.

    The fixture-setup script (`scripts/setup_e2e_library.py`) drops a
    ``.bpp-e2e-fixture`` sentinel file at the library root. The e2e
    mutation helpers check this before doing anything destructive — a
    user library has no such file, so a mis-pointed test suite fails
    fast with a clear message instead of polluting the real library
    with ``__e2e_album_*`` rows.
    """
    return jsonify({"is_fixture": is_e2e_fixture_library()}), 200


@bp.get("/api/v1/health")
def api_health() -> tuple[Response, int]:
    """Aggregate health snapshot for ops + the desktop wrapper.

    One call replaces 4-5 round-trips (status, storage, models, settings)
    when all the caller wants is "is the server still working?". Returns
    200 even when degraded — callers read the ``status`` field. Cheap by
    construction: no quick_check pass, no model probes, just liveness +
    quick disk + journal snapshot. Use ``/api/v1/status`` for the
    user-facing app state instead.

    Each ``checks`` block is one probe function below. LAN clients get
    the boolean flags only; absolute paths (which leak the owner's
    username, drive name, library location) are owner-only.
    """
    ctx = get_ctx()
    now = time.time()
    started_at = getattr(ctx, "_created_at", now)
    is_owner = principal_is_local_app()

    db_check, conn = _db_check(ctx, is_owner)
    storage_check = _storage_check(ctx, is_owner)
    journals_check = _journals_check(conn)
    phase5_check = _phase5_check(ctx)
    recovery_check = _recovery_check(ctx)
    face_recovery_check = _face_recovery_check(ctx)
    missing_scan_check = _missing_scan_check(ctx)
    models_check = _models_check()
    install_check = _install_check(is_owner)
    downloads_check = _downloads_check()

    # Aggregate status: ok if everything reports clean, degraded if any
    # individual probe failed. We never return "down" through this code
    # path — if the request was served at all the server is up; the
    # caller can detect down by HTTP failure / timeout.
    status = "ok"
    if (
        not db_check.get("ok")
        or not storage_check.get("accessible", False)
        or journals_check.get("pending", 0) > 0
        or phase5_check["failed"]
        or recovery_check["failed"]
        or face_recovery_check["failed"]
        or missing_scan_check["refused"]
        or "error" in models_check
        or install_check["interrupted"]
        or downloads_check["failed"]
    ):
        status = "degraded"

    return (
        jsonify(
            {
                "status": status,
                "uptime_s": round(now - started_at, 1),
                "checks": {
                    "db": db_check,
                    "storage": storage_check,
                    "workers": _workers_check(ctx, now),
                    "journals": journals_check,
                    "collaborators": _collaborators_check(ctx, is_owner),
                    "phase5": phase5_check,
                    "derived_recovery": recovery_check,
                    "face_recovery": face_recovery_check,
                    "missing_scan": missing_scan_check,
                    "models": models_check,
                    "install": install_check,
                    "downloads": downloads_check,
                    "phash": _phash_check(ctx),
                },
            }
        ),
        200,
    )


def _db_check(ctx: Any, is_owner: bool) -> tuple[dict[str, Any], Any]:
    """Liveness of the DB connection; returns the block and the
    connection (None when it couldn't be acquired)."""
    db_check: dict[str, Any] = {"ok": False}
    conn = None
    try:
        conn = ctx.get_conn()
    except Exception:
        # don't surface raw exception text — `get_conn()` may
        # raise an OSError or sqlite3.Error whose string includes the
        # absolute DB path, the owner's username, mount/drive labels,
        # or SQLite-internal hints. Even owner clients get the same
        # generic string; detailed diagnostics live in server.log,
        # which is already owner-only.
        log.warning("health: db connection acquisition failed", exc_info=True)
        db_check["error"] = "Database connection failed"
        return db_check, None

    try:
        conn.execute("SELECT 1").fetchone()
        db_check["ok"] = True
        db_check["writable"] = True  # WAL is on; if SELECT works writes do too
        db_check["schema_version"] = dialect.get_user_version(conn)
        # Only LOCAL_APP gets the absolute DB path. LAN clients
        # get the boolean health flags but not the filesystem layout
        # (which leaks the owner's username, drive name, library
        # location).
        if is_owner:
            db_path = dialect.database_path(conn)
            if db_path:
                db_check["path"] = db_path
    except sqlite3.Error as e:
        # Don't leak SQLite error text to the API — it can hint at
        # schema, table names, and version. Log details server-side.
        log.warning("health: db check failed: %s", e)
        db_check["error"] = "Database check failed"
    return db_check, conn


def _storage_check(ctx: Any, is_owner: bool) -> dict[str, Any]:
    library_path = ctx.state.get("library_path") or ctx.state.get("workdir") or ""
    # Same filter as the DB block — the path is LOCAL_APP only.
    storage_check: dict[str, Any] = {"path": library_path} if is_owner else {}
    if not library_path:
        storage_check["accessible"] = False
        storage_check["error"] = "No library path"
        return storage_check
    storage_check.update(check_storage_accessible(library_path))
    if not is_owner:
        # check_storage_accessible may include `error` text that
        # has the path in it. Strip path-bearing fields for LAN.
        storage_check = {
            k: v
            for k, v in storage_check.items()
            if k in {"accessible", "latency_ms", "free_gb", "total_gb"}
        }
    try:
        usage = shutil.disk_usage(library_path)
        storage_check["free_gb"] = round(usage.free / (1024**3), 2)
        storage_check["total_gb"] = round(usage.total / (1024**3), 2)
    except OSError as e:
        # OS error text can include local path fragments — keep it
        # in logs only.
        log.warning("health: disk_usage failed for %s: %s", library_path, e)
        storage_check["disk_usage_error"] = "Disk usage check failed"
    return storage_check


def _workers_check(ctx: Any, now: float) -> dict[str, dict[str, Any]]:
    """Worker liveness + activity — iterate the registry rather than
    spelling out individual workers so a future plugin worker shows
    up automatically. ``last_activity_s`` lets an operator
    distinguish a stuck worker (alive=True but last activity grew
    minutes-stale) from a slow-but-progressing one. The field is
    absent when the worker never ran in this process (no progress
    events emitted yet).

    Proof-of-concept consumer: reach through ctx.workers directly
    instead of the deprecated ctx._workers property delegate. The
    delegate still works (logs a deprecation warning), but the
    collaborator surface is what every new endpoint should target;
    this one was migrated as proof. See docs/architecture-notes.md.
    """
    worker_check: dict[str, dict[str, Any]] = {}
    for name, worker in ctx.workers.items():
        info: dict[str, Any] = {"alive": bool(getattr(worker, "is_alive", False))}
        last = float(getattr(worker, "_last_activity", 0.0))
        if last > 0:
            info["last_activity_s"] = round(now - last, 1)
        worker_check[name] = info
    return worker_check


def _journals_check(conn: Any) -> dict[str, Any]:
    journals_check: dict[str, Any] = {"pending": 0, "kinds": {}}
    if conn is None:
        return journals_check
    try:
        entries = pending_journals(conn)
        kinds: dict[str, int] = {}
        for entry in entries:
            k = str(entry.get("kind", "unknown"))
            kinds[k] = kinds.get(k, 0) + 1
        return {"pending": len(entries), "kinds": kinds}
    except sqlite3.Error as e:
        log.warning("health: journals check failed: %s", e)
        journals_check["error"] = "Journals check failed"
        return journals_check


def _collaborators_check(ctx: Any, is_owner: bool) -> dict[str, Any]:
    """Surface collaborator state. The WorkerPool block lives under
    "workers"; this adds the cache + store collaborators so on-call can
    tell whether the phash compute thread is alive, whether CLIP /
    face-cluster caches are populated, and the analysis-store
    generation counter (which bumps on every switch_library — a runaway
    switch loop shows up here as a generation that doesn't match the
    uptime)."""
    collaborators_check: dict[str, Any] = {}
    analysis_store = getattr(ctx, "analysis_store", None)
    if analysis_store is not None:
        as_block: dict[str, Any] = {
            "phash_ready": bool(
                getattr(analysis_store, "phash_ready", None) and analysis_store.phash_ready.is_set()
            ),
            "generation": int(getattr(analysis_store, "phash_generation", 0)),
        }
        ct = getattr(analysis_store, "compute_thread", None)
        as_block["compute_thread_alive"] = bool(ct and ct.is_alive())
        wt = getattr(analysis_store, "warm_thread", None)
        as_block["warm_thread_alive"] = bool(wt and wt.is_alive())
        collaborators_check["analysis_store"] = as_block

    caches = getattr(ctx, "caches", None)
    if caches is not None:
        clip_cache = getattr(caches, "clip_cache", None) or {}
        face_map = getattr(caches, "face_cluster_map", None)
        enhanced = getattr(caches, "enhanced_ids", None)
        cache_block: dict[str, Any] = {
            "clip_ready": bool(clip_cache.get("ready")),
            "clip_embeddings_count": len(clip_cache.get("embeddings") or {}),
        }
        if face_map is not None:
            fmap = getattr(face_map, "_map", None)
            cache_block["face_cluster_map_size"] = len(fmap) if fmap is not None else None
        if enhanced is not None:
            ed = getattr(enhanced, "edited", None)
            ae = getattr(enhanced, "auto_enhanced", None)
            cache_block["edited_ids_count"] = len(ed) if ed is not None else None
            cache_block["auto_enhanced_ids_count"] = len(ae) if ae is not None else None
        # Corrupt-embedding counters — every read site that decodes a face
        # embedding BLOB increments these. Non-zero "bad" counts mean
        # there's corruption in face_embeddings; the user can spot it
        # without waiting for an endpoint to 500.
        from bpp.db.face_embedding_safety import get_counters as _es_counters

        cache_block["embedding_safety"] = _es_counters()
        collaborators_check["caches"] = cache_block

    lifecycle = getattr(ctx, "lifecycle", None)
    if lifecycle is not None and is_owner:
        # Path leaks the owner's username and library location —
        # owner-only.
        collaborators_check["lifecycle"] = {
            "library_path": getattr(lifecycle, "library_path", None),
            "workdir": getattr(lifecycle, "workdir", None),
        }
    return collaborators_check


def _phase5_check(ctx: Any) -> dict[str, bool]:
    """Phase 5 (background dup-cluster + smart-album backfill) failure
    surfaces here so the operator sees 'smart album counts may be
    stale' without grepping server.log. The flag is reset on every
    spawn and set by the daemon's except block — True means the
    last attempted run failed. Active iff library has been opened
    (smart_album_backfill_done starts True on fresh ctx)."""
    _backfill_event = getattr(ctx, "smart_album_backfill_done", None)
    return {
        "failed": bool(getattr(ctx, "phase5_failed", False)),
        "in_flight": bool(_backfill_event) and not _backfill_event.is_set(),
    }


def _missing_scan_check(ctx: Any) -> dict[str, Any]:
    """The missing-file scan declined to mark more than half the library
    missing (``refused`` with the counts): an outage, or a real mass
    deletion the manual recheck must confirm."""
    store = getattr(ctx, "analysis_store", None)
    refused = getattr(store, "missing_scan_refused", None)
    if not refused:
        return {"refused": False}
    return {"refused": True, "missing": refused[0], "total": refused[1]}


def _face_recovery_check(ctx: Any) -> dict[str, bool]:
    """Resuming an interrupted face extraction at startup: ``in_flight``
    while it runs on its thread, ``failed`` when it died."""
    store = getattr(ctx, "analysis_store", None)
    return {
        "failed": bool(getattr(store, "face_recovery_failed", False)),
        "in_flight": bool(getattr(store, "face_recovery_running", False)),
    }


def _recovery_check(ctx: Any) -> dict[str, bool]:
    """The derived-recovery thread (hash backfill, sidecar tags, dup
    clusters, Moments, smart albums — after an import or at startup):
    ``failed`` when any step of its last run failed (near-duplicate and
    smart-album state may be incomplete), ``in_flight`` while the whole
    pipeline runs. Reset on every spawn and on a library switch."""
    store = getattr(ctx, "analysis_store", None)
    return {
        "failed": bool(getattr(store, "recovery_failed", False)),
        "in_flight": bool(getattr(store, "recovery_running", False)),
    }


def _install_check(is_owner: bool) -> dict[str, Any]:
    """Feature installs: what waits for a restart, which pip left half-done
    (``interrupted``, by feature key: the library degrades until repaired),
    and the running work an install would have to wait for. The cheap
    part of ``/api/v1/install/state`` only (no refusal check, no model
    probe). Paired devices get nothing to act on."""
    from bpp.web import install_status
    from bpp.web.install_busy import busy_workers

    if not is_owner:
        return {"restart_pending": [], "interrupted": [], "busy": []}
    state = install_status.quick_state()
    return {
        "restart_pending": state["restart_pending"],
        "interrupted": state["interrupted"],
        "busy": busy_workers(),
    }


def _downloads_check() -> dict[str, Any]:
    """Model download jobs: running keys and the failed ones with the
    reason. A failed download degrades health until it is retried."""
    from bpp.web import model_download_jobs

    return model_download_jobs.summary()


def _phash_check(ctx: Any) -> dict[str, Any]:
    """The perceptual-hash backfill the duplicate finder waits on: whether
    it has finished for every analysed photo, whether its thread is still
    running, and the store generation (bumps on every library switch)."""
    store = ctx.analysis_store
    thread = getattr(store, "compute_thread", None)
    return {
        "ready": bool(store.phash_ready.is_set()),
        "in_flight": bool(thread is not None and thread.is_alive()),
        "generation": int(getattr(store, "phash_generation", 0)),
    }


def _models_check() -> dict[str, Any]:
    """Every registered model the load gate would refuse right now, by
    reason (license not accepted / older terms / commercial use without
    the rights assertion / withdrawn). Informational: it includes
    optional models nobody switched on. Cached until the license files
    or registry entries change."""
    from bpp.web.model_filter import license_blocked_models

    try:
        return {"license_blocked": license_blocked_models()}
    except Exception:
        log.warning("health: license check failed", exc_info=True)
        return {"error": "License check failed"}
