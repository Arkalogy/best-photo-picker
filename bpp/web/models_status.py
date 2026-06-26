"""Build the /api/models response — one feature dict per ML model group.

M12: extracted from bp_core.api_models so the route handler is a thin
wrapper and each feature group is independently testable. The schema
each entry follows is intentionally redundant (same keys repeated)
to keep the JSON response shape uniform for the JS consumer.

Status values:
  - ``ready``       — model file present + library installed
  - ``missing``     — file not on disk (offer Download button)
  - ``no_library``  — file present but library unavailable (offer pip install)
  - ``partial``     — multi-file model with some files present
  - ``fallback``    — primary model missing, fallback path active
"""

from __future__ import annotations

import os
from typing import Any


def _file_info(name: str, path: str) -> dict[str, Any]:
    """One row for the per-feature 'files' breakdown the JS UI renders."""
    exists = os.path.exists(path)
    return {
        "name": name,
        "exists": exists,
        "size_bytes": os.path.getsize(path) if exists else 0,
    }


def _ort_available() -> bool:
    try:
        import onnxruntime  # noqa: F401

        return True
    except ImportError:
        return False


# ── Per-group builders ──────────────────────────────────────────────


def _build_scrfd(scrfd_path: str, enabled: bool) -> dict[str, Any]:
    ok = os.path.exists(scrfd_path)
    return {
        "label": "SCRFD face detection",
        "description": (
            "InsightFace SCRFD 2.5GF — best multi-scale detector"
            " for babies, small and distant faces."
        ),
        "toggle_key": "model_scrfd",
        "enabled": enabled,
        "status": "ready" if ok else "missing",
        "size_bytes": os.path.getsize(scrfd_path) if ok else 0,
        "files": [_file_info("SCRFD 2.5g", scrfd_path)],
        "speed_impact": "low",
        "quality_impact": "Dramatically improves detection of small, distant, and baby faces",
    }


def _build_face_detect_fallback(yunet_path: str, blazeface_path: str) -> dict[str, Any]:
    files = [
        _file_info("YuNet (primary)", yunet_path),
        _file_info("BlazeFace short-range", blazeface_path),
    ]
    yunet_ok = os.path.exists(yunet_path)
    blaze_ok = os.path.exists(blazeface_path)
    return {
        "label": "Face detection (fallback)",
        "description": "OpenCV YuNet + MediaPipe BlazeFace — used when SCRFD is unavailable",
        "status": "ready" if yunet_ok or blaze_ok else "missing",
        "size_bytes": sum(f["size_bytes"] for f in files),
        "bundled": True,
        "files": files,
    }


def _build_blazeface_fr(fr_path: str, enabled: bool) -> dict[str, Any]:
    ok = os.path.exists(fr_path)
    return {
        "label": "BlazeFace full-range",
        "description": "Detects distant/small faces. Slower but more thorough.",
        "toggle_key": "model_blazeface_fr",
        "enabled": enabled,
        "status": "ready" if ok else "missing",
        "size_bytes": os.path.getsize(fr_path) if ok else 0,
        "files": [_file_info("BlazeFace full-range", fr_path)],
        "speed_impact": "high",
        "quality_impact": "Improves detection of small/distant faces",
    }


def _build_landmarker(landmarker_path: str, enabled: bool) -> dict[str, Any]:
    ok = os.path.exists(landmarker_path)
    return {
        "label": "Expression scoring",
        "description": "MediaPipe FaceLandmarker — scores blink, smile, frontality.",
        "toggle_key": "model_face_landmarker",
        "enabled": enabled,
        "status": "ready" if ok else "missing",
        "size_bytes": os.path.getsize(landmarker_path) if ok else 0,
        "files": [_file_info("FaceLandmarker", landmarker_path)],
        "speed_impact": "medium",
        "quality_impact": "Scores facial expressions (eyes open, smiling, facing camera)",
    }


