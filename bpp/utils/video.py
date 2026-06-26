"""Video file detection, thumbnail extraction, metadata, and trimming."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

import cv2
from PIL import Image

from bpp.constants import (
    JPEG_QUALITY_SPRITE,
    VIDEO_SPRITE_END,
    VIDEO_SPRITE_FRAMES,
    VIDEO_SPRITE_START,
    VIDEO_SPRITE_WIDTH,
)
from bpp.utils.logging import get_logger

log = get_logger(__name__)

VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp", ".wmv", ".flv"}
)


def is_video_file(filepath: str) -> bool:
    """Check if a filepath has a video extension."""
    ext = os.path.splitext(filepath)[1].lower()
    return ext in VIDEO_EXTENSIONS


def extract_video_frame(filepath: str, position: float = 0.5) -> Image.Image | None:
    """Extract a single frame from a video file using OpenCV.

    Args:
        filepath: Path to the video file.
        position: Relative position in the video (0.0 = start, 1.0 = end).

    Returns:
        PIL Image of the frame, or None if extraction fails.
    """
    try:
        cap = cv2.VideoCapture(filepath)
        if not cap.isOpened():
            return None

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return None

        target_frame = int(total_frames * min(max(position, 0.0), 1.0))
        target_frame = min(target_frame, total_frames - 1)

        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            return None

        # BGR -> RGB
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return Image.fromarray(rgb)
    except Exception as e:
        log.debug("Failed to extract frame from %s: %s", filepath, e)
        return None


def extract_video_metadata(filepath: str) -> dict | None:
    """Extract video metadata using OpenCV.

    Returns dict with: duration (seconds), width, height, fps, codec, frame_count.
    Returns None if the file cannot be opened as a video.
    """
    try:
        cap = cv2.VideoCapture(filepath)
        if not cap.isOpened():
            return None

        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # Decode fourcc to string
        fourcc_int = int(cap.get(cv2.CAP_PROP_FOURCC))
        codec = "".join(chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4))

        cap.release()

        if frame_count <= 0 or fps <= 0 or width <= 0 or height <= 0:
            return None

        duration = frame_count / fps

        return {
            "duration": round(duration, 2),
            "width": width,
            "height": height,
            "fps": round(fps, 2),
            "codec": codec.strip(),
            "frame_count": frame_count,
        }
    except Exception as e:
        log.debug("Failed to extract video metadata from %s: %s", filepath, e)
        return None


def format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration (e.g., '1:23' or '1:02:15')."""
    s = int(seconds)
    if s < 3600:
        return f"{s // 60}:{s % 60:02d}"
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def generate_video_sprite(
    filepath: str,
    output_path: str,
    frames: int = VIDEO_SPRITE_FRAMES,
    thumb_width: int = VIDEO_SPRITE_WIDTH,
) -> bool:
    """Extract N evenly-spaced frames and tile them into a horizontal sprite sheet.

    Args:
        filepath: Path to the video file.
        output_path: Where to save the JPEG sprite.
        frames: Number of frames to extract.
        thumb_width: Width of each frame thumbnail.

    Returns:
        True on success, False on failure.
    """
    try:
        import numpy as np

        cap = cv2.VideoCapture(filepath)
        if not cap.isOpened():
            return False

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames <= 0:
            cap.release()
            return False

        start = int(total_frames * VIDEO_SPRITE_START)
        end = int(total_frames * VIDEO_SPRITE_END)
        step = max(1, (end - start) // frames)
        positions = [min(start + i * step, total_frames - 1) for i in range(frames)]

        thumbs = []
        for pos in positions:
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            h, w = frame.shape[:2]
            scale = thumb_width / w
            new_h = int(h * scale)
            resized = cv2.resize(frame, (thumb_width, new_h))
            thumbs.append(resized)

        cap.release()

        if not thumbs:
            return False

        target_h = max(t.shape[0] for t in thumbs)
        padded = []
        for t in thumbs:
            if t.shape[0] < target_h:
                pad = np.zeros((target_h - t.shape[0], t.shape[1], 3), dtype=np.uint8)
                t = np.vstack([t, pad])
            padded.append(t)

        sprite = np.hstack(padded)
        rgb = cv2.cvtColor(sprite, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        img.save(output_path, "JPEG", quality=JPEG_QUALITY_SPRITE)
        return True
    except Exception as e:
        log.debug("Failed to generate video sprite for %s: %s", filepath, e)
        return False


def ffmpeg_available() -> bool:
    """Check if ffmpeg is available on the system PATH."""
    return shutil.which("ffmpeg") is not None


def trim_video(src: str, dest: str, start: float, end: float) -> dict[str, Any]:
    """Trim a video using ffmpeg.

    Args:
        src: Source video path.
        dest: Output video path.
        start: Start time in seconds.
        end: End time in seconds.

    Returns:
        {"ok": True} on success, {"ok": False, "error": "..."} on failure.
    """
    if start >= end:
        return {"ok": False, "error": "Start time must be before end time"}
    if not os.path.isfile(src):
        return {"ok": False, "error": f"Source file not found: {src}"}
    if not ffmpeg_available():
        return {"ok": False, "error": "ffmpeg is not installed"}

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start),
        "-to",
        str(end),
        "-i",
        src,
        "-c",
        "copy",
        dest,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")[:500]
            return {"ok": False, "error": f"ffmpeg failed: {stderr}"}
        return {"ok": True}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ffmpeg timed out after 5 minutes"}
    except Exception:
        log.warning("Unexpected error during video trim of %s", src, exc_info=True)
        return {"ok": False, "error": "Unexpected error during video trim"}
