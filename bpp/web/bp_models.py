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

from bpp.constants import MODEL_TOGGLE_KEYS
from bpp.errors import BppError, ConflictError, ValidationError
from bpp.utils.logging import get_logger
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

    return jsonify({"active": bool(is_active())}), 200


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

    if key not in MODEL_TOGGLE_KEYS:
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


def _ensure_model_modules_imported() -> None:
    """Make sure every built-in model file is registered (the registry is
    filled by importing each scoring module; a fresh server may hit
    Settings → Models before any photo is scored)."""
    from bpp.scoring.model_lifecycle import ensure_builtin_model_files_registered

    ensure_builtin_model_files_registered()


def _model_path_url_sha(name: str) -> tuple[str, str, str | None]:
    """Resolve a UI model name → (path, url, sha256).

    Driven by `bpp.scoring.model_base.ModelRegistry`. Each scoring
    module registers its entry on import; this just looks it up.
    Raises ValueError on an unknown name.
    """
    from bpp.scoring.model_base import ModelRegistry

    _ensure_model_modules_imported()
    entry = ModelRegistry.get(name)
    if entry is None:
        raise ValueError(f"Unknown model: {name}")
    # Defensive expanduser: an entry registered with a literal "~" (or a
    # cache dir that resolved to one) must never reach makedirs()/open()
    # unexpanded — that writes to a bogus "~" directory and fails with
    # Errno 2. cache_dir() is the primary fix; this is belt-and-suspenders
    # for any entry that hardcodes a path.
    return (os.path.expanduser(entry.path), entry.url or "", entry.sha256)


def _reset_model_cache(name: str) -> None:
    """Clear cached model state so next .get() re-initialises from
    disk. Used by both redownload and uninstall.

    Driven by ModelRegistry. ModelSingleton-backed entries point at
    `singleton.reset`; YuNet / SFace use module-global negative-cache
    resetters defined in their own scoring modules. Unknown names are
    silently ignored (no raise).
    """
    from bpp.scoring.model_base import ModelRegistry

    _ensure_model_modules_imported()
    entry = ModelRegistry.get(name)
    if entry is not None:
        entry.reset()


# One definition, shared with the download list (model_lifecycle).
from bpp.scoring.model_lifecycle import declared_legal_id  # noqa: E402


def reset_runtime_for_legal_id(legal_id: str) -> list[str]:
    """Make an acceptance change (revoke / accept) take effect without
    a restart: re-arm every memoized license gate for ``legal_id`` and
    call the reset hook of every ModelRegistry file that belongs to it
    (drops cached sessions / negative caches). Returns the file names
    reset. Generic — driven by the declared legal ids, not a per-model
    list."""
    from bpp.scoring.model_base import ModelRegistry
    from bpp.scoring.model_load_gate import reset_gates_for

    _ensure_model_modules_imported()
    reset_gates_for(legal_id)
    names: list[str] = []
    for entry in ModelRegistry.all():
        if declared_legal_id(entry) != legal_id:
            continue
        try:
            entry.reset()
            names.append(entry.name)
        except Exception:
            log.warning("Reset hook failed for %s", entry.name, exc_info=True)
    return names


def _resolve_legal_id(name: str) -> str:
    """The legal-registry id a redownload file belongs to.

    Read from what the scoring module declares when it registers the
    file (``legal_id``, or its ModelSingleton's ``registry_id``) — the
    only record of the link. Fails CLOSED: a file with no declared
    registry entry is refused, so a download can never run ungated.
    """
    from bpp.scoring.model_base import ModelRegistry

    _ensure_model_modules_imported()
    declared = declared_legal_id(ModelRegistry.get(name))
    if declared is None:
        log.error("Redownload refused for %s: it declares no model-registry entry", name)
        raise BppError(
            f"Download refused for {name!r}",
            user_message=(
                f"Can't download {name} from here: its license check isn't wired up. "
                "Use Settings → Models to review and accept the model instead."
            ),
            diagnostic_message=f"_resolve_legal_id: {name!r} declares no legal entry",
            name=name,
        )
    return declared


def _enforce_download_status_gate(name: str, legal_id: str | None = None) -> None:
    """Refuse the download if the corresponding legal-registry entry's
    ``status`` says new downloads are not allowed.

    Closes the gap where a signed remote-registry
    overlay can flip an entry to ``WITHDRAWN_NO_NEW_DOWNLOADS`` or
    ``LEGALLY_BLOCKED`` but the Redownload button still re-fetches
    silently. Without this gate the signed manifest's takedown power
    is cosmetic — the user can always click Redownload to get the
    file back.

    """
    if legal_id is None:
        legal_id = _resolve_legal_id(name)
    from bpp.registry import get_entry, status_behavior

    entry = get_entry(legal_id)
    if entry is None:
        return
    behavior = status_behavior(entry.status)
    if behavior.new_download_allowed:
        return
    log.info(
        "Download refused for %s (legal_id=%s) — status=%s",
        name,
        legal_id,
        entry.status.value,
    )
    raise BppError(
        f"Download refused for {name!r}",
        user_message=(
            f"This model is {entry.status.value.replace('_', ' ')} and new downloads are blocked."
        ),
        diagnostic_message=(
            f"_enforce_download_status_gate: legal entry "
            f"{legal_id} has status {entry.status.value} "
            "(new_download_allowed=False)"
        ),
        name=name,
        legal_entry_id=legal_id,
        status=entry.status.value,
    )


def _enforce_download_license_gate(name: str, legal_id: str) -> None:
    """Refuse BEFORE touching the existing file when the license gate
    would block the load (not accepted, accepted under an older text,
    or commercial mode without a rights assertion). ``download_file``
    re-checks, but by then the old copy would already be deleted."""
    from bpp.registry import ModelLoadBlockedError, enforce_load_policy_for

    try:
        enforce_load_policy_for(legal_id)
    except ModelLoadBlockedError as exc:
        log.info("Download refused for %s (legal_id=%s): %s", name, legal_id, exc)
        raise ConflictError(
            f"Download refused for {name!r}",
            user_message=(
                f"Accept the license for {name} in Settings → Models first "
                "(or re-accept it if its terms were updated), then download."
            ),
            diagnostic_message=f"_enforce_download_license_gate: {exc}",
            name=name,
            legal_entry_id=legal_id,
        ) from exc


def _redownload_model(name: str) -> dict:
    """Re-download a model synchronously. Returns updated model info."""
    path, url, sha, legal_id = _prepare_redownload(name)
    return _fetch_redownload(name, path, url, sha, legal_id)


def _prepare_redownload(name: str) -> tuple[str, str, str | None, str]:
    """Resolve the file and run every refusal check (fast, synchronous)."""
    path, url, sha = _model_path_url_sha(name)
    # Resolve the file name → legal entry id (fails closed when the
    # file declares none).
    legal_id = _resolve_legal_id(name)
    _enforce_download_status_gate(name, legal_id)
    _enforce_download_license_gate(name, legal_id)
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

    _reset_model_cache(name)

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

    _reset_model_cache(model_name)
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