def _build_hand(hand_path: str, enabled: bool) -> dict[str, Any]:
    ok = os.path.exists(hand_path)
    return {
        "label": "Hand suppression",
        "description": "MediaPipe HandLandmarker — filters hand false-positives.",
        "toggle_key": "model_hand_landmarker",
        "enabled": enabled,
        "status": "ready" if ok else "missing",
        "size_bytes": os.path.getsize(hand_path) if ok else 0,
        "files": [_file_info("HandLandmarker", hand_path)],
        "speed_impact": "medium",
        "quality_impact": "Reduces false face detections caused by hands",
    }


def _build_face_recognition(sface_path: str, face_embed_ok: bool) -> dict[str, Any]:
    sface_ok = os.path.exists(sface_path)
    return {
        "label": "Face recognition",
        "description": (
            "OpenCV SFace 128-d (primary, BSD)" + (" + dlib fallback" if face_embed_ok else "")
        ),
        "status": "ready" if sface_ok else ("fallback" if face_embed_ok else "missing"),
        "size_bytes": os.path.getsize(sface_path) if sface_ok else 0,
        "files": [_file_info("SFace recognition", sface_path)],
    }


def _build_segmentation(seg_path: str, enabled: bool) -> dict[str, Any]:
    ok = os.path.exists(seg_path)
    return {
        "label": "Subject segmentation",
        "description": "MediaPipe Selfie Segmenter — composition scoring for non-face photos.",
        "toggle_key": "model_segmentation",
        "enabled": enabled,
        "status": "ready" if ok else "missing",
        "size_bytes": os.path.getsize(seg_path) if ok else 0,
        "files": [_file_info("Selfie segmenter", seg_path)],
        "speed_impact": "low",
        "quality_impact": "Better composition scores for photos without faces",
    }


def _build_pose(pose_path: str, enabled: bool) -> dict[str, Any]:
    ok = os.path.exists(pose_path)
    return {
        "label": "Pose estimation",
        "description": "MediaPipe PoseLandmarker — body pose analysis.",
        "toggle_key": "model_pose",
        "enabled": enabled,
        "status": "ready" if ok else "missing",
        "size_bytes": os.path.getsize(pose_path) if ok else 0,
        "files": [_file_info("PoseLandmarker", pose_path)],
        "speed_impact": "medium",
        "quality_impact": "Analyzes body pose for action/portrait scoring",
    }


def _build_clip(clip_dir: str, enabled: bool, ort_ok: bool) -> dict[str, Any]:
    from pathlib import Path

    p = Path(clip_dir)
    files = [
        _file_info("CLIP visual", str(p / "clip-vit-b-32-visual.onnx")),
        _file_info("CLIP text", str(p / "clip-vit-b-32-text.onnx")),
        _file_info("CLIP vocabulary", str(p / "bpe_simple_vocab_16e6.txt.gz")),
    ]
    all_files = all(f["exists"] for f in files)
    any_files = any(f["exists"] for f in files)
    if all_files and ort_ok:
        status = "ready"
    elif all_files and not ort_ok:
        status = "no_library"
    elif any_files:
        status = "partial"
    else:
        status = "missing"
    return {
        "label": "Smart search & dedup",
        "description": "OpenAI CLIP ViT-B/32 (ONNX) · OpenAI 2021",
        "toggle_key": "model_clip",
        "enabled": enabled,
        "status": status,
        "size_bytes": sum(f["size_bytes"] for f in files),
        "lib_available": ort_ok,
        "install_hint": "pip install onnxruntime",
        "install_key": "onnxruntime",
        "files": files,
        "speed_impact": "high",
        "quality_impact": "Enables semantic search and visual deduplication",
    }


def _build_nudity(enabled: bool, available: bool) -> dict[str, Any]:
    return {
        "label": "Content filter",
        "description": "NudeNet ML classifier — detects sensitive content.",
        "toggle_key": "model_nudity",
        "enabled": enabled,
        "status": "ready" if available else "no_library",
        "size_bytes": 0,
        "lib_only": True,
        "lib_available": available,
        "install_hint": "pip install nudenet",
        "install_key": "nudity",
        "files": [],
        "speed_impact": "low",
        "quality_impact": "Detects and flags sensitive/NSFW content",
    }


