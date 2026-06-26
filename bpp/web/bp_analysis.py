"""Analysis blueprint: analyze, import, library routes."""

from __future__ import annotations

import contextlib
import json
import os

from flask import Blueprint, Response, jsonify, request

from bpp.constants import PROGRESS_QUEUE_TIMEOUT_S, WORKER_JOIN_TIMEOUT_S
from bpp.db.library import clear_library
from bpp.db.settings import get_all_settings
from bpp.errors import ConflictError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("analysis", __name__)


@bp.post("/api/v1/analyze")
@requires_local_app
def api_analyze() -> tuple[Response, int]:
    """Kick off the analysis worker over a folder or archive.

    Body params: ``input_dir`` (folder or .zip/.tar.* archive),
    ``recursive``. Merges DB-stored model toggles and detection
    thresholds into the worker config. Returns 409 if the import or
    face-extraction worker is already running.

    LOCAL_APP-only — analyze reads files from a host-supplied path.
    A LAN device passing `input_dir=/etc` could exercise the worker
    against arbitrary host content; the owner gates this from the
    Mac UI."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    input_dir = data.get("input_dir") or ctx.input_dir
    recursive = data.get("recursive", False)

    archive_exts = (".zip", ".tar.gz", ".tgz", ".tar.bz2", ".tar")
    is_archive = (
        input_dir
        and os.path.isfile(input_dir)
        and any(input_dir.lower().endswith(ext) for ext in archive_exts)
    )
    if not input_dir or not (os.path.isdir(input_dir) or is_archive):
        raise ValidationError(
            "Invalid input folder or archive",
            input_dir=input_dir,
        )

    ctx.input_dir = os.path.abspath(input_dir)
    wd = ctx.ensure_workdir()

    # Merge DB settings into analysis config so user tuning takes effect
    conn = ctx.get_conn()
    db_settings = get_all_settings(conn)
    merged_config = dict(ctx.config)

    for key in ("face_detection_confidence", "face_embedding_confidence", "max_long_side"):
        if key in db_settings:
            with contextlib.suppress(ValueError, TypeError):
                merged_config[key] = float(db_settings[key])

    # Model toggle settings (stored as "true"/"false" strings in DB)
    from bpp.constants import MODEL_TOGGLE_KEYS

    for key in MODEL_TOGGLE_KEYS:
        if key in db_settings:
            merged_config[key] = db_settings[key].lower() in ("true", "1", "yes")

    # Tell the worker whether face extraction is available
    from bpp.scoring.face_embed import is_available as _face_avail

    merged_config["face_recognition_available"] = _face_avail()

    if ctx.import_worker.is_alive:
        raise ConflictError(
            "Import in progress — wait for it to finish",
            blocker="import",
        )
    if ctx.face_worker.is_alive:
        raise ConflictError(
            "Face extraction in progress — wait for it to finish",
            blocker="face_extraction",
        )

    started = ctx.worker.start(
        input_dir=ctx.input_dir,
        workdir=wd,
        config=merged_config,
        extensions=ctx.state["extensions"],
        recursive=recursive,
    )

    if not started:
        raise ConflictError("Analysis already in progress")

    ctx.invalidate_analysis()
    return jsonify({"status": "started", "workdir": wd}), 202


@bp.get("/api/v1/analyze/progress")
def api_analyze_progress() -> Response:
    """Stream analysis-worker progress as Server-Sent Events.

    Yields ``{type: progress|done|error|keepalive}`` messages until the
    worker finishes. For LAN devices the stream re-checks pairing
    trust periodically and emits ``auth_revoked`` on revocation."""
    ctx = get_ctx()

    # Capture the LAN device fingerprint at handshake — auth was just
    # verified by the @before_request middleware. While the generator
    # runs, the owner can revoke the device. We re-check every ~5s so
    # a revoke takes effect promptly mid-stream instead of waiting for
    # the client's next reconnect.
    fp = _captured_lan_fingerprint()

    def generate():
        yield from _stream_with_revoke_check(
            ctx.worker.progress_queue,
            ctx.worker,
            fp,
            ctx,
            on_done=_finalize_analyze,
        )

    return Response(generate(), mimetype="text/event-stream")


def _captured_lan_fingerprint() -> str | None:
    """Return the bpp_share_fp cookie value if the request principal is
    a LAN device; None for LOCAL_APP (loopback / app token). LAN
    devices are the ones subject to revoke; loopback is bound to the
    server process and doesn't need re-checking."""
    from flask import g

    from bpp.web.share import PRINCIPAL_LAN_DEVICE

    principal = getattr(g, "bpp_principal", None)
    if principal is None or principal.kind != PRINCIPAL_LAN_DEVICE:
        return None
    return principal.fingerprint


