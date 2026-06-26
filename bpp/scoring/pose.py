"""Pose landmark detection for semantic search.

Uses MediaPipe PoseLandmarker to detect body poses (standing, sitting,
crawling, etc.) for future semantic search queries like "baby crawling"
or "person jumping".
"""

from __future__ import annotations

import os

import cv2
import numpy as np

from bpp.scoring.model_base import ModelSingleton
from bpp.utils.logging import get_logger
from bpp.utils.paths import models_dir as _models_dir

log = get_logger(__name__)

_MODEL_DIR = str(_models_dir())
# ── Model: MediaPipe Pose Landmarker (lite) ────────────────────────
# What:   detects body pose and 33 keypoints per person. Used by
#         the composition scorer to penalize photos with off-frame
#         body parts (chopped feet, headless torsos) and to bias
#         toward photos where subjects are well-framed.
# Where:  Google's official MediaPipe model storage bucket.
# Why this one: "_lite" is the smallest pose variant (~9MB);
#         heavier "_full" and "_heavy" models exist with more
#         precision but the lite is sufficient for "is the body in
#         frame" composition heuristics.
# License: Apache 2.0 (MediaPipe).
# To bump: same `/latest/` pattern — URL stays stable, just refresh
#         SHA when Google rotates the artifact.
_POSE_MODEL_PATH = os.path.join(_MODEL_DIR, "pose_landmarker_lite.task")
_POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "pose_landmarker/pose_landmarker_lite/float16/latest/"
    "pose_landmarker_lite.task"
)
_POSE_MODEL_SHA256 = "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a"


def _create_pose_detector(path: str) -> object:
    import mediapipe as mp

    options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(path)),
        running_mode=mp.tasks.vision.RunningMode.IMAGE,
        num_poses=5,
        min_pose_detection_confidence=0.3,
    )
    return mp.tasks.vision.PoseLandmarker.create_from_options(options)


_pose_model = ModelSingleton(
    name="PoseLandmarker",
    model_path=_POSE_MODEL_PATH,
    model_url=_POSE_MODEL_URL,
    model_sha256=_POSE_MODEL_SHA256,
    create_fn=_create_pose_detector,
    registry_id=None,  # ancillary mediapipe pose model, no licensing concern
    import_check=lambda: __import__("mediapipe"),
)

# MediaPipe pose landmark indices
_NOSE = 0
_LEFT_SHOULDER = 11
_RIGHT_SHOULDER = 12
_LEFT_HIP = 23
_RIGHT_HIP = 24
_LEFT_KNEE = 25
_RIGHT_KNEE = 26
_LEFT_ANKLE = 27
_RIGHT_ANKLE = 28
_LEFT_WRIST = 15
_RIGHT_WRIST = 16


def ensure_pose_model() -> list[str]:
    """Pre-download pose model. Returns list of warnings."""
    warnings: list[str] = []
    if _pose_model.ensure_model() is None:
        warnings.append("PoseLandmarker unavailable — pose detection disabled")
    return warnings


def detect_poses(
    image: np.ndarray,
) -> list[dict[str, object]]:
    """Detect body poses in an image.

    Returns a list of pose dicts, each containing:
      - ``pose_type``: inferred pose category (standing, sitting, etc.)
      - ``bbox``: (x, y, w, h) bounding box from landmarks
      - ``landmark_count``: number of visible landmarks
      - ``confidence``: average landmark visibility
    """
    import mediapipe as mp

    detector = _pose_model.get()
    if detector is None:
        return []

    if len(image.shape) == 2:
        rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    h, w = rgb.shape[:2]
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    try:
        result = detector.detect(mp_image)
    except Exception:
        return []

    if not result.pose_landmarks:
        return []

    poses = []
    for pose_lms in result.pose_landmarks:
        # Compute bounding box from visible landmarks
        visible = [(lm.x * w, lm.y * h, lm.visibility) for lm in pose_lms]
        vis_pts = [(x, y) for x, y, v in visible if v > 0.3]
        if len(vis_pts) < 5:
            continue

        xs = [p[0] for p in vis_pts]
        ys = [p[1] for p in vis_pts]
        x_min, x_max = int(min(xs)), int(max(xs))
        y_min, y_max = int(min(ys)), int(max(ys))

        avg_vis = sum(v for _, _, v in visible) / len(visible)

        # Infer pose type from landmark geometry
        pose_type = _classify_pose(pose_lms)

        poses.append(
            {
                "pose_type": pose_type,
                "bbox": (x_min, y_min, x_max - x_min, y_max - y_min),
                "landmark_count": len(vis_pts),
                "confidence": float(avg_vis),
            }
        )

    return poses


