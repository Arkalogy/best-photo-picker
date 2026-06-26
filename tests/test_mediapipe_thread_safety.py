"""TDD tests for C-3: MediaPipe detector thread safety.

MediaPipe detectors are NOT thread-safe — concurrent .detect() calls corrupt
internal state. All MediaPipe .detect() calls must be serialized with a lock.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np


class TestMediaPipeDetectSerialized:
    """_mediapipe_detect must hold _mp_lock during .detect()."""

    def test_mediapipe_detect_acquires_lock(self):
        """The lock must be held around the .detect() call."""
        from bpp.scoring.face_mediapipe import _mp_lock

        img = np.zeros((100, 100, 3), dtype=np.uint8)

        mock_detector = MagicMock()
        mock_result = MagicMock()
        mock_result.detections = []
        mock_detector.detect.return_value = mock_result

        with patch(
            "bpp.scoring.face_mediapipe._get_mediapipe_detector", return_value=mock_detector
        ):
            lock_held_during_detect = []

            def tracking_detect(*a, **kw):
                lock_held_during_detect.append(_mp_lock.locked())
                return mock_result

            mock_detector.detect.side_effect = tracking_detect

            from bpp.scoring.face_mediapipe import _mediapipe_detect

            _mediapipe_detect(img)

            assert lock_held_during_detect, "detect was never called"
            assert lock_held_during_detect[0] is True, "_mp_lock not held during detector.detect()"


class TestLandmarkerDetectSerialized:
    """_score_expression must hold _landmarker_lock during .detect()."""

    def test_landmarker_detect_acquires_lock(self):
        from bpp.scoring.face import _landmarker_lock

        img = np.zeros((100, 100, 3), dtype=np.uint8)

        mock_landmarker = MagicMock()
        mock_result = MagicMock()
        mock_result.face_blendshapes = []
        mock_landmarker.detect.return_value = mock_result

        lock_held = []

        def tracking_detect(*a, **kw):
            lock_held.append(_landmarker_lock.locked())
            return mock_result

        mock_landmarker.detect.side_effect = tracking_detect

        with patch("bpp.scoring.face_expression._get_landmarker", return_value=mock_landmarker):
            from bpp.scoring.face import _score_expression

            _score_expression(img)

            assert lock_held, "detect was never called"
            assert lock_held[0] is True, "_landmarker_lock not held during landmarker.detect()"


class TestHandDetectSerialized:
    """_detect_hand_bboxes must hold _hand_lock during .detect()."""

    def test_hand_detect_acquires_lock(self):
        from bpp.scoring.face import _hand_lock

        img = np.zeros((100, 100, 3), dtype=np.uint8)

        mock_hand = MagicMock()
        mock_result = MagicMock()
        mock_result.hand_landmarks = []
        mock_hand.detect.return_value = mock_result

        lock_held = []

        def tracking_detect(*a, **kw):
            lock_held.append(_hand_lock.locked())
            return mock_result

        mock_hand.detect.side_effect = tracking_detect

        with patch("bpp.scoring.face_hand_filter._get_hand_detector", return_value=mock_hand):
            from bpp.scoring.face import _detect_hand_bboxes

            _detect_hand_bboxes(img)

            assert lock_held, "detect was never called"
            assert lock_held[0] is True, "_hand_lock not held during hand_detector.detect()"
