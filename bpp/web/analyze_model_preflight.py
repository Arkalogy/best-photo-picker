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


def preflight_models(emit: EmitFn) -> None:
    """Pre-download every model the analyze pipeline will use.

    Emits ``phase`` / ``status`` / ``warning`` events on the SSE
    stream so the user sees concrete progress instead of a silent
    stall during what looks like "scoring" but is really model IO.
    """
    from bpp.scoring.face import _scrfd_model, ensure_face_models
    from bpp.scoring.face_embed import ensure_sface_model
    from bpp.scoring.pets import _get_model_path as _pet_model_path
    from bpp.scoring.pets import ensure_model as ensure_pet_model
    from bpp.scoring.pets import is_available as pets_available
    from bpp.scoring.pose import ensure_pose_model
    from bpp.scoring.segmentation import ensure_segmenter_model

    emit(
        {
            "type": "phase",
            "phase": "models",
            "label": "Checking ML models",
            "step": 2,
            "of": 5,
        }
    )
    emit({"type": "status", "message": "Checking face detection models…"})

    # SCRFD (primary detector) — download if missing
    if not _scrfd_model.is_available():
        emit({"type": "warning", "message": "SCRFD requires onnxruntime"})
    elif _scrfd_model.model_path and not _scrfd_model.model_path.exists():
        emit({"type": "status", "message": "Downloading SCRFD face detector (3 MB)…"})
        path = _scrfd_model.ensure_model()
        if path:
            emit({"type": "status", "message": "SCRFD face detector ready"})
        else:
            emit(
                {
                    "type": "warning",
                    "message": "SCRFD download failed — using fallback detectors",
                }
            )

    for warn in ensure_face_models():
        emit({"type": "warning", "message": warn})
    for warn in ensure_sface_model():
        emit({"type": "warning", "message": warn})

    for warn in ensure_segmenter_model():
        emit({"type": "warning", "message": warn})
    for warn in ensure_pose_model():
        emit({"type": "warning", "message": warn})

    if pets_available() and not os.path.exists(_pet_model_path()):
        emit({"type": "status", "message": "Downloading pet detection model…"})
        try:
            ensure_pet_model()
        except Exception:
            log.warning("Pet model download failed, pet detection disabled", exc_info=True)
            emit({"type": "warning", "message": "Pet detection model unavailable"})
