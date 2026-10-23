"""Shared fixtures and helpers of the test_face_landmark_validation.py suite (split for the 500-LOC
cap)."""

from __future__ import annotations


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
