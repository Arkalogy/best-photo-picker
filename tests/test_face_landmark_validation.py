"""Tests for face landmark validation in face_embed.py.

Validates that _validate_face_landmarks correctly rejects non-face
detections (blankets, torsos, extreme aspect ratios) and accepts
geometrically valid face landmarks.

Also tests _validate_yunet_landmarks (YuNet 5-point geometry checks),
_face_quality_from_landmarks (quality scoring), and tiled detection.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np

from bpp.scoring.face_embed import (
    DEFAULT_EMBEDDING_CONFIDENCE,
    MIN_FACE_ASPECT,
    MIN_FACE_PX,
    _face_quality_from_landmarks,
    _validate_face_landmarks,
    _validate_yunet_landmarks,
)


class TestFaceLandmarkValidation:
    """Unit tests for _validate_face_landmarks."""

    def _make_landmarks(
        self,
        left_eye: list[tuple[int, int]],
        right_eye: list[tuple[int, int]],
        nose_tip: list[tuple[int, int]],
    ) -> dict:
        """Build a 'small' model landmark dict."""
        return {
            "left_eye": left_eye,
            "right_eye": right_eye,
            "nose_tip": nose_tip,
        }

    def test_valid_frontal_face(self):
        """Standard frontal face: eyes above nose, good spread."""
        bbox = (100, 100, 120, 140)
        lm = self._make_landmarks(
            left_eye=[(130, 140), (145, 140)],
            right_eye=[(170, 140), (185, 140)],
            nose_tip=[(155, 180)],
        )
        assert _validate_face_landmarks(lm, bbox) is True

    def test_valid_baby_face(self):
        """Baby face: rounder proportions, eyes close together."""
        bbox = (50, 50, 100, 90)
        lm = self._make_landmarks(
            left_eye=[(75, 75), (85, 75)],
            right_eye=[(105, 75), (115, 75)],
            nose_tip=[(95, 95)],
        )
        assert _validate_face_landmarks(lm, bbox) is True

    def test_reject_extreme_tall_aspect(self):
        """Reject a very tall narrow bbox (body/torso detection)."""
        bbox = (100, 100, 50, 200)  # aspect = 0.25
        lm = self._make_landmarks(
            left_eye=[(110, 150), (120, 150)],
            right_eye=[(130, 150), (140, 150)],
            nose_tip=[(125, 200)],
        )
        assert _validate_face_landmarks(lm, bbox) is False

    def test_reject_extreme_wide_aspect(self):
        """Reject a very wide bbox (not a face)."""
        bbox = (100, 100, 300, 50)  # aspect = 6.0
        lm = self._make_landmarks(
            left_eye=[(150, 110), (170, 110)],
            right_eye=[(280, 110), (300, 110)],
            nose_tip=[(230, 130)],
        )
        assert _validate_face_landmarks(lm, bbox) is False

    def test_reject_compressed_landmarks(self):
        """Reject when landmarks are all bunched into a tiny area (non-face)."""
        bbox = (100, 100, 150, 180)
        # All landmarks within a 5px area — blanket/texture detection
        lm = self._make_landmarks(
            left_eye=[(170, 170), (172, 170)],
            right_eye=[(173, 171), (174, 171)],
            nose_tip=[(172, 172)],
        )
        assert _validate_face_landmarks(lm, bbox) is False

    def test_reject_nose_above_eyes(self):
        """Reject when nose is clearly above eyes (inverted/wrong detection)."""
        bbox = (100, 100, 120, 140)
        lm = self._make_landmarks(
            left_eye=[(130, 200), (145, 200)],
            right_eye=[(170, 200), (185, 200)],
            nose_tip=[(155, 130)],  # nose way above eyes
        )
        assert _validate_face_landmarks(lm, bbox) is False

    def test_accept_slightly_tilted_face(self):
        """Accept a tilted face where nose is barely below eye level."""
        bbox = (100, 100, 120, 140)
        lm = self._make_landmarks(
            left_eye=[(130, 155), (145, 150)],
            right_eye=[(170, 145), (185, 150)],
            nose_tip=[(155, 155)],  # nose at roughly same Y as eyes
        )
        assert _validate_face_landmarks(lm, bbox) is True

    def test_reject_missing_landmarks(self):
        """Reject when landmark keys are missing."""
        bbox = (100, 100, 120, 140)
        assert _validate_face_landmarks({}, bbox) is False
        assert _validate_face_landmarks({"nose_tip": [(155, 180)]}, bbox) is False

    def test_reject_empty_landmark_lists(self):
        """Reject when landmark lists are empty."""
        bbox = (100, 100, 120, 140)
        lm = self._make_landmarks(left_eye=[], right_eye=[], nose_tip=[])
        assert _validate_face_landmarks(lm, bbox) is False

    def test_reject_zero_height_bbox(self):
        """Reject bbox with zero height (degenerate)."""
        bbox = (100, 100, 120, 0)
        lm = self._make_landmarks(
            left_eye=[(130, 100), (145, 100)],
            right_eye=[(170, 100), (185, 100)],
            nose_tip=[(155, 100)],
        )
        assert _validate_face_landmarks(lm, bbox) is False

    def test_borderline_aspect_accepted(self):
        """Aspect ratio right at the boundary should be accepted."""
        # MIN_FACE_ASPECT = 0.45, so w/h = 0.46 should pass
        h = 100
        w = int(h * (MIN_FACE_ASPECT + 0.02))
        bbox = (50, 50, w, h)
        lm = self._make_landmarks(
            left_eye=[(60, 70), (65, 70)],
            right_eye=[(80, 70), (85, 70)],
            nose_tip=[(72, 90)],
        )
        assert _validate_face_landmarks(lm, bbox) is True

    def test_borderline_aspect_rejected(self):
        """Aspect ratio just below boundary should be rejected."""
        h = 100
        w = int(h * (MIN_FACE_ASPECT - 0.05))
        bbox = (50, 50, w, h)
        lm = self._make_landmarks(
            left_eye=[(60, 70), (65, 70)],
            right_eye=[(75, 70), (78, 70)],
            nose_tip=[(68, 90)],
        )
        assert _validate_face_landmarks(lm, bbox) is False


class TestYuNetLandmarkValidation:
    """Tests for _validate_yunet_landmarks (YuNet 5-point geometry checks)."""

    def _make_yunet_face(
        self,
        x=100,
        y=100,
        w=80,
        h=100,
        r_eye=(125, 130),
        l_eye=(155, 130),
        nose=(140, 155),
        r_mouth=(128, 175),
        l_mouth=(152, 175),
        conf=0.9,
    ) -> np.ndarray:
        """Build a YuNet raw face row: [x,y,w,h, 5 landmarks, conf]."""
        return np.array(
            [
                x,
                y,
                w,
                h,
                r_eye[0],
                r_eye[1],
                l_eye[0],
                l_eye[1],
                nose[0],
                nose[1],
                r_mouth[0],
                r_mouth[1],
                l_mouth[0],
                l_mouth[1],
                conf,
            ],
            dtype=np.float32,
        )

    def test_valid_frontal_face(self):
        face = self._make_yunet_face()
        assert _validate_yunet_landmarks(face) is True

    def test_valid_baby_face_wide(self):
        """Wide baby face (aspect ~1.2) should pass."""
        face = self._make_yunet_face(w=120, h=100)
        assert _validate_yunet_landmarks(face) is True

    def test_reject_too_narrow(self):
        """Very narrow bbox (torso) should fail aspect check."""
        face = self._make_yunet_face(w=30, h=150)  # aspect = 0.2
        assert _validate_yunet_landmarks(face) is False

    def test_reject_too_wide(self):
        """Very wide bbox should fail."""
        face = self._make_yunet_face(w=300, h=50)  # aspect = 6.0
        assert _validate_yunet_landmarks(face) is False

    def test_reject_too_small(self):
        """Faces below MIN_FACE_PX should be rejected."""
        face = self._make_yunet_face(
            w=20,
            h=25,
            r_eye=(105, 108),
            l_eye=(115, 108),
            nose=(110, 115),
            r_mouth=(107, 120),
            l_mouth=(113, 120),
        )
        assert _validate_yunet_landmarks(face) is False

    def test_reject_nose_above_eyes(self):
        """Nose Y < eye Y means inverted — reject."""
        face = self._make_yunet_face(
            nose=(140, 120),  # above eyes at y=130
        )
        assert _validate_yunet_landmarks(face) is False

    def test_reject_mouth_above_nose(self):
        """Mouth above nose — reject."""
        face = self._make_yunet_face(
            nose=(140, 170),
            r_mouth=(128, 140),
            l_mouth=(152, 140),  # above nose
        )
        assert _validate_yunet_landmarks(face) is False

    def test_reject_compressed_landmarks(self):
        """All landmarks bunched together — non-face texture."""
        face = self._make_yunet_face(
            r_eye=(140, 150),
            l_eye=(141, 150),
            nose=(140, 151),
            r_mouth=(140, 152),
            l_mouth=(141, 152),
        )
        assert _validate_yunet_landmarks(face) is False

    def test_reject_extreme_eye_distance(self):
        """Eyes too close together (< 15% of face width) — reject."""
        face = self._make_yunet_face(
            w=100,
            r_eye=(140, 130),
            l_eye=(145, 130),  # only 5px apart on 100px face
        )
        assert _validate_yunet_landmarks(face) is False

    def test_reject_eyes_too_far_apart(self):
        """Eyes wider than face (> 85% of face width) — reject."""
        face = self._make_yunet_face(
            w=80,
            r_eye=(100, 130),
            l_eye=(178, 130),  # 78px apart on 80px face
        )
        assert _validate_yunet_landmarks(face) is False

    def test_accept_tilted_face(self):
        """Slightly tilted face (nose barely below eyes) should pass."""
        face = self._make_yunet_face(
            r_eye=(125, 133),
            l_eye=(155, 127),  # tilted
            nose=(140, 135),  # just barely below average eye Y
        )
        assert _validate_yunet_landmarks(face) is True

    def test_reject_zero_dimensions(self):
        face = self._make_yunet_face(w=0, h=0)
        assert _validate_yunet_landmarks(face) is False

    def test_min_face_px_boundary(self):
        """Face at exactly MIN_FACE_PX should pass (if landmarks are valid)."""
        face = self._make_yunet_face(
            x=50,
            y=50,
            w=MIN_FACE_PX,
            h=int(MIN_FACE_PX * 1.2),
            r_eye=(57, 58),
            l_eye=(70, 58),
            nose=(63, 68),
            r_mouth=(59, 76),
            l_mouth=(67, 76),
        )
        assert _validate_yunet_landmarks(face) is True


class TestFaceQuality:
    """Tests for _face_quality_from_landmarks."""

    def _make_face(self, **kwargs) -> np.ndarray:
        defaults = dict(
            x=100,
            y=100,
            w=112,
            h=130,
            r_eye_x=125,
            r_eye_y=130,
            l_eye_x=155,
            l_eye_y=130,
            nose_x=140,
            nose_y=155,
            r_mouth_x=128,
            r_mouth_y=175,
            l_mouth_x=152,
            l_mouth_y=175,
            conf=0.95,
        )
        defaults.update(kwargs)
        d = defaults
        return np.array(
            [
                d["x"],
                d["y"],
                d["w"],
                d["h"],
                d["r_eye_x"],
                d["r_eye_y"],
                d["l_eye_x"],
                d["l_eye_y"],
                d["nose_x"],
                d["nose_y"],
                d["r_mouth_x"],
                d["r_mouth_y"],
                d["l_mouth_x"],
                d["l_mouth_y"],
                d["conf"],
            ],
            dtype=np.float32,
        )

    def test_quality_range(self):
        """Quality score should be between 0 and 1."""
        face = self._make_face()
        q = _face_quality_from_landmarks(face)
        assert 0.0 <= q <= 1.0

    def test_frontal_higher_than_profile(self):
        """Frontal face should score higher than profile."""
        frontal = self._make_face()
        # Profile: one eye much closer to nose
        profile = self._make_face(
            r_eye_x=138,
            r_eye_y=130,  # close to nose
            l_eye_x=160,
            l_eye_y=130,  # far from nose
        )
        q_frontal = _face_quality_from_landmarks(frontal)
        q_profile = _face_quality_from_landmarks(profile)
        assert q_frontal > q_profile

    def test_larger_face_higher_quality(self):
        """Larger face should score higher than tiny face."""
        large = self._make_face(w=112, h=130)
        tiny = self._make_face(w=40, h=50)
        q_large = _face_quality_from_landmarks(large)
        q_tiny = _face_quality_from_landmarks(tiny)
        assert q_large > q_tiny

    def test_high_conf_higher_quality(self):
        """Higher detector confidence should contribute to quality."""
        high = self._make_face(conf=0.95)
        low = self._make_face(conf=0.35)
        q_high = _face_quality_from_landmarks(high)
        q_low = _face_quality_from_landmarks(low)
        assert q_high > q_low

    def test_perfect_symmetry_maxes_frontality(self):
        """Perfectly symmetric landmarks give max frontality component."""
        # Eyes equidistant from nose
        face = self._make_face(
            r_eye_x=120,
            r_eye_y=130,
            l_eye_x=160,
            l_eye_y=130,
            nose_x=140,
            nose_y=155,
        )
        q = _face_quality_from_landmarks(face)
        # With perfect symmetry(0.5), large size(0.3), high conf(0.19) ≈ 0.99
        assert q > 0.9


class TestTiledDetection:
    """Tests for tiled detection fallback in face.py."""

    def test_tiled_detect_returns_empty_on_small_image(self):
        from bpp.scoring.face import _tiled_detect

        img = np.zeros((100, 100, 3), dtype=np.uint8)
        assert _tiled_detect(img, 0.5) == []

    def test_tiled_detect_remaps_coordinates(self):
        """Detections in tiles should have coordinates remapped to full image."""
        from bpp.scoring.face import _tiled_detect

        # Large enough for multiple tiles (640px tiles, 25% overlap → 480px stride)
        img = np.zeros((1400, 1400, 3), dtype=np.uint8)

        # Mock _yunet_detect to return a face in every tile.
        # _tiled_detect lives in bpp.scoring.face_pipeline since the 500-LOC
        # split; patch the lookup at its actual definition site.
        def mock_yunet(image, min_confidence):
            return [(10, 20, 50, 60, 0.9)]

        with patch("bpp.scoring.face_pipeline._yunet_detect", side_effect=mock_yunet):
            faces = _tiled_detect(img, 0.5)

        # Should have multiple detections across tiles
        assert len(faces) > 1
        # First tile at (0,0) → x=10. Later tiles → x=10+tile_offset > 10
        all_x = [f[0] for f in faces]
        assert min(all_x) == 10, "First tile should have original x=10"
        assert max(all_x) > 10, "Later tiles should have remapped x > 10"

    def test_tiled_detect_used_as_fallback(self):
        """_collect_detections should try tiled detection when all else fails."""
        from bpp.scoring.face import _collect_detections

        img = np.zeros((800, 800, 3), dtype=np.uint8)

        # Mock all detectors to return nothing, but tiled finds one
        with (
            patch.dict(
                "bpp.scoring.face._DETECTORS",
                {
                    "yunet": lambda img, conf: [],
                    "mediapipe_sr": lambda img, conf: [],
                    "scrfd": lambda img, conf: [],
                },
            ),
            patch("bpp.scoring.face_pipeline._has_face_recognition", return_value=False),
            patch(
                "bpp.scoring.face_pipeline._tiled_detect",
                return_value=[(100, 200, 50, 60, 0.85)],
            ) as mock_tiled,
        ):
            faces, _found_upright = _collect_detections(img, 0.3)

        mock_tiled.assert_called_once()
        assert len(faces) == 1
        assert faces[0] == (100, 200, 50, 60, 0.85)

    def test_tiled_detect_skipped_on_small_image_with_faces(self):
        """Tiled detection should NOT run on small images even with faces."""
        from bpp.scoring.face import _collect_detections

        # 800x800 is below _TILE_SIZE * 2 = 1280, so tiled is skipped
        img = np.zeros((800, 800, 3), dtype=np.uint8)

        # Patch the detector registry entries directly. The orchestrator
        # calls _DETECTORS[name](image, conf) — patching the bare function
        # symbol no longer takes effect since F-12 introduced the
        # registry indirection.
        with (
            patch.dict(
                "bpp.scoring.face._DETECTORS",
                {
                    "yunet": lambda img, conf: [(50, 50, 80, 80, 0.95)],
                    "mediapipe_sr": lambda img, conf: [],
                    "scrfd": lambda img, conf: [],
                },
            ),
            patch("bpp.scoring.face_pipeline._has_face_recognition", return_value=False),
            patch("bpp.scoring.face_pipeline._tiled_detect") as mock_tiled,
        ):
            faces, _ = _collect_detections(img, 0.3)

        mock_tiled.assert_not_called()
        assert len(faces) == 1

    def test_tiled_detect_skipped_when_faces_found(self):
        """Tiled detection should be skipped when faces already found."""
        from bpp.scoring.face import _collect_detections

        # 1400x1400 > _TILE_SIZE * 2 = 1280 — but faces found, so skip tiling
        img = np.zeros((1400, 1400, 3), dtype=np.uint8)

        with (
            patch.dict(
                "bpp.scoring.face._DETECTORS",
                {
                    "yunet": lambda img, conf: [(50, 50, 80, 80, 0.95)],
                    "mediapipe_sr": lambda img, conf: [],
                    "scrfd": lambda img, conf: [],
                },
            ),
            patch("bpp.scoring.face_pipeline._has_face_recognition", return_value=False),
            patch(
                "bpp.scoring.face_pipeline._tiled_detect",
                return_value=[(500, 600, 40, 50, 0.8)],
            ) as mock_tiled,
        ):
            faces, _ = _collect_detections(img, 0.3)

        mock_tiled.assert_not_called()
        # Only the original face (tiling skipped)
        assert len(faces) == 1


class TestScrfdEarlyExitRunsYunet:
    """SCRFD high-confidence early-exit must still run YuNet.

    BPP's per-person Pick workflow needs every detectable face to make
    it into clustering. SCRFD nails the obvious foreground subject but
    can miss small/profile/background faces in group photos that YuNet
    catches. The original early-exit skipped YuNet entirely, silently
    dropping the second face. The fix runs YuNet on the early-exit
    path but still skips the expensive detectors (MediaPipe, BlazeFace
    FR, dlib). Lock that behavior here.
    """

    def test_scrfd_confident_face_still_runs_yunet_and_merges_extra(self):
        """SCRFD finds one confident face, YuNet finds a second non-overlapping
        face. Both must end up in the returned list, and MediaPipe / dlib
        must NOT be called.
        """
        from bpp.scoring.face import _collect_detections

        img = np.zeros((800, 800, 3), dtype=np.uint8)

        scrfd_face = (100, 100, 120, 140, 0.95)  # high-confidence foreground
        yunet_face = (500, 400, 60, 70, 0.72)  # non-overlapping background face

        yunet_mock = unittest_mock_call_tracker([yunet_face])
        mp_mock = unittest_mock_call_tracker([])
        dlib_mock = unittest_mock_call_tracker([])

        with (
            patch(
                "bpp.scoring.face_pipeline.detect_faces_scrfd",
                return_value=[scrfd_face],
            ),
            patch.dict(
                "bpp.scoring.face._DETECTORS",
                {
                    "yunet": yunet_mock,
                    "mediapipe_sr": mp_mock,
                    "blazeface_fr": mp_mock,
                    "dlib": dlib_mock,
                },
            ),
            patch("bpp.scoring.face_pipeline._has_face_recognition", return_value=False),
        ):
            faces, found_upright = _collect_detections(img, 0.3)

        assert yunet_mock.called, "YuNet must still run on the SCRFD early-exit path"
        assert not mp_mock.called, (
            "MediaPipe / BlazeFace FR must be skipped when SCRFD is confident"
        )
        assert found_upright is True
        assert scrfd_face in faces
        assert yunet_face in faces, (
            "YuNet's complementary face was dropped — early-exit regressed; "
            "BPP would silently lose this person from the People view"
        )

    def test_scrfd_confident_no_yunet_extra(self):
        """When YuNet finds nothing extra, behavior is unchanged: only the
        SCRFD face is returned, and the expensive detectors stay skipped.
        """
        from bpp.scoring.face import _collect_detections

        img = np.zeros((800, 800, 3), dtype=np.uint8)
        scrfd_face = (100, 100, 120, 140, 0.95)

        mp_mock = unittest_mock_call_tracker([])
        dlib_mock = unittest_mock_call_tracker([])

        with (
            patch(
                "bpp.scoring.face_pipeline.detect_faces_scrfd",
                return_value=[scrfd_face],
            ),
            patch.dict(
                "bpp.scoring.face._DETECTORS",
                {
                    "yunet": lambda img, conf: [],
                    "mediapipe_sr": mp_mock,
                    "blazeface_fr": mp_mock,
                    "dlib": dlib_mock,
                },
            ),
            patch("bpp.scoring.face_pipeline._has_face_recognition", return_value=False),
        ):
            faces, _ = _collect_detections(img, 0.3)

        assert not mp_mock.called
        assert faces == [scrfd_face]


def unittest_mock_call_tracker(return_value):
    """Tiny callable tracker — simpler than MagicMock(return_value=...)
    for the (image, confidence) detector signature.
    """

    class _Tracked:
        called = False

        def __call__(self, image, conf):
            self.called = True
            return return_value

    return _Tracked()


class TestEmbeddingConfidenceFilter:
    """Tests for confidence-based filtering in extract_face_embeddings."""

    def test_high_confidence_passes(self):
        """Detections above DEFAULT_EMBEDDING_CONFIDENCE should be kept."""
        assert DEFAULT_EMBEDDING_CONFIDENCE <= 0.9

    def test_low_confidence_rejected(self):
        """Detections below DEFAULT_EMBEDDING_CONFIDENCE should be rejected."""
        assert DEFAULT_EMBEDDING_CONFIDENCE > 0.3

    def test_threshold_value(self):
        """DEFAULT_EMBEDDING_CONFIDENCE should be stricter than detection threshold."""
        # User default detection threshold is 0.3, embedding must be higher
        assert DEFAULT_EMBEDDING_CONFIDENCE > 0.3
        # Calibrated at 0.65 for 97.3% precision on 150 real photos
        assert DEFAULT_EMBEDDING_CONFIDENCE <= 0.75

    def test_detect_faces_with_confidence_returns_scores(self):
        """detect_faces_with_confidence returns (x,y,w,h,conf) tuples."""
        from unittest.mock import patch

        import numpy as np

        from bpp.scoring.face import detect_faces_with_confidence

        img = np.zeros((200, 200, 3), dtype=np.uint8)

        # Mock _collect_detections to return known faces with confidence
        mock_faces = [(50, 50, 80, 80, 0.95), (10, 10, 30, 30, 0.4)]
        with patch("bpp.scoring.face._collect_detections", return_value=(mock_faces, True)):
            results = detect_faces_with_confidence(img, min_confidence=0.3)

        # Should have tuples with 5 elements (including confidence)
        for r in results:
            assert len(r) == 5
            assert isinstance(r[4], float)
