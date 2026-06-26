"""Tests for full-coverage face detection (bpp.scoring.face_embed_detect).

The shared detector both built-in embedders use. Its job: surface every
face the wider pipeline finds — not just what YuNet sees at full image
scale — by recovering a real YuNet landmark row per box via a crop
re-detect, while still dropping boxes no detector can confirm.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from bpp.scoring.face_embed_detect import detect_face_rows


def _blank(h: int = 300, w: int = 300) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _valid_row(x: int, y: int, w: int = 50, h: int = 60, conf: float = 0.9) -> np.ndarray:
    """A geometrically valid YuNet row (eyes>nose>mouth, plausible spread)."""
    return np.array(
        [
            x,
            y,
            w,
            h,
            x + 12,
            y + 15,  # right eye
            x + 30,
            y + 15,  # left eye
            x + 21,
            y + 32,  # nose
            x + 13,
            y + 45,  # mouth right
            x + 30,
            y + 45,  # mouth left
            conf,
        ],
        dtype=np.float32,
    )


def test_recovers_pipeline_face_missed_by_direct_yunet():
    """A face the pipeline finds but direct YuNet misses is recovered via crop."""
    calls = {"n": 0}

    def _raw_side_effect(image, min_confidence=0.5):
        # First call = direct full-image pass → miss. Later = crop pass → hit.
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return np.array([_valid_row(20, 20)])

    with (
        patch("bpp.scoring.face_yunet._yunet_detect_raw", side_effect=_raw_side_effect),
        patch(
            "bpp.scoring.face.detect_faces_with_confidence",
            return_value=[(100, 100, 60, 70, 0.4)],
        ),
    ):
        rows = detect_face_rows(_blank(), min_confidence=0.2, embedding_confidence=0.65)

    assert len(rows) == 1, "the pipeline-only face should be recovered"


def test_unconfirmable_pipeline_box_is_dropped():
    """A box no YuNet crop re-detect can confirm is dropped (false-positive filter)."""
    with (
        patch("bpp.scoring.face_yunet._yunet_detect_raw", return_value=None),
        patch(
            "bpp.scoring.face.detect_faces_with_confidence",
            return_value=[(120, 120, 40, 40, 0.18)],
        ),
    ):
        rows = detect_face_rows(_blank(), min_confidence=0.2, embedding_confidence=0.65)

    assert rows == [], "an unconfirmable low-confidence box must not become a face"


def test_direct_and_pipeline_overlap_deduped():
    """A face found both directly and by the pipeline is kept exactly once."""
    direct = np.array([_valid_row(40, 40)])
    with (
        patch("bpp.scoring.face_yunet._yunet_detect_raw", return_value=direct),
        patch(
            "bpp.scoring.face.detect_faces_with_confidence",
            return_value=[(42, 42, 50, 60, 0.9)],  # overlaps the direct box
        ),
    ):
        rows = detect_face_rows(_blank(), min_confidence=0.2, embedding_confidence=0.65)

    assert len(rows) == 1


def test_direct_face_below_gate_dropped():
    """Direct YuNet faces under the embedding-confidence gate are dropped."""
    direct = np.array([_valid_row(40, 40, conf=0.3)])
    with (
        patch("bpp.scoring.face_yunet._yunet_detect_raw", return_value=direct),
        patch("bpp.scoring.face.detect_faces_with_confidence", return_value=[]),
    ):
        rows = detect_face_rows(_blank(), min_confidence=0.2, embedding_confidence=0.65)

    assert rows == []


# ─── detect_inverted_face: 180°-upside-down face resolution ──────
#
# YuNet fires on upside-down faces with hallucinated UPRIGHT landmarks
# (verified on real bath photos: conf 0.86 inverted vs 0.94 rotated), so
# landmark geometry can't reveal the inversion — only a rotated
# re-detect can. These tests pin the decision rule and the coordinate
# mapping into the 180°-rotated full-image space.

from bpp.scoring.face_embed_detect import detect_inverted_face  # noqa: E402


def test_upright_face_no_rotated_detection_returns_none():
    """Rotated re-detect finds nothing → face is upright, keep as-is."""
    face = _valid_row(100, 100, conf=0.85)
    with patch("bpp.scoring.face_yunet._yunet_detect_raw", return_value=None):
        assert detect_inverted_face(_blank(), face) is None


def test_rotated_detection_must_beat_confidence_margin():
    """A rotated hit only barely as confident as the original is noise."""
    face = _valid_row(100, 100, conf=0.85)
    # Same spot in the rotated crop (expected position), conf below 0.85+margin.
    rotated_hit = np.array([_valid_row(36, 36, conf=0.87)])
    with patch("bpp.scoring.face_yunet._yunet_detect_raw", return_value=rotated_hit):
        assert detect_inverted_face(_blank(), face) is None


def test_rotated_detection_elsewhere_in_crop_rejected():
    """A *different* face inside the padded crop must not flip this one."""
    face = _valid_row(100, 100, conf=0.5)
    # Confident, valid row — but nowhere near the expected rotated position.
    rotated_hit = np.array([_valid_row(0, 0, conf=0.95)])
    with patch("bpp.scoring.face_yunet._yunet_detect_raw", return_value=rotated_hit):
        assert detect_inverted_face(_blank(), face) is None


def test_high_confidence_face_skips_rotated_redetect():
    """conf + margin >= 1.0 can never be beaten — skip the extra detect."""
    face = _valid_row(100, 100, conf=0.97)
    with patch("bpp.scoring.face_yunet._yunet_detect_raw") as raw:
        assert detect_inverted_face(_blank(), face) is None
        raw.assert_not_called()


def test_inverted_face_row_mapped_to_rotated_image_coords():
    """The winning rotated row comes back in 180°-rotated FULL-image coords.

    Geometry: image 300x300, face at (100,100,50,60), pad=0.6*60=36 →
    crop spans (64,64)-(186,196). Expected position inside the rotated
    crop is (36,36); the mapped row must land at (150,140) — exactly
    where the face sits in image[::-1, ::-1] (300-100-50, 300-100-60).
    """
    face = _valid_row(100, 100, conf=0.5)
    rotated_hit = np.array([_valid_row(36, 36, conf=0.9)])
    with patch("bpp.scoring.face_yunet._yunet_detect_raw", return_value=rotated_hit):
        row = detect_inverted_face(_blank(), face)

    assert row is not None, "clearly-better rotated detection must win"
    assert (int(row[0]), int(row[1]), int(row[2]), int(row[3])) == (150, 140, 50, 60)
    # Landmarks translate by the same offset: crop-rot (36+12, 36+15)
    # + origin of the crop within the rotated image (300-186, 300-196).
    assert (int(row[4]), int(row[5])) == (114 + 48, 104 + 51)
    assert float(row[-1]) == pytest.approx(0.9)


def test_extract_sface_aligns_inverted_face_against_rotated_image():
    """_extract_sface must hand alignCrop the 180°-rotated image + mapped
    row for an inverted face, while the result bbox stays in original
    image coordinates for the UI overlay."""
    from unittest.mock import MagicMock

    from bpp.scoring.face_embed_sface import _extract_sface

    image = np.full((300, 300, 3), 60, dtype=np.uint8)
    face = _valid_row(100, 100, conf=0.5)
    mapped = _valid_row(150, 140, conf=0.9)

    recognizer = MagicMock()
    recognizer.alignCrop.return_value = np.zeros((112, 112, 3), dtype=np.uint8)
    recognizer.feature.return_value = np.ones((1, 128), dtype=np.float32)

    with (
        patch("bpp.scoring.face_embed_sface._get_sface_recognizer", return_value=recognizer),
        patch("bpp.scoring.face_embed_detect.detect_face_rows", return_value=[face]),
        patch("bpp.scoring.face_embed_detect.detect_inverted_face", return_value=mapped),
    ):
        results = _extract_sface(image, min_confidence=0.2, min_quality=0.0)

    assert results is not None and len(results) == 1
    align_image, align_row = recognizer.alignCrop.call_args[0]
    assert np.array_equal(align_image, image[::-1, ::-1]), (
        "alignCrop must receive the 180°-rotated view for an inverted face"
    )
    assert np.array_equal(align_row, mapped), "alignCrop must receive the mapped row"
    assert results[0]["bbox"] == (100, 100, 50, 60), (
        "stored bbox must stay in original-image coordinates"
    )
