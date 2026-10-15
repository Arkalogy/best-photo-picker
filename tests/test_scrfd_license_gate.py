"""SCRFD is research-only: it must not run or download until accepted."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from bpp.registry import ModelLoadBlockedError
from bpp.registry.policy import ModelLoadDecision, PolicyResult
from bpp.scoring import face_scrfd


def test_detect_returns_nothing_and_never_loads_when_not_accepted():
    gate = MagicMock()
    gate.enforce.side_effect = ModelLoadBlockedError(
        PolicyResult(
            decision=ModelLoadDecision.BLOCKED_NEEDS_ACK,
            reason="test",
            entry_id="insightface_scrfd_25g",
        )
    )
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    with (
        patch.object(face_scrfd, "_scrfd_gate", gate),
        patch.object(face_scrfd._scrfd_model, "get") as get,
    ):
        assert face_scrfd.detect_faces_scrfd(img) == []
    get.assert_not_called()


def test_detect_loads_model_once_accepted():
    img = np.zeros((64, 64, 3), dtype=np.uint8)
    with (
        patch.object(face_scrfd, "_scrfd_gate", MagicMock()),
        patch.object(face_scrfd._scrfd_model, "get", return_value=None) as get,
    ):
        assert face_scrfd.detect_faces_scrfd(img) == []
    get.assert_called_once()


def test_registry_classifies_scrfd_as_restricted_and_not_default():
    from bpp.registry import get_default_for_kind, get_entry

    entry = get_entry("insightface_scrfd_25g")
    assert entry.requires_explicit_ack
    assert entry.commercial_use_restriction_known
    assert not entry.bppicker_commercial_default_allowed
    assert not entry.default_for_kind
    assert get_default_for_kind("face_detector").id == "opencv_yunet"


def test_preflight_does_not_download_scrfd_when_not_accepted():
    from bpp.web import analyze_model_preflight as pf

    events = []
    with (
        patch("bpp.scoring.face_scrfd.scrfd_accepted", return_value=False),
        patch.object(face_scrfd._scrfd_model, "ensure_model") as ensure,
        patch("bpp.scoring.face.core_face_model_failures", return_value={}),
        patch("bpp.scoring.face.OPTIONAL_FACE_MODELS", ()),
        patch("bpp.scoring.face_mediapipe.ensure_mediapipe_models", return_value=[]),
        patch("bpp.web.analyze_model_preflight._download_content_filter", return_value=True),
        patch("bpp.utils.download.download_file", side_effect=AssertionError("network")),
        patch("bpp.scoring.face_embed.ensure_sface_model", return_value=[]),
        patch("bpp.scoring.segmentation.ensure_segmenter_model", return_value=[]),
        patch("bpp.scoring.pose.ensure_pose_model", return_value=[]),
        patch("bpp.scoring.pets.is_available", return_value=False),
    ):
        pf.preflight_models(events.append)
    ensure.assert_not_called()
    assert any("SCRFD face detector not enabled" in str(e.get("message", "")) for e in events)
