"""The face pipeline binds detectors through the registry alone.

A private ``_DETECTORS`` dict used to hold a second name → function map
next to ``FaceDetectorRegistry``; a replacement registered through the
public API (a plugin, a test) changed the registry but not what the
pipeline ran.
"""

from __future__ import annotations

import numpy as np

from tests._detector_stubs import stub_detectors


def test_no_private_detector_map_remains():
    from bpp.scoring import face, face_pipeline

    assert not hasattr(face_pipeline, "_DETECTORS")
    assert not hasattr(face, "_DETECTORS")


def test_a_detector_registered_through_the_registry_is_what_runs():
    from bpp.scoring.face_pipeline import _collect_builtin_detections

    img = np.zeros((300, 300, 3), dtype=np.uint8)
    ran: list[str] = []
    with stub_detectors(
        {
            "yunet": lambda im, c: ran.append("yunet") or [(10, 10, 40, 40, 0.99)],
            "mediapipe_sr": lambda im, c: ran.append("mediapipe_sr") or [],
            "blazeface_fr": lambda im, c: ran.append("blazeface_fr") or [],
            "dlib": lambda im, c: ran.append("dlib") or [],
        }
    ):
        faces, found = _collect_builtin_detections(img, 0.5, {"model_scrfd": False})
    assert found is True
    assert faces == [(10, 10, 40, 40, 0.99)]
    assert "yunet" in ran and "mediapipe_sr" in ran
