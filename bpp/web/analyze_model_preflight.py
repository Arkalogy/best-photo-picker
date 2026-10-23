"""Model preflight downloads for the analyze worker.

Extracted from :mod:`bpp.web.analyze_worker` as part of the 500-LOC
cap split. Before Phase 1 (scoring) starts, the worker checks every
ML model the pipeline will use and downloads any that are missing.
Surfacing this as its own phase keeps the user out of a silent stall
when, e.g., SCRFD or the pet detector first needs ~3 MB or ~6 MB of
network IO; the progress emissions feed the SSE stream so the UI can
display "Downloading SCRFD…" etc.

The caller threads its ``self._emit`` callback in via ``emit``; this
module does not own any worker state.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from bpp.utils.logging import get_logger

log = get_logger(__name__)

EmitFn = Callable[[dict[str, Any]], None]


def preflight_run_config(emit: EmitFn, config: dict[str, Any]) -> dict[str, Any]:
    """Run the preflight for an analysis with *config*; return the config
    to score with. A switched-on model that couldn't be fetched is off for
    this run (its warning already said so), so scoring doesn't silently
    try — and stall on — the same download again."""
    from bpp.constants import MODEL_TOGGLE_KEYS
    from bpp.utils.download import announce_downloads

    toggles = {k: config.get(k, True) for k in MODEL_TOGGLE_KEYS}
    with announce_downloads(lambda message: emit({"type": "status", "message": message})):
        failed = preflight_models(emit, toggles)
    return {**config, **dict.fromkeys(failed, False)}


#: Run-only switches for models that have no Settings toggle: when the
#: preflight turns one off, the run's subprocesses also refuse to try
#: that download again (see apply_run_download_skips).
_RUN_ONLY_DOWNLOADS = {"model_yunet": "opencv_yunet"}


def apply_run_download_skips(config: dict[str, Any]) -> None:
    """In a run's subprocess: don't retry downloads the preflight already
    saw fail (including in that subprocess's own worker pool)."""
    from bpp.utils.download import skip_downloads_in_this_process

    skip_downloads_in_this_process(
        [rid for key, rid in _RUN_ONLY_DOWNLOADS.items() if config.get(key, True) is False]
    )


def preflight_models(emit: EmitFn, model_toggles: dict[str, Any] | None = None) -> set[str]:
    """Pre-download every model the analyze pipeline will use.

    Emits ``phase`` / ``status`` / ``warning`` events on the SSE
    stream so the user sees concrete progress instead of a silent
    stall during what looks like "scoring" but is really model IO.
    Models switched off in *model_toggles* are skipped.

    Returns the toggle keys of models that couldn't be fetched (see
    :func:`preflight_run_config`).
    """
    emit(
        {
            "type": "phase",
            "phase": "models",
            "label": "Checking ML models",
            "step": 2,
            "of": 5,
        }
    )
    _report_unaccepted(emit)
    return _download_accepted_models(emit, model_toggles or {})


def _report_unaccepted(emit: EmitFn) -> None:
    """One notice for every built-in model still waiting on its license,
    instead of a "download failed" / "unavailable" line per model."""
    from bpp.registry.attribution_bundle import pending_attribution_entries

    pending = pending_attribution_entries()
    if pending:
        emit(
            {
                "type": "warning",
                "message": (
                    f"{len(pending)} built-in models are off until you accept their "
                    "licenses, so faces, people, expressions and composition won't be "
                    "scored. Accept them in the dialog before analyzing, or in "
                    "Settings → Models, or with `bpp model accept-attribution`."
                ),
            }
        )


def _download_accepted_models(emit: EmitFn, model_toggles: dict[str, Any]) -> set[str]:
    """Fetch the weights the run will use, with a visible status for
    each. Models whose license isn't accepted are skipped (the gate
    refuses their download). Returns the toggle keys that failed."""
    failed = _download_builtin_models(emit, model_toggles)
    if model_toggles.get("model_nudity", True) and not _download_content_filter(emit):
        failed.add("model_nudity")
    return failed


def _download_content_filter(emit: EmitFn) -> bool:
    """Fetch NudeNet before scoring (not silently mid-analysis), only
    when it can run and its license is accepted. False if it failed."""
    from bpp.registry.policy import is_load_blocked
    from bpp.scoring import nudity

    if not nudity.is_available() or is_load_blocked(nudity.REGISTRY_ID):
        return True
    # Always run ensure: for a cached file it's just a checksum (no
    # network), and it replaces a damaged file before scoring would fail.
    # The loader announces any download it makes (preflight_run_config).
    try:
        nudity.ensure_nudenet_model()
    except Exception as exc:
        from bpp.utils.download import plain_reason

        log.warning("Content filter model unavailable", exc_info=True)
        emit(
            {"type": "warning", "message": f"Content filter model unavailable: {plain_reason(exc)}"}
        )
        return False
    return True


def _download_builtin_models(emit: EmitFn, model_toggles: dict[str, Any]) -> set[str]:
    """Face, segmentation, pose and pet models. Returns the toggle keys
    of switched-on models that couldn't be fetched."""
    from bpp.scoring import face
    from bpp.scoring.face_embed import ensure_sface_model
    from bpp.scoring.face_mediapipe import ensure_mediapipe_models
    from bpp.scoring.pose import ensure_pose_model
    from bpp.scoring.segmentation import ensure_segmenter_model

    def on(key: str) -> bool:
        return bool(model_toggles.get(key, True))

    failed: set[str] = set()
    emit({"type": "status", "message": "Checking face detection models…"})
    if on("model_scrfd") and not _download_scrfd(emit):
        failed.add("model_scrfd")

    # A failed always-on detector has no Settings switch, but is off for
    # this run all the same (see preflight_run_config).
    for key, warn in face.core_face_model_failures().items():
        emit({"type": "warning", "message": warn})
        failed.add(key)
    for warn in [*ensure_mediapipe_models(), *ensure_sface_model()]:
        emit({"type": "warning", "message": warn})
    for key, model, warning in face.OPTIONAL_FACE_MODELS:
        if on(key) and model.ensure_model() is None and not model.license_blocked():
            emit({"type": "warning", "message": warning})
            failed.add(key)

    for key, ensure in (
        ("model_segmentation", ensure_segmenter_model),
        ("model_pose", ensure_pose_model),
    ):
        if not on(key):
            continue
        warnings = ensure()
        for warn in warnings:
            emit({"type": "warning", "message": warn})
        if warnings:
            failed.add(key)

    if on("model_pets") and not _download_pets(emit):
        failed.add("model_pets")
    return failed


def _download_scrfd(emit: EmitFn) -> bool:
    """SCRFD (optional, research-only) — download only once accepted.
    False if an accepted, runnable SCRFD couldn't be fetched."""
    from bpp.registry.labels import license_label_for
    from bpp.scoring.face import _scrfd_model
    from bpp.scoring.face_scrfd import scrfd_accepted

    if not scrfd_accepted():
        emit(
            {
                "type": "status",
                "message": "SCRFD face detector not enabled ("
                + license_label_for(_scrfd_model.registry_id)
                + " license; accept it in Settings → Models to use it)",
            }
        )
    elif not _scrfd_model.is_available():
        emit({"type": "warning", "message": "SCRFD requires onnxruntime"})
    elif _scrfd_model.ensure_model() is None:
        # (The loader announces the download itself; a cached file is
        # just checked, and replaced if damaged.)
        emit(
            {
                "type": "warning",
                "message": "SCRFD download failed — using fallback detectors",
            }
        )
        return False
    return True


def _download_pets(emit: EmitFn) -> bool:
    """Pet detector, when it can run and its license is accepted.
    False if the download failed."""
    from bpp.registry.policy import is_load_blocked
    from bpp.scoring.pets import _get_model_path as _pet_model_path
    from bpp.scoring.pets import ensure_model as ensure_pet_model
    from bpp.scoring.pets import is_available as pets_available

    if (
        not pets_available()
        or os.path.exists(_pet_model_path())
        or is_load_blocked("ultralytics_yolov11n_pets")
    ):
        return True
    emit({"type": "status", "message": "Downloading pet detection model…"})
    try:
        ensure_pet_model()
    except Exception:
        log.warning("Pet model download failed, pet detection disabled", exc_info=True)
        emit({"type": "warning", "message": "Pet detection model unavailable"})
        return False
    return True
