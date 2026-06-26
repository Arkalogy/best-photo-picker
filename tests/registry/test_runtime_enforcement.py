"""Runtime enforcement gate — proves the click-through dialog is
load-bearing at inference time, not just an audit-log artifact.

Until ``enforce_load_policy_for`` landed, the Batch 4 click-through
dialog surfaces (CLI + web UI) recorded an acceptance row but did NOT
gate the model file from loading — a user could ``pip install
bppicker[nudity]`` and run NudeNet without ever seeing the dialog.

These tests pin the gate at the three loader entry points:

* ``bpp.scoring.pets._get_session``          (YOLOv11n pet detector, AGPL-3.0)
* ``bpp.scoring.nudity._get_detector``       (NudeNet, GPL-3.0)
* ``bpp.ai.inpainting._get_model``           (LaMa, research weights)

The actual model file is not touched — the tests assert the gate
fires BEFORE the singleton's ``.get()`` is reached.

Without acceptance row → gate raises.
With acceptance row → gate returns (and the singleton's get is
invoked; we mock its return value to keep the tests fast).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bpp.registry import (
    BYOM_DISCLAIMER_VERSION,  # noqa: F401 — kept for symmetry in BYOM tests
    CANONICAL_DISCLAIMER_VERSION,
    ModelLoadBlockedError,
    UseContext,
    confirm_acceptance,
    enforce_load_policy_for,
    get_entry,
    prepare_acceptance,
    set_use_context,
    utc_now_iso,
)


def _accept(model_id: str) -> None:
    """Record an acceptance row for ``model_id`` under personal use,
    all 4 canonical checkboxes ticked."""
    set_use_context(UseContext.PERSONAL, set_via="test")
    entry = get_entry(model_id)
    assert entry is not None
    draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
    responses = {cb_id: True for cb_id, _ in draft.required_checkboxes}
    confirm_acceptance(
        draft,
        checkbox_responses=responses,
        accepted_at=utc_now_iso(),
        separate_rights_asserted=False,
        source_of_rights_note="",
    )


# ── Helper: gate is permissive for permissive entries ──


@pytest.mark.parametrize(
    "permissive_id",
    [
        # MIT-permissive entries only — no acceptance required, gate
        # passes through. Attribution-permissive entries (SFace,
        # YuNet, dlib) ALSO need acceptance now, so they belong with
        # the restricted gate-blocks-without-acceptance test below
        # rather than here.
        "insightface_scrfd_25g",
        "openai_clip_vit_b32_onnx",
    ],
)
def test_gate_passes_through_for_mit_entries(
    permissive_id: str,
) -> None:
    """MIT entries don't need an acceptance row — the gate
    short-circuits to ALLOW immediately. Option B from the
    legal-posture rollout draws the line at MIT: everything else
    requires explicit acknowledgment."""
    enforce_load_policy_for(permissive_id)  # must not raise


# ── Restricted entries fail-closed without acceptance ──


@pytest.mark.parametrize(
    "restricted_id",
    [
        "ultralytics_yolov11n_pets",
        "nudenet_320n",
        "lama_inpaint_research",
        "insightface_buffalo_s",
    ],
)
def test_gate_blocks_restricted_without_acceptance(
    restricted_id: str,
) -> None:
    """Every restricted entry must fail-closed before its loader can
    open the model file. Adds a regression test for the legal-posture
    paper-vs-runtime gap closed by this helper."""
    set_use_context(UseContext.PERSONAL, set_via="test")
    with pytest.raises(ModelLoadBlockedError):
        enforce_load_policy_for(restricted_id)


# ── Restricted entries allow load after acceptance ──


@pytest.mark.parametrize(
    "restricted_id",
    [
        "ultralytics_yolov11n_pets",
        "nudenet_320n",
        "lama_inpaint_research",
    ],
)
def test_gate_allows_restricted_after_acceptance(
    restricted_id: str,
) -> None:
    _accept(restricted_id)
    enforce_load_policy_for(restricted_id)  # must not raise


# ── Loader entry points wired ──


class TestPetsLoaderGate:
    """The YOLOv11n loader (bpp.scoring.pets._get_session) must call
    enforce_load_policy_for BEFORE the ModelSingleton's get() runs.
    Without acceptance, the gate raises and the singleton stays
    untouched."""

    def test_get_session_raises_without_acceptance(self) -> None:
        set_use_context(UseContext.PERSONAL, set_via="test")
        # Import after the registry isolation fixture has fired so
        # the bundled baseline is in place.
        from bpp.scoring import pets

        with pytest.raises(ModelLoadBlockedError):
            pets._get_session()

    def test_get_session_passes_with_acceptance(self) -> None:
        _accept("ultralytics_yolov11n_pets")
        from bpp.scoring import pets

        # Mock the singleton's get() so we don't actually touch the
        # ONNX file in the test environment.
        with patch.object(pets._yolo, "get", return_value="<mock-session>"):
            assert pets._get_session() == "<mock-session>"


class TestNudityLoaderGate:
    def test_get_detector_raises_without_acceptance(self) -> None:
        set_use_context(UseContext.PERSONAL, set_via="test")
        from bpp.scoring import nudity

        with pytest.raises(ModelLoadBlockedError):
            nudity._get_detector()

    def test_get_detector_passes_with_acceptance(self) -> None:
        _accept("nudenet_320n")
        from bpp.scoring import nudity

        with patch.object(nudity._nudenet, "get", return_value="<mock>"):
            assert nudity._get_detector() == "<mock>"


class TestLaMaLoaderGate:
    def test_get_model_raises_without_acceptance(self) -> None:
        set_use_context(UseContext.PERSONAL, set_via="test")
        from bpp.ai import inpainting

        with pytest.raises(ModelLoadBlockedError):
            inpainting._get_model()

    def test_get_model_passes_with_acceptance(self) -> None:
        _accept("lama_inpaint_research")
        from bpp.ai import inpainting

        with patch.object(inpainting._LAMA, "get", return_value="<mock>"):
            assert inpainting._get_model() == "<mock>"


# ── Commercial-mode gate fires even after acceptance ──


def test_gate_blocks_commercial_mode_without_separate_rights() -> None:
    """User accepted under personal use, then switched to commercial.
    The hard-block fires until they re-accept with the
    separate-rights box checked. This is the Item 16 gate that the
    policy review specifically wanted enforced at inference time."""
    _accept("nudenet_320n")
    set_use_context(UseContext.COMMERCIAL, set_via="test")
    with pytest.raises(ModelLoadBlockedError) as exc_info:
        enforce_load_policy_for("nudenet_320n")
    assert "commercial" in str(exc_info.value).lower()


def test_unknown_model_id_raises_value_error() -> None:
    """A typo in the loader's model_id argument is a programmer
    error — surface it explicitly rather than silently allowing the
    load."""
    with pytest.raises(ValueError, match="no registry entry with id"):
        enforce_load_policy_for("not_a_real_model_id")


# Reference: this constant is exported to ensure BYOM acceptance flow
# stays aligned with the gate. Loaders for user-supplied weights would
# call enforce_load_policy_for(byom_entry_id) on the same code path.
assert CANONICAL_DISCLAIMER_VERSION  # silence unused-export lint
