"""Tests never download models (``tests/conftest.py`` blocks the
network). A test about real weights skips when they aren't on this
machine, naming what it needs. CI opts in with
``BPP_TEST_ALLOW_DOWNLOADS=1`` (see conftest): there nothing skips and
the loaders download into the runner's model cache."""

from __future__ import annotations

import os

import pytest

ALLOW_DOWNLOADS_ENV = "BPP_TEST_ALLOW_DOWNLOADS"


def downloads_allowed() -> bool:
    """CI's opt-in: the suite may download (it keeps a model cache)."""
    return os.environ.get(ALLOW_DOWNLOADS_ENV) == "1"


#: The real face pipeline: detect → landmarks → embed.
FACE_PIPELINE_MODELS = (
    "YuNet (primary)",
    "SFace recognition",
    "BlazeFace full-range",
    "BlazeFace short-range",
    "FaceLandmarker",
    "HandLandmarker",
)

#: Everything a real ``score_photo`` / video scoring pass loads.
SCORING_MODELS = (
    "BlazeFace full-range",
    "FaceLandmarker",
    "HandLandmarker",
    "PoseLandmarker",
    "Selfie segmenter",
    "YuNet (primary)",
)

#: Everything a real analyze / import worker loads.
ANALYZE_MODELS = (*FACE_PIPELINE_MODELS, "Selfie segmenter", "CLIP visual", "PoseLandmarker")


def skip_unless_on_disk(*names: str) -> None:
    """Skip unless every named model file (a ``ModelRegistry`` name such as
    ``"CLIP text"``) is present — in the cache or as a bundled copy, the
    same rule the app uses. No-op when downloads are allowed."""
    if downloads_allowed():
        return
    from bpp.scoring import model_lifecycle
    from bpp.scoring.model_lifecycle import ModelRegistry, bundled_path_of, file_present

    model_lifecycle.ensure_builtin_model_files_registered()
    missing = []
    for n in names:
        entry = ModelRegistry.get(n)
        assert entry is not None, f"unknown model file name {n!r}"
        if not file_present(entry.path, bundled_path_of(entry)):
            missing.append(n)
    if missing:
        pytest.skip(f"needs {', '.join(missing)} on disk; tests never download models")


def skip_unless_face_pipeline() -> None:
    """Skip unless the real face pipeline can run."""
    skip_unless_on_disk(*FACE_PIPELINE_MODELS)


def skip_unless_scoring_models() -> None:
    """Skip unless a real scoring pass can run."""
    skip_unless_on_disk(*SCORING_MODELS)


def skip_unless_analyze_models() -> None:
    """Skip unless a real analyze / import worker can run."""
    skip_unless_on_disk(*ANALYZE_MODELS)


def skip_unless_buffalo_s() -> None:
    """Skip unless the buffalo_s catalog weights are on disk."""
    if downloads_allowed():
        return
    from bpp.scoring.face_embed_buffalo_s import is_on_disk

    if not is_on_disk():
        pytest.skip("needs the buffalo_s weights on disk; tests never download models")
