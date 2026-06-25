"""Photo export endpoints — the /api/v1/export* surface (synchronous
export + the start/progress/cancel SSE flow).

Split out of bp_photos.py for the 500-LOC cap; registered as its own
blueprint in app.py. Export progress state lives on the app context
(``ctx``), not module globals, so nothing shared moves with it.
"""

from __future__ import annotations

import json
import os

from flask import Blueprint, Response, jsonify, request

from bpp.constants import WORKER_JOIN_TIMEOUT_S
from bpp.errors import BppError, ConflictError, NotFoundError, ValidationError
from bpp.output.export import export_selected
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("export", __name__)


@bp.get("/api/v1/export/modes")
@requires_local_app
def api_export_modes() -> tuple[Response, int]:
    """List the available export "copy method" modes for the modal dropdown.

    Sourced from ExportModeRegistry so plugin-registered modes show up in
    the UI without a frontend edit. ``zip`` is omitted — it's the separate
    "bundle into a .zip" checkbox, not a per-photo copy method.
    """
    from bpp.output.export_modes import ExportModeRegistry

    modes = [
        {"name": m.name, "description": m.description, "builtin": m.is_builtin}
        for m in ExportModeRegistry.all()
        if m.name != "zip"
    ]
    return jsonify({"modes": modes}), 200


@bp.post("/api/v1/export")
@requires_local_app
def api_export() -> tuple[Response, int]:
    """Copy or convert ``selected_paths`` into ``outdir`` with optional
    gallery, manifest, XMP sidecars, format conversion, max_size, and
    JPEG quality. The output directory must lie within the library,
    workdir, or user home; mode/fmt/quality/max_size are validated.
    Returns ``{count, failed}`` from the underlying export pipeline."""
    ctx = get_ctx()
    analysis = ctx.load_analysis_if_needed()
    if analysis is None:
        raise NotFoundError("No analysis data")

    params = request.get_json(silent=True) or {}
    outdir = params.get("outdir")
    if not outdir:
        raise ValidationError("outdir is required", field="outdir")

    outdir = os.path.abspath(outdir)

    # shared allowlist helper instead of an open-coded
    # realpath + is_relative_to loop. Same allow-list as before:
    # library, workdir, or anywhere under the user's home.
    from bpp.utils.path_validation import build_library_allowlist, is_path_under_any

    allowed = build_library_allowlist(
        library_path=ctx.state.get("library_path"),
        workdir=ctx.state.get("workdir"),
        include_home=True,
    )
    if not is_path_under_any(outdir, allowed):
        # ValidationError (400) rather than ForbiddenError (403) to
        # preserve the historical 400 status — pre-T2 callers and
        # tests pin this as a 'bad input' check, not an 'auth
        # denied' one.
        raise ValidationError(
            "Output directory is outside allowed paths",
            field="outdir",
            outdir=outdir,
        )

    selected_paths = set(params.get("selected_paths", []))

    if not selected_paths:
        raise ValidationError("No photos selected", field="selected_paths")

    selected = [item for item in analysis if item["filepath"] in selected_paths]

    fmt = params.get("fmt", "original")
    if fmt not in ("original", "jpeg", "png"):
        raise ValidationError(
            "Invalid format",
            field="fmt",
            value=fmt,
            allowed=["original", "jpeg", "png"],
        )

    max_size = params.get("max_size")
    if max_size is not None:
        try:
            max_size = int(max_size)
        except (TypeError, ValueError) as e:
            raise ValidationError(
                "max_size must be a number",
                field="max_size",
            ) from e
        if max_size < 100:
            raise ValidationError(
                "max_size must be >= 100",
                field="max_size",
                value=max_size,
                min=100,
            )

    try:
        quality = int(params.get("quality", 85))
    except (TypeError, ValueError) as e:
        raise ValidationError(
            "quality must be a number",
            field="quality",
        ) from e
    if not 1 <= quality <= 100:
        raise ValidationError(
            "quality must be 1-100",
            field="quality",
            value=quality,
            min=1,
            max=100,
        )

    log.info(
        "Export starting: %d photo(s) -> %s (mode=%s, fmt=%s)",
        len(selected),
        outdir,
        params.get("mode", "copy"),
        fmt,
    )
    try:
        # ctx.config is the layered Config resolver, not a plain dict.
        # export_selected serializes the config into report.json — pass
        # the flat resolved snapshot.
        result = export_selected(
            selected=selected,
            analysis=analysis,
            outdir=outdir,
            mode=params.get("mode", "copy"),
            gallery=params.get("gallery", True),
            config=ctx.config.as_dict(),
            fmt=fmt,
            max_size=max_size,
            quality=quality,
            write_manifest=params.get("write_manifest", False),
            write_xmp=params.get("write_xmp", False),
            library_path=ctx.state.get("library_path", ""),
            strip_metadata=params.get("strip_metadata", True),
        )
    except Exception as e:
        raise BppError(
            "Export failed",
            user_message="Export failed",
            diagnostic_message=f"export_selected raised: {e!s}",
            outdir=outdir,
        ) from e

    log.info(
        "Export complete: %d exported, %d failed%s",
        result.exported,
        result.failed,
        f" (aborted: {result.disk_error['category']})" if result.disk_error else "",
    )
    return jsonify(
        {
            "status": "exported",
            "outdir": outdir,
            "count": result.exported,
            "failed": result.failed,
            "disk_error": result.disk_error,
        }
    ), 200