def _classify_pose(landmarks: list) -> str:
    """Classify body pose from landmark positions.

    Returns one of: standing, sitting, lying, crawling, crouching, unknown.

    MediaPipe landmarks are already image-normalized (y in [0, 1]), so the
    classifier doesn't need image height — every comparison is between
    relative landmark Y coordinates.
    """

    def _get(idx: int) -> tuple[float, float, float]:
        lm = landmarks[idx]
        return lm.x, lm.y, lm.visibility

    # Get key joints
    _, nose_y, nose_v = _get(_NOSE)
    _, l_shoulder_y, ls_v = _get(_LEFT_SHOULDER)
    _, r_shoulder_y, rs_v = _get(_RIGHT_SHOULDER)
    _, l_hip_y, lh_v = _get(_LEFT_HIP)
    _, r_hip_y, rh_v = _get(_RIGHT_HIP)
    _, l_knee_y, lk_v = _get(_LEFT_KNEE)
    _, r_knee_y, rk_v = _get(_RIGHT_KNEE)
    _, l_ankle_y, la_v = _get(_LEFT_ANKLE)
    _, r_ankle_y, ra_v = _get(_RIGHT_ANKLE)

    # Need minimum visibility for classification
    if ls_v < 0.3 or rs_v < 0.3 or lh_v < 0.3 or rh_v < 0.3:
        return "unknown"

    shoulder_y = (l_shoulder_y + r_shoulder_y) / 2
    hip_y = (l_hip_y + r_hip_y) / 2
    torso_len = abs(hip_y - shoulder_y)

    if torso_len < 0.01:
        return "unknown"

    # Lying: torso is nearly horizontal (small Y difference between shoulder and hip)
    if torso_len < 0.08:
        return "lying"

    # Check if knees/ankles are visible for more detailed classification
    knees_visible = lk_v > 0.3 or rk_v > 0.3
    ankles_visible = la_v > 0.3 or ra_v > 0.3

    if knees_visible:
        knee_y = (l_knee_y if lk_v > 0.3 else r_knee_y) if lk_v > 0.3 or rk_v > 0.3 else hip_y

        # Crawling: knees and hands at similar height, body horizontal-ish
        if torso_len < 0.15 and knee_y > hip_y:
            return "crawling"

        # Sitting: hips and knees at similar height
        if abs(hip_y - knee_y) < torso_len * 0.5:
            return "sitting"

        # Crouching: knees bent significantly, body low
        if knee_y > hip_y + torso_len * 0.3 and nose_v > 0.3 and nose_y > 0.4:
            return "crouching"

    # Standing: vertical torso, body fills height
    if ankles_visible:
        ankle_y = (
            (l_ankle_y + r_ankle_y) / 2
            if la_v > 0.3 and ra_v > 0.3
            else (l_ankle_y if la_v > 0.3 else r_ankle_y)
        )
        body_height = ankle_y - shoulder_y
        if body_height > 0.2:
            return "standing"

    return "standing" if torso_len > 0.15 else "unknown"


# ── Registry ───────────────────────────────────────────────────────
from bpp.scoring.model_base import ModelEntry, ModelRegistry  # noqa: E402

ModelRegistry.register(
    ModelEntry(
        name="PoseLandmarker",
        path=_POSE_MODEL_PATH,
        url=_POSE_MODEL_URL,
        sha256=_POSE_MODEL_SHA256,
        reset=_pose_model.reset,
    )
)
