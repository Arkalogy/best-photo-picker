"""A MediaPipe inference failure is logged, not swallowed.

Expression, pose, segmentation and the hand filter each returned their
neutral value when ``detect`` raised, with nothing in the log. A library
or model problem then showed up only as "every photo scores the same".
The first failure per detector is a WARNING with the traceback; later
ones are DEBUG so a broken detector doesn't flood a 30k-photo run.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

pytest.importorskip("mediapipe")


class _Broken:
    def detect(self, *_a, **_k):
        raise RuntimeError("graph failed to run")

    segment = detect


@pytest.fixture(autouse=True)
def _fresh_once(monkeypatch):
    from bpp.scoring import inference_log

    inference_log._reset_for_tests()


_IMAGE = np.zeros((16, 16, 3), dtype=np.uint8)


@pytest.mark.parametrize(
    "module, getter, call, neutral",
    [
        ("bpp.scoring.face_expression", "_get_landmarker", "_score_expression", 0.5),
        ("bpp.scoring.face_hand_filter", "_get_hand_detector", "_detect_hand_bboxes", []),
        ("bpp.scoring.segmentation", "_get_segmenter", "segment_subject", None),
    ],
)
def test_first_failure_warns_then_goes_quiet(monkeypatch, caplog, module, getter, call, neutral):
    import importlib

    mod = importlib.import_module(module)
    monkeypatch.setattr(mod, getter, lambda: _Broken())
    caplog.set_level(logging.DEBUG, logger="bpp")
    fn = getattr(mod, call)
    assert fn(_IMAGE) == neutral
    assert fn(_IMAGE) == neutral
    levels = [r.levelno for r in caplog.records if "graph failed to run" in (r.exc_text or "")]
    assert levels[:1] == [logging.WARNING], caplog.text
    assert all(lvl == logging.DEBUG for lvl in levels[1:]), caplog.text
    assert len(levels) == 2


def test_pose_failure_is_logged_too(monkeypatch, caplog):
    from bpp.scoring import pose

    monkeypatch.setattr(pose._pose_model, "get", lambda: _Broken())
    caplog.set_level(logging.DEBUG, logger="bpp")
    assert pose.detect_poses(_IMAGE) == []
    assert any(
        r.levelno == logging.WARNING and "graph failed to run" in (r.exc_text or "")
        for r in caplog.records
    ), caplog.text