def _build_inpaint(available: bool) -> dict[str, Any]:
    return {
        "label": "AI object removal",
        "description": "LaMa inpainting — remove unwanted objects from photos.",
        "status": "ready" if available else "no_library",
        "size_bytes": 0,
        "lib_only": True,
        "lib_available": available,
        "install_hint": "pip install bppicker[inpaint]",
        "install_key": "inpaint",
        "files": [],
    }


def _build_pets(pets_path: str, enabled: bool, lib_ok: bool) -> dict[str, Any]:
    file_ok = os.path.exists(pets_path)
    if file_ok and lib_ok:
        status = "ready"
    elif file_ok and not lib_ok:
        status = "no_library"
    else:
        status = "missing"
    return {
        "label": "Pet detection",
        "description": "YOLO11n (ONNX, COCO) · Ultralytics v8.3.0",
        "toggle_key": "model_pets",
        "enabled": enabled,
        "status": status,
        "size_bytes": os.path.getsize(pets_path) if file_ok else 0,
        "lib_available": lib_ok,
        "install_hint": "pip install onnxruntime",
        "install_key": "onnxruntime",
        "files": [_file_info("YOLO pet detector", pets_path)],
        "speed_impact": "low",
        "quality_impact": "Detects cats and dogs for smart pet albums",
    }


# ── Top-level builder ───────────────────────────────────────────────


def build_model_features(ctx: Any) -> list[dict[str, Any]]:
    """Build the full /api/models response from the live ctx.

    Imports the model-path constants lazily so this module is cheap
    to import in tests that don't need them.
    """
    from bpp.ai.inpainting import is_available as _inpaint_available
    from bpp.constants import MODEL_TOGGLE_KEYS

    # Each constant lives in its canonical module (face.py is split
    # across face_blazeface_fr / face_expression / face_hand_filter /
    # face_scrfd). Importing directly from each source avoids
    # depending on face.py's re-export list, which has drifted before.
    from bpp.scoring.face import _MODEL_PATH, _YUNET_MODEL_PATH
    from bpp.scoring.face_blazeface_fr import _FR_MODEL_PATH
    from bpp.scoring.face_embed import _SFACE_MODEL_PATH
    from bpp.scoring.face_embed import is_available as face_recognition_available
    from bpp.scoring.face_expression import _LANDMARKER_PATH
    from bpp.scoring.face_hand_filter import _HAND_MODEL_PATH
    from bpp.scoring.face_scrfd import _SCRFD_MODEL_PATH
    from bpp.scoring.nudity import is_available as nudenet_available
    from bpp.scoring.pets import _get_model_path as _pets_model_path
    from bpp.scoring.pets import is_available as pets_available
    from bpp.scoring.pose import _POSE_MODEL_PATH
    from bpp.scoring.segmentation import _SEGMENTER_PATH
    from bpp.utils.paths import cache_dir as _bpp_cache_dir

    clip_dir = _bpp_cache_dir()
    pets_path = _pets_model_path()
    face_embed_ok = face_recognition_available()
    pets_lib_ok = pets_available()
    ort_ok = _ort_available()

    toggles = {key: bool(ctx.config.get(key, True)) for key in MODEL_TOGGLE_KEYS}

    return [
        _build_scrfd(_SCRFD_MODEL_PATH, toggles["model_scrfd"]),
        _build_face_detect_fallback(_YUNET_MODEL_PATH, _MODEL_PATH),
        _build_blazeface_fr(_FR_MODEL_PATH, toggles["model_blazeface_fr"]),
        _build_landmarker(_LANDMARKER_PATH, toggles["model_face_landmarker"]),
        _build_hand(_HAND_MODEL_PATH, toggles["model_hand_landmarker"]),
        _build_face_recognition(_SFACE_MODEL_PATH, face_embed_ok),
        _build_segmentation(_SEGMENTER_PATH, toggles["model_segmentation"]),
        _build_pose(_POSE_MODEL_PATH, toggles["model_pose"]),
        _build_clip(str(clip_dir), toggles["model_clip"], ort_ok),
        _build_nudity(toggles["model_nudity"], nudenet_available()),
        _build_inpaint(_inpaint_available()),
        _build_pets(pets_path, toggles["model_pets"], pets_lib_ok),
    ]
