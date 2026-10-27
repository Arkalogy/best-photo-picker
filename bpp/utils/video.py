"""Video file detection, thumbnail extraction, metadata, and trimming."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from typing import Any

import cv2
from PIL import Image

from bpp.constants import (
    JPEG_QUALITY_SPRITE,
    VIDEO_EXTENSIONS,
    VIDEO_SPRITE_END,
    VIDEO_SPRITE_FRAMES,
    VIDEO_SPRITE_START,
    VIDEO_SPRITE_WIDTH,
)
from bpp.utils.fresh_file import replace_fresh
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def is_video_file(filepath: str) -> bool:
    """Check if a filepath has a video extension."""
    ext = os.path.splitext(filepath)[1].lower()
    return ext in VIDEO_EXTENSIONS


#: FFmpeg (OpenCV's backend) follows the URLs inside a DASH / HLS manifest
#: that merely carries a video extension. The control is the manifest
#: sniff in open_capture (such a file never reaches the decoder); this
#: capture option asks the backend for the local file protocol only, but
#: the bundled OpenCV wheel does not honour it, so it is best effort, not
#: a second layer.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "protocol_whitelist;file")


def _looks_like_a_manifest(filepath: str) -> bool:
    """A text file (an XML / M3U playlist with a video extension) is not a
    video container: every container this app reads starts with binary
    bytes. Such a file is never handed to the decoder at all."""
    try:
        with open(filepath, "rb") as f:
            head = f.read(64)
    except OSError:
        return False
    if not head:
        return True
    text = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    return text[:1] in (b"<", b"#") or all(32 <= b < 127 or b in (9, 10, 13) for b in head)


def open_capture(filepath: str) -> cv2.VideoCapture | None:
    """The one way this app decodes a video: refuses manifests (the one
    real guard against a playlist with a video extension), returns None
    when the file does not open. (:func:`trim_video` gates ffmpeg the
    same way, plus ``-protocol_whitelist file``.)"""
    if _looks_like_a_manifest(filepath):
        return None
    cap = cv2.VideoCapture(filepath, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap.release()
        return None
    return cap


def extract_video_frame(filepath: str, position: float = 0.5) -> Image.Image | None:
    """Extract a single frame from a video file using OpenCV.

    Args:
        filepath: Path to the video file.
        position: Relative position in the video (0.0 = start, 1.0 = end).

    Returns:
        PIL Image of the frame, or None if extraction fails.
    """
    try:
        cap = open_capture(filepath)
        if cap is None:
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
        cap = open_capture(filepath)
        if cap is None:
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

        cap = open_capture(filepath)
        if cap is None:
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
        tmp = f"{output_path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with replace_fresh(output_path, part=tmp) as fh:
            img.save(fh, "JPEG", quality=JPEG_QUALITY_SPRITE)
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
    if _looks_like_a_manifest(src):
        # A playlist with a video extension would have ffmpeg open
        # whatever it lists; the decoder refuses it, so does the trim.
        return {"ok": False, "error": "This file is a playlist, not a video"}
    if not ffmpeg_available():
        return {"ok": False, "error": "ffmpeg is not installed"}

    cmd = [
        "ffmpeg",
        "-y",
        "-protocol_whitelist",
        "file",
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