def _stream_with_revoke_check(progress_queue, worker, fp, ctx, *, on_done):
    """Yield SSE messages from `progress_queue` until done/error.

    Periodically re-verifies that `fp` (the LAN-device fingerprint
    captured at handshake) is still trusted. On revoke, yields an
    auth_revoked error message and breaks the loop.

    `worker` is the BaseWorker driving the queue — used for the
    worker-death keepalive escape.
    `on_done(msg, ctx)` runs after a successful "done" message.
    """
    from bpp.web.share import is_device_trusted

    msgs_since_recheck = 0
    while True:
        try:
            msg = progress_queue.get(timeout=PROGRESS_QUEUE_TIMEOUT_S)
            yield f"data: {json.dumps(msg)}\n\n"
            if msg.get("type") in ("done", "error"):
                if msg.get("type") == "done":
                    on_done(msg, ctx)
                break
            msgs_since_recheck += 1
            # Re-auth gate: every ~5 messages, verify the LAN device
            # is still trusted. LOCAL_APP (fp=None) skips the check.
            if fp is not None and msgs_since_recheck >= 5:
                msgs_since_recheck = 0
                if not is_device_trusted(ctx.get_conn(), fp):
                    yield f"data: {json.dumps({'type': 'error', 'message': 'auth_revoked'})}\n\n"
                    break
        except Exception as exc:
            log.debug("SSE keepalive after %s: %s", type(exc).__name__, exc)
            yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
            # On keepalive (queue starvation), recheck auth too — the
            # owner may have revoked while no progress was streaming.
            if fp is not None and not is_device_trusted(ctx.get_conn(), fp):
                yield f"data: {json.dumps({'type': 'error', 'message': 'auth_revoked'})}\n\n"
                break
            if not worker.is_alive:
                err = {"type": "error", "message": "Worker stopped unexpectedly"}
                yield f"data: {json.dumps(err)}\n\n"
                break


def _finalize_analyze(msg, ctx):
    """Post-completion bookkeeping for /api/analyze/progress.

    Since S4 (2026-06-12) the WORKER finalizes itself before emitting
    "done" — a headless analyze (no SSE consumer) previously never
    finalized at all. This hook stays as a cheap idempotent backstop:
    invalidate + reload is a no-op when the worker already did it.
    """
    ctx.invalidate_analysis()
    ctx.load_analysis_if_needed()
    if msg.get("clip_computed", 0) > 0:
        ctx.load_clip_embeddings()


@bp.post("/api/v1/analyze/cancel")
@requires_local_app
def api_analyze_cancel() -> tuple[Response, int]:
    """Signal the analysis worker to stop and wait briefly for it to
    join. Returns ``not_running`` when nothing was active."""
    ctx = get_ctx()
    if not ctx.worker.is_alive:
        return jsonify({"status": "not_running"}), 200
    ctx.worker.cancel_and_join(timeout=WORKER_JOIN_TIMEOUT_S)
    return jsonify({"status": "cancelling"}), 200


@bp.post("/api/v1/import/cancel")
@requires_local_app
def api_import_cancel() -> tuple[Response, int]:
    """Signal the import worker to stop and wait briefly for it to
    join. Returns ``not_running`` when nothing was active."""
    ctx = get_ctx()
    if not ctx.import_worker.is_alive:
        return jsonify({"status": "not_running"}), 200
    ctx.import_worker.cancel_and_join(timeout=WORKER_JOIN_TIMEOUT_S)
    return jsonify({"status": "cancelling"}), 200