def _validate_export_params(ctx, params):
    """Validation shared between /api/v1/export (sync) and
    /api/v1/export/start (streaming worker, L-S3). Returns the tuple
    ``(selected, outdir, fmt, max_size, quality)`` or raises
    ValidationError. Pulled out so both endpoints stay in sync and a
    later schema change lands in one place.
    """
    analysis = ctx.load_analysis_if_needed()
    if analysis is None:
        raise NotFoundError("No analysis data")

    outdir = params.get("outdir")
    if not outdir:
        raise ValidationError("outdir is required", field="outdir")
    outdir = os.path.abspath(outdir)

    from bpp.utils.path_validation import build_library_allowlist, is_path_under_any

    allowed = build_library_allowlist(
        library_path=ctx.state.get("library_path"),
        workdir=ctx.state.get("workdir"),
        include_home=True,
    )
    if not is_path_under_any(outdir, allowed):
        raise ValidationError(
            "Output directory is outside allowed paths",
            field="outdir",
            outdir=outdir,
        )

    selected_paths = set(params.get("selected_paths", []))
    if not selected_paths:
        raise ValidationError("No photos selected", field="selected_paths")
    selected = [item for item in analysis if item["filepath"] in selected_paths]

    fmt = params.get("fmt", "original")
    if fmt not in ("original", "jpeg", "png"):
        raise ValidationError(
            "Invalid format",
            field="fmt",
            value=fmt,
            allowed=["original", "jpeg", "png"],
        )

    max_size = params.get("max_size")
    if max_size is not None:
        try:
            max_size = int(max_size)
        except (TypeError, ValueError) as e:
            raise ValidationError(
                "max_size must be a number",
                field="max_size",
            ) from e
        if max_size < 100:
            raise ValidationError(
                "max_size must be >= 100",
                field="max_size",
                value=max_size,
                min=100,
            )

    try:
        quality = int(params.get("quality", 85))
    except (TypeError, ValueError) as e:
        raise ValidationError(
            "quality must be a number",
            field="quality",
        ) from e
    if not 1 <= quality <= 100:
        raise ValidationError(
            "quality must be 1-100",
            field="quality",
            value=quality,
            min=1,
            max=100,
        )

    return selected, analysis, outdir, fmt, max_size, quality


@bp.post("/api/v1/export/start")
@requires_local_app
def api_export_start() -> tuple[Response, int]:
    """L-S3: spawn the streaming export worker, return 202 immediately.

    Same param schema as /api/v1/export. The browser then opens
    /api/v1/export/progress (SSE) to receive per-photo events and the
    final done payload. Solves the 'export looks frozen for 30-60s on
    100+ photo batches' UX problem the synchronous endpoint has.
    Synchronous /api/v1/export is retained for back-compat callers
    that don't want a streaming consumer.
    """
    ctx = get_ctx()
    params = request.get_json(silent=True) or {}
    selected, analysis, outdir, fmt, max_size, quality = _validate_export_params(
        ctx,
        params,
    )

    if ctx.export_worker.is_alive:
        raise ConflictError(
            "An export is already in progress",
            user_message="An export is already in progress",
        )

    started = ctx.export_worker.start(
        selected=selected,
        analysis=analysis,
        outdir=outdir,
        config=ctx.config.as_dict(),
        mode=params.get("mode", "copy"),
        gallery=params.get("gallery", True),
        fmt=fmt,
        max_size=max_size,
        quality=quality,
        write_manifest=params.get("write_manifest", False),
        write_xmp=params.get("write_xmp", False),
        library_path=ctx.state.get("library_path", ""),
        strip_metadata=params.get("strip_metadata", True),
    )
    if not started:
        raise ConflictError(
            "Export worker failed to start",
            user_message="Export worker failed to start",
        )
    return jsonify({"status": "started", "total": len(selected), "outdir": outdir}), 202


@bp.get("/api/v1/export/progress")
def api_export_progress() -> Response:
    """SSE stream of export progress. Each event carries one of:
    ``start`` / ``export_progress`` / ``done`` / ``error`` / ``cancelled``.
    Open by the frontend right after /api/v1/export/start returns 202.
    """
    import queue
    import time

    ctx = get_ctx()

    def generate():
        worker = ctx.export_worker
        # Idle-timeout so a dead consumer doesn't pin the worker queue
        # forever. Mirrors the import/analyze stream cadence.
        last_event = time.time()
        while True:
            try:
                msg = worker.progress_queue.get(timeout=1.0)
            except queue.Empty:
                if not worker.is_alive and time.time() - last_event > 2.0:
                    break
                # Heartbeat keeps proxies + browsers from closing the
                # connection during quiet periods (e.g., large image
                # conversion taking >30s without per-photo events).
                yield ": heartbeat\n\n"
                continue
            last_event = time.time()
            yield f"data: {json.dumps(msg)}\n\n"
            if msg.get("type") in {"done", "error", "cancelled"}:
                break

    return Response(generate(), mimetype="text/event-stream")


@bp.post("/api/v1/export/cancel")
@requires_local_app
def api_export_cancel() -> tuple[Response, int]:
    """L-S3: signal the streaming export worker to stop after its
    current photo. The progress stream then emits a ``cancelled``
    event and closes."""
    ctx = get_ctx()
    if not ctx.export_worker.is_alive:
        return jsonify({"status": "not_running"}), 200
    ctx.export_worker.cancel_and_join(timeout=WORKER_JOIN_TIMEOUT_S)
    return jsonify({"status": "cancelled"}), 200
