"""Build the /api/models response — one feature dict per ML model group.

Extracted from bp_core.api_models so the route handler is a thin
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
            "InsightFace SCRFD 2.5GF — optional extra detector. Off until"
            " you accept its license; YuNet + BlazeFace stay the default"
            " detectors."
        ),
        "toggle_key": "model_scrfd",
        "enabled": enabled,
        "status": "ready" if ok else "missing",
        "size_bytes": os.path.getsize(scrfd_path) if ok else 0,
        "files": [_file_info("SCRFD 2.5g", scrfd_path)],
        "speed_impact": "low",
        "quality_impact": "Can help find small or distant faces the default detectors miss",
    }


def _build_face_detect_fallback(yunet_path: str, blazeface_path: str) -> dict[str, Any]:
    files = [
        _file_info("YuNet (primary)", yunet_path),
        _file_info("BlazeFace short-range", blazeface_path),
    ]
    yunet_ok = os.path.exists(yunet_path)
    blaze_ok = os.path.exists(blazeface_path)
    return {
        "label": "Face detection",
        "description": "OpenCV YuNet + MediaPipe BlazeFace — the default detectors",
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
            "OpenCV SFace 128-d (primary)" + (" + dlib fallback" if face_embed_ok else "")
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


def _build_nudity(model_path: str, enabled: bool, available: bool) -> dict[str, Any]:
    file_ok = os.path.exists(model_path)
    if file_ok and available:
        status = "ready"
    elif file_ok:
        status = "no_library"
    else:
        status = "missing"
    return {
        "label": "Content filter",
        "description": "NudeNet 320n — downloaded only after you accept its license.",
        "toggle_key": "model_nudity",
        "enabled": enabled,
        "status": status,
        "size_bytes": os.path.getsize(model_path) if file_ok else 0,
        "lib_available": available,
        "install_hint": "pip install onnxruntime",
        "install_key": "onnxruntime",
        "files": [_file_info("NudeNet 320n", model_path)],
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
    from bpp.scoring.nudity import model_file as _nudenet_model_path
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
        # Default detectors first; research-only SCRFD is an opt-in extra.
        _build_face_detect_fallback(_YUNET_MODEL_PATH, _MODEL_PATH),
        _build_blazeface_fr(_FR_MODEL_PATH, toggles["model_blazeface_fr"]),
        _build_scrfd(_SCRFD_MODEL_PATH, toggles["model_scrfd"]),
        _build_landmarker(_LANDMARKER_PATH, toggles["model_face_landmarker"]),
        _build_hand(_HAND_MODEL_PATH, toggles["model_hand_landmarker"]),
        _build_face_recognition(_SFACE_MODEL_PATH, face_embed_ok),
        _build_segmentation(_SEGMENTER_PATH, toggles["model_segmentation"]),
        _build_pose(_POSE_MODEL_PATH, toggles["model_pose"]),
        _build_clip(str(clip_dir), toggles["model_clip"], ort_ok),
        _build_nudity(str(_nudenet_model_path()), toggles["model_nudity"], nudenet_available()),
        _build_inpaint(_inpaint_available()),
        _build_pets(pets_path, toggles["model_pets"], pets_lib_ok),
        *_build_plugin_models(),
    ]


def _build_plugin_models() -> list[dict[str, Any]]:
    """One row per model file a plugin registered, so it gets the same
    on-disk status and Redownload / Uninstall buttons as the built-ins
    (the endpoints look files up by name in the ModelRegistry). A file
    bpp can't fetch, or whose license isn't accepted yet, has no
    Download button but keeps Uninstall."""
    from bpp.registry.model_registry import get_entry, status_behavior
    from bpp.scoring.model_lifecycle import (
        bundled_path_of,
        fetchable_legal_id,
        file_present,
        plugin_model_entries,
    )
    from bpp.web.model_filter import license_state

    rows = []
    for e in plugin_model_entries():
        info = _file_info(e.name, os.path.expanduser(e.path))
        bundled = bundled_path_of(e)
        row: dict[str, Any] = {
            "label": f"Plugin model: {e.name}",
            "description": "Registered by a plugin.",
            "status": "ready" if file_present(e.path, bundled) else "missing",
            "size_bytes": info["size_bytes"],
            "files": [info],
        }
        if bundled:
            row["bundled"] = True
        legal_id = fetchable_legal_id(e)
        if legal_id is None:
            row["no_download"] = True
            row["download_hint"] = "managed by its plugin"
        elif not status_behavior(get_entry(legal_id).status).new_download_allowed:
            row["no_download"] = True
            row["download_hint"] = "no longer offered for download"
        elif license_state(get_entry(legal_id)) != "ok":
            row["no_download"] = True
            row["download_hint"] = "accept its license in Settings → Models first"
        rows.append(row)
    return rows