@bp.delete("/api/v1/analysis-cache")
@requires_local_app
def api_clear_analysis_cache() -> tuple[Response, int]:
    """Delete the analysis cache so next analyze re-scores everything.

    LOCAL_APP-only — destructive cache wipe + the next analyze
    will re-run full scoring (CPU/disk pressure)."""
    ctx = get_ctx()
    if ctx.worker.is_alive or ctx.import_worker.is_alive:
        raise ConflictError(
            "Cannot clear while analysis or import is running",
            blocker="analysis_or_import",
        )
    wd = ctx.state.get("workdir") or ctx.dirs.get("data", "")
    if not wd:
        raise ValidationError("No workdir configured")
    cache_path = os.path.join(wd, "analysis_cache.db")
    if os.path.exists(cache_path):
        os.remove(cache_path)
        log.info("Cleared analysis cache: %s", cache_path)
        return jsonify({"status": "cleared"}), 200
    return jsonify({"status": "no_cache"}), 200


@bp.post("/api/v1/compute-hashes")
@requires_local_app
def api_compute_hashes() -> tuple[Response, int]:
    """Compute perceptual hashes for photos missing them, then cluster near-duplicates.

    With hashes missing, the work runs as the ordered derived-recovery
    pipeline in the background (hashes -> sidecar tags -> dup clusters
    -> Moments -> smart-album refresh) and the endpoint returns
    ``{"status": "started"}`` — clustering synchronously here would
    race the pipeline over half-computed hashes (the wipe-incident bug
    class). With nothing missing, clustering is cheap and runs inline,
    returning counts as before.
    """
    from bpp.db.dedupe import assign_near_duplicate_clusters
    from bpp.db.moments import assign_moment_clusters

    ctx = get_ctx()
    data = ctx.load_analysis_if_needed()
    if not data:
        raise ValidationError("No analysis data")
    conn = ctx.get_conn()
    row = conn.execute(
        "SELECT COUNT(*) FROM photos WHERE (phash IS NULL OR ahash IS NULL)"
        " AND missing=0 AND deleted_at IS NULL AND is_live_photo_sidecar=0"
    ).fetchone()
    missing = row[0] if row else 0
    if missing > 0:
        # The pipeline does hash -> sidecar -> dup -> moments -> refresh
        # in order; don't duplicate (and race) the clustering here.
        ctx.precompute_phashes(data)
        return jsonify({"status": "started", "missing": missing}), 200
    clustered = assign_near_duplicate_clusters(conn)
    moments = assign_moment_clusters(conn)
    return (
        jsonify({"status": "done", "missing": missing, "clustered": clustered, "moments": moments}),
        200,
    )


