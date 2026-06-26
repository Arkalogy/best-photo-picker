"""Pet detection using YOLOv8n ONNX model.

Detects cats and dogs in photos using a lightweight YOLOv8n model running
via ONNX Runtime. The model (~6 MB) is downloaded automatically on first use.

This module follows the same pattern as ``nudity.py``: lazy singleton model,
thread-safe inference, and a simple ``is_available()`` guard.

COCO class IDs: 15 = cat, 16 = dog.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from bpp.scoring.model_base import ModelSingleton
from bpp.utils.logging import get_logger

log = get_logger(__name__)

# COCO class IDs for pets
_CAT_CLASS = 15
_DOG_CLASS = 16
_PET_CLASSES = {_CAT_CLASS, _DOG_CLASS}
_CLASS_NAMES = {_CAT_CLASS: "cat", _DOG_CLASS: "dog"}

# ── Model: YOLOv11 nano (yolo11n.onnx) ─────────────────────────────
# What:   general object detector trained on COCO (80 classes). bpp
#         only consumes two: COCO class 15 (cat) and class 16 (dog),
#         see _PET_CLASSES above. Used to label photos with pets and
#         power the per-pet album.
# Where:  ultralytics/assets GitHub release (the official YOLO
#         distribution).
# Why this one: yolo11n is the smallest YOLOv11 variant (~10MB) —
#         picking up clear cats / dogs is well within its capacity
#         and the larger s/m/l/x variants would 5-50x the inference
#         time without meaningfully better recall on a personal
#         photo library. `v8.3.0` is the YOLOv11 release tag (the
#         repo is named "ultralytics/assets" but ships YOLOv11
#         alongside legacy YOLOv8 weights).
# License: AGPL-3.0 (Ultralytics) — bpp's [pets] extra is opt-in for
#         this reason; non-AGPL deployments should skip the extra.
# To bump: replace v8.3.0 + filename + SHA together; class IDs 15
#         and 16 are stable across COCO so no caller-side changes.
_MODEL_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.onnx"
_MODEL_SHA256 = "634279b40c07c6391472c51ad45b81ebc48706a9a1fe72dd3396322acd0c053b"
_MODEL_FILENAME = "yolo11n.onnx"
_INPUT_SIZE = 1024
_CONFIDENCE_THRESHOLD = 0.2
_NMS_IOU_THRESHOLD = 0.45

# Tiled detection for small pets in high-res images
_TILE_SIZE = 640
_TILE_OVERLAP = 0.25
_TILE_MIN_IMAGE_PX = 800  # skip tiling on images smaller than this


def _get_model_dir() -> str:
    """Return the directory where ONNX models are cached.

    Centralised in bpp.utils.paths so BPP_CACHE_DIR / BPP_MODELS_DIR
    overrides apply uniformly across face, pose, segmentation, pets,
    and CLIP.
    """
    from bpp.utils.paths import models_dir

    d = str(models_dir())
    os.makedirs(d, exist_ok=True)
    return d


def _get_model_path() -> str:
    return os.path.join(_get_model_dir(), _MODEL_FILENAME)


def _create_yolo_session(path: Path | None):
    import onnxruntime

    from bpp.scoring.onnx_providers import get_providers

    return onnxruntime.InferenceSession(str(path), providers=get_providers())


from bpp.utils.paths import models_dir as _models_dir  # noqa: E402

_yolo = ModelSingleton(
    name="YOLOv8n pet detector",
    model_path=_models_dir() / _MODEL_FILENAME,
    model_url=_MODEL_URL,
    model_sha256=_MODEL_SHA256,
    create_fn=_create_yolo_session,
    registry_id="ultralytics_yolov11n_pets",
    import_check=lambda: __import__("onnxruntime"),
)


def is_available() -> bool:
    """Return True if onnxruntime is importable."""
    return _yolo.is_available()


def ensure_model() -> str:
    """Download the YOLO model if not cached. Returns model path.

    Respects XDG_CACHE_HOME so tests can redirect model storage.

    cached files are SHA-verified before reuse. Was missing
    from this custom helper (touched YuNet/SFace but
    not pets). Tampered ONNX bytes would otherwise load silently
    into the YOLO inference path — the same supply-chain bypass
    the integrity check was supposed to prevent.
    """
    from bpp.scoring.model_base import ModelIntegrityError
    from bpp.utils.download import download_file, verify_existing

    model_path = _get_model_path()
    if os.path.exists(model_path):
        # Verify cached bytes — propagate ModelIntegrityError
        verify_existing(model_path, sha256=_MODEL_SHA256)
        return model_path

    log.info("Downloading YOLOv8n model to %s ...", model_path)

    tmp_path = model_path + ".tmp"
    try:
        download_file(
            _MODEL_URL,
            tmp_path,
            registry_id="ultralytics_yolov11n_pets",
            sha256=_MODEL_SHA256,
        )
        os.rename(tmp_path, model_path)
        log.info("Model downloaded (%d KB)", os.path.getsize(model_path) // 1024)
    except ModelIntegrityError:
        # Loud failure — propagate.
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        log.error("YOLO model integrity failure", exc_info=True)
        raise
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        log.warning("Failed to download YOLOv8n model", exc_info=True)
        raise

    return model_path


def _get_session():
    """Return (and lazily create) the ONNX inference session.

    Enforces the registry policy gate FIRST. YOLOv11n is licensed
    under AGPL-3.0; the click-through acceptance dialog must have
    been completed (with separate-rights assertion if the user is
    in commercial mode) before the weights load. Raises
    :class:`bpp.registry.ModelLoadBlockedError` otherwise.
    """
    from bpp.registry import enforce_load_policy_for

    enforce_load_policy_for("ultralytics_yolov11n_pets")
    return _yolo.get()


def _preprocess(
    img: np.ndarray, input_size: int = _INPUT_SIZE
) -> tuple[np.ndarray, float, int, int]:
    """Letterbox and normalize image for YOLO input.

    Returns (blob, scale, pad_x, pad_y).
    """
    h, w = img.shape[:2]
    scale = min(input_size / w, input_size / h)
    new_w, new_h = int(w * scale), int(h * scale)
    resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

    # Letterbox: center on input_size x input_size canvas
    pad_x = (input_size - new_w) // 2
    pad_y = (input_size - new_h) // 2
    canvas = np.full((input_size, input_size, 3), 114, dtype=np.uint8)
    canvas[pad_y : pad_y + new_h, pad_x : pad_x + new_w] = resized

    # HWC BGR -> CHW RGB, float32, normalized 0-1
    blob = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
    blob = blob[np.newaxis]  # add batch dim
    return blob, scale, pad_x, pad_y


def _postprocess(
    output: np.ndarray,
    scale: float,
    pad_x: int,
    pad_y: int,
    orig_w: int,
    orig_h: int,
    conf_threshold: float = _CONFIDENCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Parse YOLOv8 output into pet detections.

    YOLOv8 output shape: (1, 84, N) — transposed to (N, 84).
    Each row: [cx, cy, w, h, class0_conf, class1_conf, ...].
    N depends on input size (8400 at 640px, 21504 at 1024px).
    """
    # output shape: (1, 84, N) -> (N, 84)
    if output.shape[1] != 84:
        log.error("Unexpected YOLO output shape %s (expected dim 1 = 84)", output.shape)
        return []
    preds = output[0].T

    # Extract boxes and class scores
    boxes = preds[:, :4]  # cx, cy, w, h
    scores = preds[:, 4:]  # 80 class scores

    # Filter to pet classes only
    pet_class_indices = list(_PET_CLASSES)
    pet_scores = scores[:, pet_class_indices]  # (N, 2)
    max_pet_scores = pet_scores.max(axis=1)  # (N,)
    mask = max_pet_scores > conf_threshold

    if not mask.any():
        return []

    filtered_boxes = boxes[mask]
    filtered_pet_scores = pet_scores[mask]
    filtered_max_scores = max_pet_scores[mask]
    filtered_class_idx = filtered_pet_scores.argmax(axis=1)
    filtered_class_ids = np.array(pet_class_indices)[filtered_class_idx]

    # Convert cx,cy,w,h -> x1,y1,x2,y2
    cx, cy, bw, bh = filtered_boxes.T
    x1 = cx - bw / 2
    y1 = cy - bh / 2
    x2 = cx + bw / 2
    y2 = cy + bh / 2

    # Remove letterbox padding and rescale to original image
    x1 = (x1 - pad_x) / scale
    y1 = (y1 - pad_y) / scale
    x2 = (x2 - pad_x) / scale
    y2 = (y2 - pad_y) / scale

    # Clip to image bounds
    x1 = np.clip(x1, 0, orig_w)
    y1 = np.clip(y1, 0, orig_h)
    x2 = np.clip(x2, 0, orig_w)
    y2 = np.clip(y2, 0, orig_h)

    # NMS per class (so overlapping cat+dog both survive)
    rects_all = np.stack([x1, y1, x2, y2], axis=1)
    kept_indices: list[int] = []
    for cid in np.unique(filtered_class_ids):
        cls_mask = filtered_class_ids == cid
        cls_idx = np.where(cls_mask)[0]
        cls_rects = rects_all[cls_mask].tolist()
        cls_confs = filtered_max_scores[cls_mask].tolist()
        nms_idx = cv2.dnn.NMSBoxes(cls_rects, cls_confs, conf_threshold, _NMS_IOU_THRESHOLD)
        if len(nms_idx) > 0:
            kept_indices.extend(cls_idx[nms_idx.flatten()].tolist())

    if not kept_indices:
        return []

    detections = []
    for i in kept_indices:
        detections.append(
            {
                "class": _CLASS_NAMES[int(filtered_class_ids[i])],
                "class_id": int(filtered_class_ids[i]),
                "confidence": float(filtered_max_scores[i]),
                "bbox_x": round(float(x1[i])),
                "bbox_y": round(float(y1[i])),
                "bbox_w": round(float(x2[i] - x1[i])),
                "bbox_h": round(float(y2[i] - y1[i])),
            }
        )

    return detections


