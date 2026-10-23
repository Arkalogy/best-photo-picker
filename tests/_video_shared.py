"""Shared fixtures and helpers of the test_video.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

from PIL import Image


def _make_photo(tmp_path, name="photo.jpg"):
    path = str(tmp_path / name)
    Image.new("RGB", (10, 10)).save(path, "JPEG")
    return path


def _make_fake_video(tmp_path, name="video.mp4"):
    """Create a fake video file (not valid but has the right extension)."""
    path = str(tmp_path / name)
    with open(path, "wb") as f:
        f.write(b"\x00" * 1024)
    return path


def _make_test_video(tmp_path, name="test.mp4", frames=10, size=(64, 48)):
    """Create a real short video using OpenCV's VideoWriter."""
    import cv2
    import numpy as np

    path = str(tmp_path / name)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, 10.0, size)
    for i in range(frames):
        frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        frame[:] = (i * 20 % 256, 100, 200)  # varying blue channel
        writer.write(frame)
    writer.release()
    return path
