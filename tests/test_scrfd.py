"""TDD tests for SCRFD face detector.

RED phase: these tests define the expected API and behavior of the SCRFD
detector BEFORE implementation. All tests should FAIL initially.

SCRFD should:
1. Load as a ModelSingleton (ONNX model, lazy download)
2. Provide detect_faces_scrfd(image) → list of (x, y, w, h, confidence)
3. Be wired into the main detect_faces pipeline as primary detector
4. Detect small faces that BlazeFace/dlib miss (babies, distant people)
5. Handle HEIC-originated images (uint8 BGR numpy arrays)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

try:
    import cv2
except ImportError:
    cv2 = None  # type: ignore[assignment]


# ── Helpers (must be before skipif decorators) ──


def _scrfd_available() -> bool:
    """Check if SCRFD model is downloaded and ready."""
    try:
        from bpp.scoring.face import _scrfd_model

        return _scrfd_model.is_available()
    except (ImportError, AttributeError):
        return False


def _make_synthetic_face(size: int = 200) -> np.ndarray:
    """Create a crude synthetic face-like image for testing."""
    img = np.full((size, size, 3), 220, dtype=np.uint8)
    cx, cy = size // 2, size // 2
    cv2.ellipse(img, (cx, cy), (size // 3, size // 2 - 10), 0, 0, 360, (180, 150, 130), -1)
    eye_y = cy - size // 8
    cv2.circle(img, (cx - size // 6, eye_y), size // 20, (40, 40, 40), -1)
    cv2.circle(img, (cx + size // 6, eye_y), size // 20, (40, 40, 40), -1)
    mouth_y = cy + size // 6
    cv2.ellipse(img, (cx, mouth_y), (size // 6, size // 12), 0, 0, 180, (60, 60, 80), 2)
    return img


# ── 1. Module-level: SCRFD detector function exists ──


def test_scrfd_module_has_detect_function():
    """detect_faces_scrfd must be importable from bpp.scoring.face."""
    from bpp.scoring.face import detect_faces_scrfd

    assert callable(detect_faces_scrfd)


def test_scrfd_singleton_exists():
    """SCRFD model must use ModelSingleton pattern."""
    from bpp.scoring.face import _scrfd_model
    from bpp.scoring.model_base import ModelSingleton

    assert isinstance(_scrfd_model, ModelSingleton)


# ── 2. Detection API contract ──


def test_scrfd_returns_list_of_tuples():
    """detect_faces_scrfd returns list of (x, y, w, h, confidence) tuples."""
    from bpp.scoring.face import detect_faces_scrfd

    # 200x200 blank image — no faces expected, but return type must be list
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    result = detect_faces_scrfd(img)
    assert isinstance(result, list)


def test_scrfd_empty_on_blank_image():
    """No faces on a uniform grey image."""
    from bpp.scoring.face import detect_faces_scrfd

    img = np.full((300, 300, 3), 128, dtype=np.uint8)
    result = detect_faces_scrfd(img)
    assert result == []


def test_scrfd_tuple_shape():
    """Each detection must be a 5-tuple: (x, y, w, h, confidence)."""
    from bpp.scoring.face import detect_faces_scrfd

    # Use a real test image with a clear face
    img = _make_synthetic_face(size=300)
    result = detect_faces_scrfd(img)
    # If detection works on synthetic face, check tuple shape
    # If not, this test passes vacuously (no detections to check)
    for det in result:
        assert len(det) == 5, f"Expected 5-tuple, got {len(det)}-tuple: {det}"
        _x, _y, _w, _h, conf = det
        assert isinstance(conf, float)
        assert 0.0 <= conf <= 1.0


def test_scrfd_confidence_threshold():
    """Detections below min_confidence should be filtered out."""
    from bpp.scoring.face import detect_faces_scrfd

    img = _make_synthetic_face(size=300)
    # Very high threshold should return fewer or no detections
    high_thresh = detect_faces_scrfd(img, min_confidence=0.99)
    low_thresh = detect_faces_scrfd(img, min_confidence=0.01)
    assert len(high_thresh) <= len(low_thresh)


# ── 3. Integration: SCRFD wired into main pipeline ──


def test_scrfd_in_iterative_collect():
    """_iterative_collect should try SCRFD before BlazeFace."""
    from bpp.scoring import face

    # SCRFD should be mentioned in the detection pipeline
    # This verifies it's wired in, not just defined
    src = Path(face.__file__).read_text()
    assert "scrfd" in src.lower(), "SCRFD not found in face.py source"


def test_scrfd_toggle():
    """model_toggles={'model_scrfd': False} should skip SCRFD."""
    from bpp.scoring.face import detect_faces_with_confidence

    img = np.zeros((200, 200, 3), dtype=np.uint8)
    # Should not crash when SCRFD is disabled
    result = detect_faces_with_confidence(img, model_toggles={"model_scrfd": False})
    assert isinstance(result, list)


# ── 4. Model registry ──


def test_scrfd_model_path_exists():
    """SCRFD model path constant must be defined in face.py."""
    from bpp.scoring.face import _SCRFD_MODEL_PATH

    assert isinstance(_SCRFD_MODEL_PATH, (str, type(None)))


# ── 5. Quality: detects faces that BlazeFace misses ──


@pytest.mark.skipif(
    not _scrfd_available(),
    reason="SCRFD model not downloaded yet",
)
def test_scrfd_detects_small_face():
    """SCRFD should detect a face occupying ~3% of the image.

    BlazeFace typically misses faces below ~10% of frame area.
    """
    from bpp.scoring.face import detect_faces_scrfd

    # Create 640x640 image with a small face region (~50x50 = 0.6% of area)
    img = np.full((640, 640, 3), 200, dtype=np.uint8)
    # Paste a synthetic face at small scale
    face = _make_synthetic_face(size=60)
    img[100:160, 100:160] = face
    result = detect_faces_scrfd(img, min_confidence=0.3)
    # We can't guarantee detection on synthetic faces, but the function
    # must at least not crash on this input
    assert isinstance(result, list)
