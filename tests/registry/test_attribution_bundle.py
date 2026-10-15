"""One combined acceptance step for every attribution-licensed model."""

from __future__ import annotations

import pytest

from bpp.registry import get_entry
from bpp.registry.acceptance import AcceptanceError, is_acceptance_valid_for
from bpp.registry.acceptance_log import iter_rows
from bpp.registry.attribution_bundle import (
    confirm_attribution_bundle,
    is_attribution_entry,
    pending_attribution_entries,
    prepare_attribution_bundle,
)

pytestmark = pytest.mark.no_preaccept_permissive

_ACK = {"permissive_attribution_acknowledged": True}

#: Every model a default analysis runs that carries attribution terms.
_EXPECTED_ATTRIBUTION_IDS = {
    "sface_yunet",
    "opencv_yunet",
    "mediapipe_blazeface_short_range",
    "mediapipe_blazeface_full_range",
    "mediapipe_face_landmarker",
    "mediapipe_hand_landmarker",
    "mediapipe_pose_landmarker_lite",
    "mediapipe_selfie_segmenter",
    "opencv_haar_frontalface",
}


def test_fresh_install_lists_every_attribution_model():
    pending = {e.id for e in pending_attribution_entries()}
    assert pending == _EXPECTED_ATTRIBUTION_IDS


def test_restricted_models_are_never_in_the_bundle():
    pending = pending_attribution_entries()
    assert all(is_attribution_entry(e) for e in pending)
    assert "insightface_scrfd_25g" not in {e.id for e in pending}
    assert "dlib_face_recognition_resnet_v1" not in {e.id for e in pending}


def test_confirm_records_one_row_per_model_and_clears_pending():
    ids = [e.id for e in pending_attribution_entries()]
    rows = confirm_attribution_bundle(
        ids, checkbox_responses=_ACK, accepted_at="2026-10-14T00:00:00+00:00"
    )
    assert sorted(r.model_id for r in rows) == sorted(ids)
    assert pending_attribution_entries() == []
    for model_id in ids:
        assert is_acceptance_valid_for(get_entry(model_id)), model_id


def test_rows_match_the_per_model_dialog():
    (row,) = confirm_attribution_bundle(
        ["mediapipe_selfie_segmenter"],
        checkbox_responses=_ACK,
        accepted_at="2026-10-14T00:00:00+00:00",
    )
    entry = get_entry("mediapipe_selfie_segmenter")
    assert row.ack_text_sha256 == entry.ack_text_sha256
    assert row.terms_permalink_url == entry.terms_permalink_url
    assert row.checkbox_responses == _ACK


def test_restricted_id_is_refused_and_nothing_is_written():
    with pytest.raises(AcceptanceError, match="not an attribution"):
        confirm_attribution_bundle(
            ["opencv_yunet", "insightface_scrfd_25g"],
            checkbox_responses=_ACK,
            accepted_at="2026-10-14T00:00:00+00:00",
        )
    assert list(iter_rows()) == []


def test_unticked_box_is_refused_and_nothing_is_written():
    with pytest.raises(AcceptanceError, match="Unchecked"):
        confirm_attribution_bundle(
            ["opencv_yunet"],
            checkbox_responses={"permissive_attribution_acknowledged": False},
            accepted_at="2026-10-14T00:00:00+00:00",
        )
    assert list(iter_rows()) == []


def test_unknown_or_empty_ids_are_refused():
    with pytest.raises(AcceptanceError, match="unknown"):
        confirm_attribution_bundle(["nope"], checkbox_responses=_ACK, accepted_at="t")
    with pytest.raises(AcceptanceError, match="no models"):
        confirm_attribution_bundle([], checkbox_responses=_ACK, accepted_at="t")


def test_prepare_includes_biometric_note_only_when_relevant():
    face = prepare_attribution_bundle([get_entry("opencv_yunet")])
    seg = prepare_attribution_bundle([get_entry("mediapipe_selfie_segmenter")])
    assert face.biometric_responsibility_text
    assert seg.biometric_responsibility_text == ""
    assert face.required_checkboxes[0][0] == "permissive_attribution_acknowledged"
