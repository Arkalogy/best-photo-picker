"""The content filter keeps reading each photo itself at full size, so its
input (and the 0.7 threshold tuned on it) doesn't depend on the "Max
scoring resolution" setting. Camera RAW photos get a real scan, and an
unreadable photo is reported once per process, not once per photo."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from bpp.scoring import nudity


def _session():
    session = nudity._NudeNetSession.__new__(nudity._NudeNetSession)
    session.input_name = "images"
    session.labels = ["FACE_FEMALE"]
    session.session = MagicMock()
    session.session.run.return_value = [np.zeros((1, 5, 2100), np.float32)]
    return session


def test_the_pipeline_scorer_hands_over_the_file_not_the_downscaled_image():
    from bpp.scoring.registry import _SCORER_REGISTRY, _wire_optional_scorers

    _wire_optional_scorers()
    detector = MagicMock()
    detector.label_scores.return_value = {"MALE_GENITALIA_EXPOSED": 0.8}
    with patch.object(nudity, "_get_detector", return_value=detector):
        out = _SCORER_REGISTRY["nudity"].score_fn(
            np.zeros((256, 171, 3), np.uint8), "/photos/a.jpg", {"max_long_side": 256}
        )
    assert out == {"nudity_score": 0.8}
    assert detector.label_scores.call_args.args == ("/photos/a.jpg",)


def test_the_filter_reads_full_size_through_the_shared_reader():
    full = np.zeros((3024, 4032, 3), np.uint8)
    session = _session()
    with patch("bpp.scoring.aggregate.load_image_for_scoring", return_value=full) as read:
        session.label_scores("/photos/a.CR2")
    assert read.call_args.args == ("/photos/a.CR2",)
    assert read.call_args.kwargs == {}  # full size, RAW included


def test_unreadable_photos_are_reported_once_per_process():
    nudity._reset_scan_failure_log_for_tests()
    session = _session()
    with (
        patch(
            "bpp.scoring.aggregate.load_image_for_scoring",
            side_effect=ValueError("truncated RAW data"),
        ),
        patch.object(nudity, "_get_detector", return_value=session),
        patch.object(nudity, "log") as log,
    ):
        assert nudity.score_nudity("/photos/a.CR2") == 0.0
        assert nudity.score_nudity("/photos/b.CR2") == 0.0
    assert log.warning.call_count == 1
    assert "/photos/a.CR2" in log.warning.call_args.args
    assert "truncated RAW data" in str(log.warning.call_args.args)  # the real cause
    session.session.run.assert_not_called()
    nudity._reset_scan_failure_log_for_tests()


def test_missing_raw_support_adds_no_filter_warning():
    from bpp.utils.raw import RawSupportMissing

    session = _session()
    with (
        patch("bpp.scoring.aggregate.load_image_for_scoring", side_effect=RawSupportMissing("x")),
        patch.object(nudity, "log") as log,
    ):
        assert session.label_scores("/photos/a.CR2") == {}
    log.warning.assert_not_called()  # the RAW module already said it, once