def _detect_single_pass(
    img: np.ndarray,
    session: Any,
    input_size: int = _INPUT_SIZE,
    conf_threshold: float = _CONFIDENCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Run a single YOLO inference pass on an image."""
    h, w = img.shape[:2]
    blob, scale, pad_x, pad_y = _preprocess(img, input_size=input_size)
    input_name = session.get_inputs()[0].name
    output = session.run(None, {input_name: blob})[0]
    return _postprocess(output, scale, pad_x, pad_y, w, h, conf_threshold=conf_threshold)


def _tiled_detect(
    img: np.ndarray,
    session: Any,
    conf_threshold: float = _CONFIDENCE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Run YOLO on overlapping tiles to catch small pets.

    Uses _TILE_SIZE (640) tiles with 25% overlap, then merges results
    with NMS to remove duplicates across tile boundaries.
    """
    h, w = img.shape[:2]
    if h < _TILE_MIN_IMAGE_PX or w < _TILE_MIN_IMAGE_PX:
        return []

    stride = int(_TILE_SIZE * (1 - _TILE_OVERLAP))
    all_detections: list[dict[str, Any]] = []

    for y0 in range(0, max(1, h - _TILE_SIZE // 2), stride):
        for x0 in range(0, max(1, w - _TILE_SIZE // 2), stride):
            y1 = min(y0 + _TILE_SIZE, h)
            x1 = min(x0 + _TILE_SIZE, w)
            tile = img[y0:y1, x0:x1]

            th, tw = tile.shape[:2]
            if th < 200 or tw < 200:
                continue

            dets = _detect_single_pass(
                tile, session, input_size=_TILE_SIZE, conf_threshold=conf_threshold
            )
            # Remap bbox to full-image coordinates
            for d in dets:
                d["bbox_x"] += x0
                d["bbox_y"] += y0
            all_detections.extend(dets)

    if not all_detections:
        return []

    # NMS across tiles to remove duplicate detections at boundaries
    rects = [
        [d["bbox_x"], d["bbox_y"], d["bbox_x"] + d["bbox_w"], d["bbox_y"] + d["bbox_h"]]
        for d in all_detections
    ]
    confs = [d["confidence"] for d in all_detections]
    class_ids = [d["class_id"] for d in all_detections]

    kept: list[dict[str, Any]] = []
    for cid in set(class_ids):
        cls_idx = [i for i, c in enumerate(class_ids) if c == cid]
        cls_rects = [rects[i] for i in cls_idx]
        cls_confs = [confs[i] for i in cls_idx]
        nms_idx = cv2.dnn.NMSBoxes(cls_rects, cls_confs, conf_threshold, _NMS_IOU_THRESHOLD)
        if len(nms_idx) > 0:
            for i in nms_idx.flatten():
                kept.append(all_detections[cls_idx[i]])
    return kept


def detect_pets(
    img: np.ndarray,
    *,
    input_size: int = _INPUT_SIZE,
    conf_threshold: float = _CONFIDENCE_THRESHOLD,
    enable_tiling: bool = False,
) -> dict[str, Any]:
    """Detect cats and dogs in an image (BGR numpy array).

    Args:
        input_size: YOLO input resolution (higher catches smaller pets).
        conf_threshold: Minimum confidence for a detection.
        enable_tiling: If True, run tiled detection as fallback when no
            pets found in the full-image pass. Default False — empirical
            tuning sweep on a real library found tiling at conf=0.2 on
            640px tiles produces ~80% false-positive rate on pet-free
            photos (12 of 15 random "no-pet" photos hallucinated cats /
            dogs). The single-pass at default settings already catches
            the user's reported small-cat miss case (IMG_1470.HEIC), so
            tiling buys recall we don't need at a precision cost we
            can't afford. Re-enable per-call only when you specifically
            need maximum recall on small/distant subjects.

    Returns a dict with keys: pet_count, has_cat, has_dog, pet_detections.
    """
    session = _get_session()

    detections = _detect_single_pass(
        img, session, input_size=input_size, conf_threshold=conf_threshold
    )

    # Tiled fallback: if nothing found, try overlapping tiles
    if not detections and enable_tiling:
        detections = _tiled_detect(img, session, conf_threshold=conf_threshold)

    has_cat = any(d["class"] == "cat" for d in detections)
    has_dog = any(d["class"] == "dog" for d in detections)

    return {
        "pet_count": len(detections),
        "has_cat": int(has_cat),
        "has_dog": int(has_dog),
        "pet_detections": detections,
    }


def detect_pets_from_file(
    filepath: str,
    max_long_side: int = 1024,
    *,
    input_size: int = _INPUT_SIZE,
    conf_threshold: float = _CONFIDENCE_THRESHOLD,
) -> dict[str, Any]:
    """Convenience: load image from path and detect pets.

    Returns the same dict as detect_pets, with empty results on failure.
    """
    from bpp.scoring.aggregate import read_image_for_scoring
    from bpp.scoring.model_base import ModelIntegrityError

    try:
        # Canonical scoring image reader: cv2.imread → PIL fallback,
        # wrapped in retry_io for transient FS failures. Returns None
        # (with log.warning) on persistent failure.
        img = read_image_for_scoring(filepath)
        if img is None:
            return {"pet_count": 0, "has_cat": 0, "has_dog": 0, "pet_detections": []}

        # Downscale for speed
        h, w = img.shape[:2]
        long_side = max(h, w)
        if long_side > max_long_side:
            s = max_long_side / long_side
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)

        return detect_pets(img, input_size=input_size, conf_threshold=conf_threshold)
    except ModelIntegrityError:
        # integrity failure must propagate, not silently
        # report "no pets detected" for every photo.
        log.error("Pet model integrity failure on %s", filepath, exc_info=True)
        raise
    except Exception as e:
        # Warning-level so a runtime fault (CUDA OOM, mid-run model
        # unload, etc.) surfaces in server.log. ModelIntegrityError is
        # already raised above; this branch only fires for environmental
        # / transient issues that we still want operators to see.
        log.warning("Pet detection failed for %s: %s", filepath, e)
        return {"pet_count": 0, "has_cat": 0, "has_dog": 0, "pet_detections": []}


# ── Registry ───────────────────────────────────────────────────────
# Path is computed via _get_model_path() because BPP_MODELS_DIR can
# move it. Evaluated at module import — env vars are set by then.
from bpp.scoring.model_base import ModelEntry, ModelRegistry  # noqa: E402

ModelRegistry.register(
    ModelEntry(
        name="YOLO pet detector",
        path=_get_model_path(),
        url=_MODEL_URL,
        sha256=_MODEL_SHA256,
        reset=_yolo.reset,
    )
)
