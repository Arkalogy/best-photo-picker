"""Video scoring helper — frame-sample-then-average pipeline.

Extracted from ``bpp.scoring.aggregate`` during the v0.1 cleanup. The
host module covers the broader scoring pipeline (image analysis,
caching, blur normalisation, aggregate weights); video scoring is the
single largest sub-feature and stands cleanly on its own.

Re-exported from ``bpp.scoring.aggregate`` for back-compat.
"""

from __future__ import annotations

from typing import Any

import cv2

from bpp.exif_utils import get_date
from bpp.scoring.blur import score_blur_raw
from bpp.scoring.composition import score_composition
from bpp.scoring.exposure import score_exposure
from bpp.scoring.face import detect_faces, score_face
from bpp.scoring.pets import is_available as pets_available
from bpp.scoring.skin import score_skin_exposure
from bpp.utils.logging import get_logger

log = get_logger(__name__)


def analyze_single_video(
    filepath: str,
    max_long_side: int = 1024,
    num_samples: int = 5,
    model_toggles: dict[str, bool] | None = None,
) -> dict[str, Any] | None:
    """Analyze a video by sampling frames and averaging scores.

    Extracts `num_samples` evenly-spaced frames, scores each through the
    image pipeline, and returns the averaged result with video metadata.
    """
    mt = model_toggles or {}
    from bpp.media_types import MediaKind, media_kind_from_path
    from bpp.utils.video import extract_video_metadata

    if media_kind_from_path(filepath) is not MediaKind.VIDEO:
        return None

    cap = cv2.VideoCapture(filepath)
    if not cap.isOpened():
        return None

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if frame_count <= 0:
        cap.release()
        return None

    # Sample frame positions evenly across the video
    positions = [int(frame_count * (i + 1) / (num_samples + 1)) for i in range(num_samples)]
    frames = []
    for pos in positions:
        cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
        ret, frame = cap.read()
        if ret and frame is not None:
            # Downscale if needed
            h, w = frame.shape[:2]
            long_side = max(h, w)
            if long_side > max_long_side:
                scale = max_long_side / long_side
                frame = cv2.resize(
                    frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA
                )
            frames.append(frame)
    cap.release()

    if not frames:
        return None

    # Score each frame and average
    from bpp.scoring.registry import get_video_avg_keys

    score_keys = get_video_avg_keys()
    accum: dict[str, float] = {k: 0.0 for k in score_keys}

    for frame in frames:
        accum["blur_raw"] += score_blur_raw(frame)
        accum["exposure_score"] += score_exposure(frame)
        faces = detect_faces(frame, model_toggles=mt)
        face_result = score_face(frame, faces=faces, model_toggles=mt)
        accum["face_score"] += face_result["face_score"]
        accum["face_count"] += face_result["face_count"]
        accum["largest_face_ratio"] += face_result["largest_face_ratio"]
        accum["face_center_dist"] += face_result["face_center_dist"]
        accum["composition_score"] += score_composition(frame, faces, model_toggles=mt)
        accum["skin_score"] += score_skin_exposure(frame)

    n = len(frames)
    averaged = {k: accum[k] / n for k in score_keys}
    averaged["face_count"] = round(averaged["face_count"])

    dt = get_date(filepath)
    result: dict[str, Any] = {
        "filepath": filepath,
        "date": dt.isoformat(),
        "date_day": dt.strftime("%Y-%m-%d"),
        "date_month": dt.strftime("%Y-%m"),
        "is_video": 1,
        **averaged,
    }

    # Pet detection on the middle frame
    if mt.get("model_pets", True) and pets_available():
        try:
            from bpp.scoring.pets import detect_pets

            pet_result = detect_pets(frames[len(frames) // 2])
            result["pet_count"] = pet_result["pet_count"]
            result["has_cat"] = pet_result["has_cat"]
            result["has_dog"] = pet_result["has_dog"]
            result["pet_detections"] = pet_result.get("pet_detections", [])
        except Exception:
            # `pets_available()` was True coming in — any failure here is a
            # real runtime fault (model corrupted post-install, mid-run
            # unload, CUDA OOM, etc.), not a "feature missing" case.
            # Warning-level so it surfaces in server.log without spamming
            # INFO for ordinary photos.
            log.warning("Pet detection failed for video %s", filepath, exc_info=True)

    # Video metadata
    vmeta = extract_video_metadata(filepath)
    if vmeta:
        result["video_duration"] = vmeta["duration"]
        result["video_width"] = vmeta["width"]
        result["video_height"] = vmeta["height"]
        result["video_fps"] = vmeta["fps"]
        result["video_codec"] = vmeta["codec"]

    return result
