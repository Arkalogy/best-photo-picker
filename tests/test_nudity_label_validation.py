"""The content filter must never score every photo 0 because the model's
class names are missing or don't match what BPP scores on."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bpp.scoring import nudity
from bpp.scoring.model_base import ModelIntegrityError

_LABELS = [
    "FEMALE_GENITALIA_COVERED",
    "FACE_FEMALE",
    "BUTTOCKS_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_BREAST_EXPOSED",
    "ANUS_EXPOSED",
    "FEET_EXPOSED",
    "BELLY_COVERED",
    "FEET_COVERED",
    "ARMPITS_COVERED",
    "ARMPITS_EXPOSED",
    "FACE_MALE",
    "BELLY_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "ANUS_COVERED",
    "FEMALE_BREAST_COVERED",
    "BUTTOCKS_COVERED",
]

_N_CLASSES = len(_LABELS)


def _fake_session(names_meta, n_classes=_N_CLASSES):
    meta = {} if names_meta is None else {"names": names_meta}
    sess = MagicMock()
    sess.get_inputs.return_value = [SimpleNamespace(name="images")]
    sess.get_outputs.return_value = [SimpleNamespace(shape=[1, 4 + n_classes, 2100])]
    sess.get_modelmeta.return_value = SimpleNamespace(custom_metadata_map=meta)
    return sess


def _build(names_meta, n_classes=_N_CLASSES):
    with patch("onnxruntime.InferenceSession", return_value=_fake_session(names_meta, n_classes)):
        return nudity._NudeNetSession("/fake/320n.onnx")


def _names(labels):
    return repr(dict(enumerate(labels)))


def test_valid_metadata_loads():
    assert _build(_names(_LABELS)).labels == _LABELS


def test_missing_names_fails_loudly():
    with pytest.raises(ModelIntegrityError, match="class names"):
        _build(None)


def test_missing_scored_category_fails_loudly():
    labels = [lbl for lbl in _LABELS if lbl != "ANUS_EXPOSED"] + ["SOMETHING_ELSE"]
    with pytest.raises(ModelIntegrityError, match="ANUS_EXPOSED"):
        _build(_names(labels))


def test_count_mismatch_with_model_output_fails_loudly():
    with pytest.raises(ModelIntegrityError, match="18"):
        _build(_names(_LABELS), n_classes=17)


def test_first_scan_failure_is_a_warning_then_debug(caplog):
    detector = MagicMock()
    detector.label_scores.side_effect = RuntimeError("bad image")
    nudity._reset_scan_failure_log_for_tests()
    with (
        patch.object(nudity, "_get_detector", return_value=detector),
        caplog.at_level(logging.DEBUG, logger=nudity.log.name),
    ):
        assert nudity.score_nudity("/a.jpg") == 0.0
        assert nudity.score_nudity("/b.jpg") == 0.0
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "/a.jpg" in warnings[0].getMessage()
