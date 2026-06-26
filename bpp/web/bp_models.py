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

import os

from flask import Blueprint, Response, jsonify, request

from bpp.constants import MODEL_TOGGLE_KEYS
from bpp.errors import BppError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("models", __name__)


@bp.get("/api/v1/models")
def api_models() -> tuple[Response, int]:
    """Return ML model status grouped by feature.

    M12.c: the per-feature dicts and library/file checks live in
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
    this could DoS-by-bandwidth and (worse) racing against C-02's
    owner-only PyPI install path opens the same supply-chain
    surface."""
    data = request.get_json(silent=True) or {}
    model_name = data.get("name", "")
    if not model_name:
        raise ValidationError("name required", field="name")

    try:
        result = _redownload_model(model_name)
        return jsonify(result), 200
    except ValueError as e:
        raise ValidationError(
            "Invalid model name",
            field="name",
            value=model_name,
        ) from e
    except Exception as e:
        raise BppError(
            "Failed to redownload model",
            # LOCAL_APP-only endpoint — the caller is the machine owner, so
            # surfacing the concrete reason (network error, integrity
            # mismatch, disk path) is useful, not a leak. A bare "Failed"
            # left the user with no idea what went wrong.
            user_message=f"Couldn't download {model_name}: {e!s}",
            diagnostic_message=f"redownload error for {model_name}: {e!s}",
            name=model_name,
        ) from e


def _ensure_model_modules_imported() -> None:
    """Force-import every scoring module that registers a ModelEntry.

    The ModelRegistry is populated by side effects of importing each
    scoring module. The `_model_path_url_sha` and `_reset_model_cache`
    callers might run before any scoring code has been touched (e.g.
    a fresh server hits Settings → ML Models before any photo is
    scored), so we ensure the registry is fully populated here.
    """
    import importlib

    for mod in (
        "bpp.scoring.face",
        "bpp.scoring.face_blazeface_fr",
        "bpp.scoring.face_embed",
        "bpp.scoring.face_expression",
        "bpp.scoring.face_hand_filter",
        "bpp.scoring.face_scrfd",
        "bpp.scoring.pose",
        "bpp.scoring.segmentation",
        "bpp.scoring.pets",
        "bpp.scoring.clip_embed",
        "bpp.scoring.clip_tokenizer",
    ):
        importlib.import_module(mod)


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


#: Legacy scoring-registry file names → ``bpp.registry`` legal entry
#: ids. The redownload endpoint receives the file-level name (e.g.
#: ``"SFace recognition"``) because that's what the legacy
#: ``ModelRegistry`` uses; the status gate operates on the legal
#: registry's ``ModelEntry.id`` (e.g. ``"sface_yunet"``). One legal
#: entry may map to multiple files (CLIP visual + CLIP text both
#: belong to ``openai_clip_vit_b32_onnx``). Files with no legal entry
#: are ancillary models with no licensing concern (BlazeFace,
#: segmenter, etc.) — they fall through the gate.
_LEGAL_ENTRY_ID_FOR_FILE_NAME: dict[str, str] = {
    "SFace recognition": "sface_yunet",
    "YuNet (primary)": "opencv_yunet",
    "SCRFD 2.5g": "insightface_scrfd_25g",
    "CLIP visual": "openai_clip_vit_b32_onnx",
    "CLIP text": "openai_clip_vit_b32_onnx",
    "YOLO pet detector": "ultralytics_yolov11n_pets",
}


def _enforce_download_status_gate(name: str) -> None:
    """Refuse the download if the corresponding legal-registry entry's
    ``status`` says new downloads are not allowed.

    Closes the item-12 + item-20 gap where a signed remote-registry
    overlay can flip an entry to ``WITHDRAWN_NO_NEW_DOWNLOADS`` or
    ``LEGALLY_BLOCKED`` but the Redownload button still re-fetches
    silently. Without this gate the signed manifest's takedown power
    is cosmetic — the user can always click Redownload to get the
    file back.

    Files with no legal-registry counterpart fall through (ancillary
    detectors / segmenters that don't carry a licensing concern).
    """
    legal_id = _LEGAL_ENTRY_ID_FOR_FILE_NAME.get(name)
    if legal_id is None:
        return
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
            f"This model is {entry.status.value.replace('_', ' ')} "
            "and cannot be downloaded. Existing local copies may "
            "still work depending on the upstream takedown; new "
            "downloads are blocked at the registry level."
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


def _redownload_model(name: str) -> dict:
    """Delete and re-download a model. Returns updated model info."""
    from bpp.utils.download import download_file

    _enforce_download_status_gate(name)
    path, url, sha = _model_path_url_sha(name)
    log.info("Re-downloading model %s from %s", name, url)

    if os.path.exists(path):
        os.remove(path)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    # Resolve the file-name → legal entry id; ancillary models (no
    # licensing concern, no entry in the legal registry) explicitly
    # pass registry_id=None to opt out of the policy gate.
    legal_id = _LEGAL_ENTRY_ID_FOR_FILE_NAME.get(name)
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

    bytes_freed = 0
    try:
        if os.path.exists(path):
            bytes_freed = os.path.getsize(path)
            os.remove(path)
        # Also clear any stray .tmp from an interrupted download
        tmp = path + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
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