@bp.delete("/api/v1/library")
@requires_local_app
def api_clear_library() -> tuple[Response, int]:
    """Delete all photos from DB and disk. Requires confirmation='delete'.

    LOCAL_APP-only — this is the most destructive endpoint in the
    app: a paired LAN device hitting it could wipe the entire
    library (DB rows + photo files on disk). The confirmation field
    is a UI guard, not an auth boundary."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    if data.get("confirmation") != "delete":
        raise ValidationError(
            "Must send confirmation: 'delete'",
            field="confirmation",
        )
    if ctx.worker.is_alive or ctx.import_worker.is_alive:
        raise ConflictError(
            "Cannot clear while analysis or import is running",
            blocker="analysis_or_import",
        )
    conn = ctx.get_conn()
    library_path = ctx.library_path
    if not library_path:
        raise ValidationError("No library configured")
    result = clear_library(conn, library_path)
    ctx.invalidate_analysis()
    with ctx.lock:
        ctx.thumbs = None
        ctx.caches.clip_cache["embeddings"] = {}
        ctx.caches.clip_cache["ready"] = False
    return jsonify({"status": "cleared", **result}), 200


@bp.post("/api/v1/import")
@requires_local_app
def api_import() -> tuple[Response, int]:
    """Start the import worker copying photos from ``source_dir`` into
    the active library. Optional ``batch_name`` controls the destination
    subfolder under ``photos/``. Returns 409 when analyze or face
    extraction is already running.

    LOCAL_APP-only — import reads from a host-supplied source folder
    and writes into the library photos directory. A LAN device must
    not be able to ingest arbitrary host paths."""
    ctx = get_ctx()
    data = request.get_json(silent=True) or {}
    source_dir = data.get("source_dir")
    if not source_dir or not os.path.isdir(source_dir):
        raise ValidationError(
            "Invalid source directory",
            source_dir=source_dir,
        )

    if ctx.worker.is_alive:
        raise ConflictError(
            "Analysis in progress — wait for it to finish",
            blocker="analysis",
        )
    if ctx.face_worker.is_alive:
        raise ConflictError(
            "Face extraction in progress — wait for it to finish",
            blocker="face_extraction",
        )

    source_dir = os.path.abspath(source_dir)
    batch_name = data.get("batch_name")
    # User opt-in: include Live Photo motion sidecars in the import.
    # Default False — sidecars are filtered at scan time when the parent
    # exists in the same source directory.  See bpp/db/live_photo.py.
    import_live_photo_sidecars = bool(data.get("import_live_photo_sidecars", False))
    wd = ctx.ensure_workdir()
    lib = ctx.library_path
    os.makedirs(lib, exist_ok=True)

    started = ctx.import_worker.start(
        source_dir=source_dir,
        library_path=lib,
        workdir=wd,
        config=ctx.config,
        extensions=ctx.state["extensions"],
        batch_name=batch_name,
        import_live_photo_sidecars=import_live_photo_sidecars,
    )
    if not started:
        raise ConflictError("Import already in progress")

    ctx.invalidate_analysis()
    return jsonify({"status": "started", "library_path": lib}), 202


@bp.get("/api/v1/import/progress")
def api_import_progress() -> Response:
    """Stream import-worker progress as Server-Sent Events. Same
    revoke-aware streaming envelope as the analyze progress route."""
    ctx = get_ctx()
    fp = _captured_lan_fingerprint()

    def generate():
        yield from _stream_with_revoke_check(
            ctx.import_worker.progress_queue,
            ctx.import_worker,
            fp,
            ctx,
            on_done=_finalize_import,
        )

    return Response(generate(), mimetype="text/event-stream")


def _finalize_import(msg, ctx):
    """Post-completion bookkeeping for /api/import/progress."""
    ctx.invalidate_analysis()
    ctx.load_analysis_if_needed()
    try:
        from bpp.db.settings import delete_setting

        delete_setting(ctx.get_conn(), "first_run")
    except Exception:
        log.debug("first_run setting cleanup skipped", exc_info=True)


@bp.get("/api/v1/library/status")
def api_library_status() -> tuple[Response, int]:
    """Return library presence + import worker state.

    LOCAL_APP gets the absolute library_path + batch folder names.
    LAN clients get the boolean fields only — D-05: don't leak the
    owner's filesystem layout (username, drive name, library
    location) to a paired phone."""
    # predicate moved from bp_core to share.py. The
    # cross-blueprint import was a code-smell — `principal_is_local_app`
    # belongs next to `PRINCIPAL_LOCAL_APP` and `Principal`, not
    # buried in any one route file.
    from bpp.web.share import principal_is_local_app

    ctx = get_ctx()
    lib = ctx.library_path
    photos_dir = ctx.dirs["photos"]
    exists = os.path.isdir(photos_dir)
    batches = []
    if exists:
        try:
            for entry in sorted(os.scandir(photos_dir), key=lambda e: e.name):
                if entry.is_dir():
                    batches.append(entry.name)
        except OSError as e:
            log.warning("Failed to scan photos directory %s: %s", photos_dir, e)

    is_owner = principal_is_local_app()
    response = {
        "exists": exists,
        "importing": ctx.import_worker.is_alive,
    }
    if is_owner:
        response["library_path"] = lib
        response["batches"] = batches
    return jsonify(response), 200
