"""Tests for face landmark validation in face_embed.py — part 2 of 2: TestTiledDetection,
TestScrfdEarlyExitRunsYunet, TestEmbeddingConfidenceFilter."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np

from bpp.scoring.face_embed import (
    DEFAULT_EMBEDDING_CONFIDENCE,
)
from tests._detector_stubs import stub_detectors
from tests._face_landmark_validation_shared import unittest_mock_call_tracker


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
            stub_detectors(
                {
                    "yunet": lambda img, conf: [],
                    "mediapipe_sr": lambda img, conf: [],
                    "blazeface_fr": lambda img, conf: [],
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

        # Replace the detectors through the registry: that is where the
        # orchestrator looks them up.
        with (
            stub_detectors(
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
            stub_detectors(
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
            stub_detectors(
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
            stub_detectors(
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
        with (
            patch("bpp.scoring.face_pipeline._collect_detections", return_value=(mock_faces, True)),
            patch("bpp.scoring.face_hand_filter._detect_hand_bboxes", return_value=[]),
        ):
            results = detect_faces_with_confidence(img, min_confidence=0.3)

        # Should have tuples with 5 elements (including confidence)
        for r in results:
            assert len(r) == 5
            assert isinstance(r[4], float)
