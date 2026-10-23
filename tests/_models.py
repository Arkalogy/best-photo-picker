"""Tests never download models (``tests/conftest.py`` blocks the
network). A test about real weights skips when they aren't on this
machine, naming what it needs. CI opts in with
``BPP_TEST_ALLOW_DOWNLOADS=1`` (see conftest): there nothing skips and
the loaders download into the runner's model cache (buffalo_s, whose
loader never downloads, is accepted and fetched by its helper)."""

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


def accept_buffalo_s() -> None:
    """Record the click-through acceptance buffalo_s needs, in the
    test's own acceptance log (the conftest points the log at a temp
    file), so its gate lets a test download and load it."""
    from bpp.registry import (
        UseContext,
        confirm_acceptance,
        get_entry,
        prepare_acceptance,
        set_use_context,
        utc_now_iso,
    )

    set_use_context(UseContext.PERSONAL, set_via="test")
    entry = get_entry("insightface_buffalo_s")
    draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
    confirm_acceptance(
        draft,
        checkbox_responses={cb_id: True for cb_id, _ in draft.required_checkboxes},
        accepted_at=utc_now_iso(),
        separate_rights_asserted=False,
        source_of_rights_note="",
    )


def _buffalo_s_weights_whole() -> bool:
    """The extracted buffalo_s file is on disk with the pinned hash
    (checked here without touching the file: the app's own verifier
    deletes a bad one, which a test must never do to a real cache)."""
    import hashlib

    from bpp.scoring.face_embed_buffalo_s import W600K_MBF_SHA256, _extracted_model_path

    path = _extracted_model_path()
    if not path.exists():
        return False
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest() == W600K_MBF_SHA256.lower().strip()


def skip_unless_buffalo_s() -> None:
    """Skip unless the buffalo_s catalog weights are on disk and whole.
    With downloads allowed (CI) they are accepted and fetched here,
    because the code under test deliberately never downloads them; a
    fetch that fails there is a failure, named as such."""
    if _buffalo_s_weights_whole():
        return
    if not downloads_allowed():
        pytest.skip("needs the buffalo_s weights on disk; tests never download models")
    from bpp.scoring.face_embed_buffalo_s import ensure_buffalo_s_model

    try:
        accept_buffalo_s()
        ensure_buffalo_s_model()
    except Exception as exc:
        pytest.fail(f"buffalo_s fetch failed (test setup, not the subject): {exc}")
