"""Models / install blueprint: ML model lifecycle + runtime pip install.

extracted from `bp_core` to keep app-surface concerns
(index, status, pick, presets, settings) separate from the much
larger model lifecycle + pip-install plumbing. Endpoints, response
shapes, auth contracts, and the @requires_local_app gating on
every mutating route are unchanged — pure relocation.

Routes:
  GET  /api/v1/models                       — feature/model status
  POST /api/v1/models/toggle                — enable/disable a model
  POST /api/v1/models/redownload            — wipe + refetch a model
  POST /api/v1/models/uninstall             — wipe a model from disk
  POST /api/v1/install/faces                — legacy pip install kick
  GET  /api/v1/install/faces/progress       — legacy SSE
  POST /api/v1/install/<key>                — generic pip install kick
  GET  /api/v1/install/<key>/progress       — SSE stream for pip
  GET  /api/v1/install/<key>/info           — what `<key>` would install
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

from flask import Blueprint, Response, jsonify, request

from bpp.errors import BppError, ValidationError

# (Through the module, never bare names: one patch point for the tests.)
from bpp.scoring import model_lifecycle
from bpp.scoring.toggles import all_toggle_keys
from bpp.utils.logging import get_logger
from bpp.web import download_gates
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("models", __name__)


@bp.get("/api/v1/models/content-filter")
def api_content_filter_status() -> tuple[Response, int]:
    """Whether automatic sensitive-photo detection is on (accepted + on
    disk). Cheap — the sidebar hint re-reads it after any model change
    instead of fetching the whole /api/v1/status snapshot."""
    from bpp.scoring.nudity import is_active

    return jsonify({"active": bool(is_active(get_ctx().config))}), 200


@bp.get("/api/v1/models")
def api_models() -> tuple[Response, int]:
    """Return ML model status grouped by feature.

    The per-feature dicts and library/file checks live in
    ``bpp.web.models_status``; this handler is now a thin wrapper.
    """
    from bpp.web.models_status import build_model_features

    return jsonify(build_model_features(get_ctx())), 200


@bp.post("/api/v1/models/toggle")
@requires_local_app
def api_models_toggle() -> tuple[Response, int]:
    """Toggle a model on/off. Expects {key: str, enabled: bool}.

    LOCAL_APP-only — model toggles control whether face/CLIP/pets/etc.
    inference runs on every photo. A paired LAN device flipping these
    could disable owner-mandated detection (e.g., NSFW filtering) or
    re-enable expensive ML on a cold-storage library."""
    data = request.get_json(silent=True) or {}
    key = data.get("key", "")
    enabled = data.get("enabled")

    if key not in all_toggle_keys():
        raise ValidationError(
            f"Invalid model key: {key}",
            field="key",
            value=key,
        )
    if not isinstance(enabled, bool):
        raise ValidationError(
            "enabled must be a boolean",
            field="enabled",
            value=enabled,
        )

    ctx = get_ctx()
    conn = ctx.get_conn()
    from bpp.db.settings import set_setting

    set_setting(conn, key, str(enabled).lower())
    log.info("Model toggle %s = %s", key, enabled)
    return jsonify({"key": key, "enabled": enabled}), 200


@bp.post("/api/v1/models/redownload")
@requires_local_app
def api_models_redownload() -> tuple[Response, int]:
    """Delete and re-download a specific model by name.

    LOCAL_APP-only — deleting cached model files forces a fresh
    download from the public host. A paired LAN device triggering
    this could DoS-by-bandwidth and (worse) racing against the
    owner-only PyPI install path opens the same supply-chain
    surface."""
    data = request.get_json(silent=True) or {}
    model_name = data.get("name", "")
    if not model_name:
        raise ValidationError("name required", field="name")

    try:
        path, url, sha, legal_id = _prepare_redownload(model_name)
    except ValueError as e:
        raise ValidationError(
            "Invalid model name",
            field="name",
            value=model_name,
        ) from e
    # Gate refusals (status / license / unmapped licensed file) raised
    # above already carry a specific user_message and return at once.
    # The fetch itself can take minutes, so it runs as a background job;
    # the picker polls /api/v1/models/download-jobs/<job> for progress.
    from bpp.web import model_download_jobs

    job, _started = model_download_jobs.start(
        f"model:{model_name}",
        model_name,
        lambda: _fetch_redownload(model_name, path, url, sha, legal_id),
    )
    return jsonify(job.to_dict()), 202


@bp.get("/api/v1/models/download-jobs/<path:job_key>")
@requires_local_app
def api_model_download_job(job_key: str) -> tuple[Response, int]:
    """Progress of a background model download started by redownload or
    ensure-weights: ``{status, bytes_done, bytes_total, result, error}``."""
    from bpp.web import model_download_jobs

    snap = model_download_jobs.snapshot(job_key)
    if snap is None:
        return jsonify({"error": f"No download job {job_key!r}"}), 404
    return jsonify(snap), 200


def _model_path_url_sha(name: str) -> tuple[str, str, str | None]:
    """Resolve a UI model name → (path, url, sha256).

    Driven by `bpp.scoring.model_base.ModelRegistry`. Each scoring
    module registers its entry on import; this just looks it up.
    Raises ValueError on an unknown name.
    """
    from bpp.scoring.model_base import ModelRegistry

    model_lifecycle.ensure_builtin_model_files_registered()
    entry = ModelRegistry.get(name)
    if entry is None:
        raise ValueError(f"Unknown model: {name}")
    # Defensive expanduser: an entry registered with a literal "~" (or a
    # cache dir that resolved to one) must never reach makedirs()/open()
    # unexpanded — that writes to a bogus "~" directory and fails with
    # Errno 2. cache_dir() is the primary fix; this is belt-and-suspenders
    # for any entry that hardcodes a path.
    return (os.path.expanduser(entry.path), entry.url or "", entry.sha256)


def _redownload_model(name: str) -> dict:
    """Re-download a model synchronously. Returns updated model info."""
    path, url, sha, legal_id = _prepare_redownload(name)
    return _fetch_redownload(name, path, url, sha, legal_id)


def _prepare_redownload(name: str) -> tuple[str, str, str | None, str]:
    """Resolve the file and run every refusal check (fast, synchronous)."""
    path, url, sha = _model_path_url_sha(name)
    # Resolve the file name → legal entry id (fails closed when the
    # file declares none).
    legal_id = download_gates.resolve_legal_id(name)
    download_gates.enforce_download_status_gate(name, legal_id)
    download_gates.enforce_download_license_gate(name, legal_id)
    return path, url, sha, legal_id


def _fetch_redownload(name: str, path: str, url: str, sha: str | None, legal_id: str) -> dict:
    """Fetch to a temp file and swap it in. The existing copy stays in
    place (and usable) until the new one is verified."""
    from bpp.utils.download import download_file, download_temp_path

    log.info("Re-downloading model %s from %s", name, url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = download_temp_path(path)
    try:
        download_file(url, tmp_path, registry_id=legal_id, sha256=sha)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    model_lifecycle.reset_model_file(name)

    from bpp.web.models_status import _file_info

    info = _file_info(name, path)
    size_mb = (info.get("size_bytes", 0) or 0) / (1024 * 1024)
    log.info("Re-downloaded model %s (%.1f MB)", name, size_mb)
    return info


@bp.post("/api/v1/models/uninstall")
@requires_local_app
def api_models_uninstall() -> tuple[Response, int]:
    """Delete a model from disk to free space. The model can be
    re-downloaded later via the Redownload button or auto-fetched on
    next analyze.

    LOCAL_APP-only — same blast radius as redownload."""
    data = request.get_json(silent=True) or {}
    model_name = data.get("name", "")
    if not model_name:
        raise ValidationError("name required", field="name")

    try:
        path, _url, _sha = _model_path_url_sha(model_name)
    except ValueError as e:
        raise ValidationError(
            "Invalid model name",
            field="name",
            value=model_name,
        ) from e

    from bpp.utils.download import remove_partial_downloads

    bytes_freed = 0
    try:
        # Wait for a download of this file in progress, so its retry
        # can't put the model back after the user removed it.
        with _download_lock_for(model_name):
            if os.path.exists(path):
                bytes_freed = os.path.getsize(path)
                os.remove(path)
            # Also clear leftovers of interrupted downloads
            remove_partial_downloads(path)
    except OSError as e:
        raise BppError(
            "Failed to delete model file",
            user_message="Failed to delete model file",
            diagnostic_message=f"uninstall error for {model_name} at {path}: {e!s}",
            name=model_name,
        ) from e

    model_lifecycle.reset_model_file(model_name)
    log.info(
        "Uninstalled model %s (%.1f MB freed)",
        model_name,
        bytes_freed / (1024 * 1024),
    )

    from bpp.web.models_status import _file_info

    return jsonify({"file": _file_info(model_name, path), "bytes_freed": bytes_freed}), 200


def _download_lock_for(model_name: str) -> contextlib.AbstractContextManager[Any]:
    """The download lock of the ModelSingleton that owns this file, or a
    no-op for files managed by their own helpers."""
    from bpp.scoring.model_base import ModelRegistry, ModelSingleton

    entry = ModelRegistry.get(model_name)
    owner = (entry.owner or getattr(entry.reset, "__self__", None)) if entry else None
    if isinstance(owner, ModelSingleton):
        return owner.download_lock
    return contextlib.nullcontext()
