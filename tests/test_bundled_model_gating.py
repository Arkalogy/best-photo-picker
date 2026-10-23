"""Supporting models only load once their license is accepted.

Before the registry covered them, the MediaPipe models, the Haar
cascade and dlib's HOG detector loaded with no acceptance check at all.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from bpp.registry.attribution_bundle import confirm_attribution_bundle
from bpp.scoring.model_base import ModelSingleton

pytestmark = pytest.mark.no_preaccept_permissive

_ACK = {"permissive_attribution_acknowledged": True}


def _accept(*ids: str) -> None:
    confirm_attribution_bundle(
        ids, checkbox_responses=_ACK, accepted_at="2026-10-14T00:00:00+00:00"
    )


def _singleton(created: list) -> ModelSingleton:
    return ModelSingleton(
        name="test segmenter",
        model_path=None,
        model_url=None,
        create_fn=lambda _p: created.append(1) or object(),
        registry_id="mediapipe_selfie_segmenter",
    )


def test_unaccepted_singleton_does_not_load():
    created: list = []
    assert _singleton(created).get() is None
    assert created == []


def test_acceptance_takes_effect_without_a_reset():
    created: list = []
    s = _singleton(created)
    assert s.get() is None
    _accept("mediapipe_selfie_segmenter")
    assert s.get() is not None
    assert created == [1]


def test_haar_cascade_needs_acceptance():
    from bpp.scoring import face_fallback

    img = np.zeros((64, 64, 3), dtype=np.uint8)
    face_fallback._haar_gate.reset()  # a pass memoized by an earlier test
    with patch.object(face_fallback, "_get_cascade") as cascade:
        assert face_fallback._haar_detect(img) == []
        cascade.assert_not_called()
        _accept("opencv_haar_frontalface")
        cascade.return_value.detectMultiScale.return_value = ()
        face_fallback._haar_detect(img)
        cascade.assert_called_once()


def test_face_recognition_package_needs_the_dlib_acceptance():
    """Importing face_recognition loads dlib's restricted landmark model,
    so the loader (used by the HOG detector and the dlib embedder) must
    not import it until the dlib entry is accepted."""
    import sys

    from bpp.registry import confirm_acceptance, get_entry, prepare_acceptance
    from bpp.scoring import face_fallback, face_recognition_loader

    fake = object()
    face_recognition_loader._reset_for_tests()
    try:
        with patch.dict(sys.modules, {"face_recognition": fake}):
            assert face_recognition_loader.load_face_recognition() is None
            assert "not accepted" in face_recognition_loader.load_face_recognition_or_reason()[1]
            assert face_fallback._dlib_detect(np.zeros((8, 8, 3), np.uint8)) == []
            draft = prepare_acceptance(get_entry("dlib_face_recognition_resnet_v1"))
            confirm_acceptance(
                draft,
                checkbox_responses={cb: True for cb, _ in draft.required_checkboxes},
                accepted_at="2026-10-14T00:00:00+00:00",
            )
            assert face_recognition_loader.load_face_recognition() is fake
    finally:
        face_recognition_loader._reset_for_tests()
