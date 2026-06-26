"""CLIP embedding extraction using ONNX Runtime (optional dependency).

When onnxruntime is installed and the CLIP ONNX model is available, this
module computes 512-dimensional image embeddings for semantic similarity.
Embeddings are L2-normalized at compute time so cosine similarity reduces
to a simple dot product.

The ONNX model is downloaded automatically on first use to
``~/.cache/bpp/clip-vit-b-32-visual.onnx``.

Thread safety
-------------
ONNX Runtime sessions are thread-safe, so sharing the singleton across
workers is fine (same pattern as ``scoring.nudity``).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from bpp.scoring.model_base import ModelSingleton
from bpp.utils.logging import get_logger

log = get_logger(__name__)

# CLIP ViT-B/32 preprocessing constants
_CLIP_SIZE = 224
_CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

from bpp.utils.paths import cache_dir as _cache_dir  # noqa: E402

_MODEL_DIR = _cache_dir()

# ── Model: OpenAI CLIP ViT-B/32 (visual + text encoders) ────────────
# What:   image-text contrastive embedding model used for free-text
#         photo search and (optionally) zero-shot category boosts.
# Where:  ONNX export from deepghs/clip_onnx on HuggingFace, mirroring
#         the official openai/clip-vit-base-patch32 weights.
# Why this one: CPU-friendly (ViT-B is the smallest ViT variant
#         OpenAI shipped); embeddings are 512-dim and cheap to store
#         per photo. Larger variants (ViT-L/14) would 4x the storage
#         and inference time without proportional precision gain on
#         a personal photo library.
# License: MIT (same as upstream openai/CLIP).
# Pinned:  initial release of bpp's CLIP feature.
# To bump: change URL + filename + SHA together; CLIP_OUTPUT_NAME and
#         tokenizer vocab in clip_tokenizer.py must match the new
#         export's output graph + tokenizer.
_MODEL_FILENAME = "clip-vit-b-32-visual.onnx"
_MODEL_URL = (
    "https://huggingface.co/deepghs/clip_onnx/resolve/main/"
    "openai/clip-vit-base-patch32/image_encode.onnx"
)
_MODEL_SHA256 = "58773462a749e7122def90d16eb159c20dff1f40b210f40ba834161ddd076f2e"

_TEXT_MODEL_FILENAME = "clip-vit-b-32-text.onnx"
_TEXT_MODEL_URL = (
    "https://huggingface.co/deepghs/clip_onnx/resolve/main/"
    "openai/clip-vit-base-patch32/text_encode.onnx"
)
_TEXT_MODEL_SHA256 = "2f07758ed6a9f05f8af341a80c486fcb547db499e8a5f5c79d8bc7e38d298156"


def _make_ort_session(path: Path | None):
    import onnxruntime as ort

    from bpp.scoring.onnx_providers import get_providers

    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = 2
    return ort.InferenceSession(str(path), opts, providers=get_providers())


_clip_visual = ModelSingleton(
    name="CLIP visual",
    model_path=_MODEL_DIR / _MODEL_FILENAME,
    model_url=_MODEL_URL,
    model_sha256=_MODEL_SHA256,
    create_fn=_make_ort_session,
    registry_id="openai_clip_vit_b32_onnx",
    import_check=lambda: __import__("onnxruntime"),
)

_clip_text = ModelSingleton(
    name="CLIP text",
    model_path=_MODEL_DIR / _TEXT_MODEL_FILENAME,
    model_url=_TEXT_MODEL_URL,
    model_sha256=_TEXT_MODEL_SHA256,
    create_fn=_make_ort_session,
    registry_id="openai_clip_vit_b32_onnx",
    import_check=lambda: __import__("onnxruntime"),
)


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """Return True if onnxruntime is importable and the visual model file exists."""
    return _clip_visual.is_available()


def text_is_available() -> bool:
    """Return True if onnxruntime is importable and the text model file exists."""
    return _clip_text.is_available()


def can_install() -> bool:
    """Return True if onnxruntime is importable (model can be downloaded)."""
    try:
        import onnxruntime  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------


def _model_path() -> Path:
    return _MODEL_DIR / _MODEL_FILENAME


def _text_model_path() -> Path:
    return _MODEL_DIR / _TEXT_MODEL_FILENAME


def ensure_model() -> Path:
    """Download the CLIP visual ONNX model if not present. Returns model path."""
    path = _clip_visual.ensure_model()
    return path if path is not None else _model_path()


def ensure_text_model() -> Path:
    """Download the CLIP text ONNX model if not present. Returns model path."""
    path = _clip_text.ensure_model()
    return path if path is not None else _text_model_path()


# ---------------------------------------------------------------------------
# Singleton session
# ---------------------------------------------------------------------------


def _get_session():
    """Return (and lazily create) the shared visual ONNX InferenceSession."""
    return _clip_visual.get()


def _get_text_session():
    """Return (and lazily create) the shared text ONNX InferenceSession."""
    return _clip_text.get()


# ---------------------------------------------------------------------------
# Image preprocessing
# ---------------------------------------------------------------------------


def _preprocess(image: np.ndarray) -> np.ndarray:
    """Preprocess an image for CLIP: resize, center crop, normalize.

    Args:
        image: BGR uint8 image (OpenCV format).

    Returns:
        float32 array of shape (1, 3, 224, 224).
    """
    # Convert BGR to RGB
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB) if len(image.shape) == 3 else image

    # Resize shortest side to 224, then center crop
    h, w = rgb.shape[:2]
    scale = _CLIP_SIZE / min(h, w)
    new_h, new_w = int(h * scale), int(w * scale)
    resized = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)

    # Center crop
    y0 = (new_h - _CLIP_SIZE) // 2
    x0 = (new_w - _CLIP_SIZE) // 2
    cropped = resized[y0 : y0 + _CLIP_SIZE, x0 : x0 + _CLIP_SIZE]

    # Normalize to [0, 1], then apply CLIP mean/std
    tensor = cropped.astype(np.float32) / 255.0
    tensor = (tensor - _CLIP_MEAN) / _CLIP_STD

    # HWC -> CHW, add batch dimension
    tensor = tensor.transpose(2, 0, 1)[np.newaxis, ...]
    return tensor


# ---------------------------------------------------------------------------
# Embedding computation
# ---------------------------------------------------------------------------


def compute_clip_embedding(image: np.ndarray) -> np.ndarray | None:
    """Compute a 512-d CLIP embedding for a BGR image.

    Returns L2-normalized float32 array, or None on error.
    """
    try:
        session = _get_session()
        tensor = _preprocess(image)
        input_name = session.get_inputs()[0].name
        output_names = [o.name for o in session.get_outputs()]
        outputs = session.run(None, {input_name: tensor})
        # Select 'embeddings' (512-d projected) by name, never by index
        if "embeddings" not in output_names:
            raise RuntimeError(f"CLIP visual model missing 'embeddings' output: {output_names}")
        emb = outputs[output_names.index("embeddings")].flatten().astype(np.float32)
        # L2-normalize
        norm = np.linalg.norm(emb)
        if norm > 0:
            emb = emb / norm
        return emb
    except Exception as e:
        log.warning("CLIP embedding failed: %s", e)
        return None


def compute_clip_embedding_from_file(filepath: str) -> np.ndarray | None:
    """Load an image and compute its CLIP embedding."""
    from bpp.scoring.aggregate import read_image_for_scoring

    # Canonical scoring image reader: cv2.imread → PIL fallback,
    # wrapped in retry_io for transient FS failures. Returns None
    # (with log.warning) on persistent failure.
    img = read_image_for_scoring(filepath)
    if img is None:
        return None
    return compute_clip_embedding(img)


# ---------------------------------------------------------------------------
# Text embedding
# ---------------------------------------------------------------------------


def compute_text_embedding(text: str) -> np.ndarray | None:
    """Compute a 512-d CLIP embedding for a text string.

    Returns L2-normalized float32 array, or None on error.
    """
    try:
        from bpp.scoring.clip_tokenizer import tokenize

        session = _get_text_session()
        tokens = tokenize(text)

        # Build feed dict from model's declared inputs
        input_names = [inp.name for inp in session.get_inputs()]
        feed: dict[str, np.ndarray] = {}
        for name in input_names:
            if "input_ids" in name or "text" in name:
                feed[name] = tokens.astype(np.int64)
            elif "attention_mask" in name:
                mask = (tokens != 0).astype(np.int64)
                feed[name] = mask
        if not feed:
            # Fallback: use the first input
            feed[input_names[0]] = tokens.astype(np.int64)

        outputs = session.run(None, feed)
        # Select 'embeddings' (projected) by name, never by index
        output_names = [o.name for o in session.get_outputs()]
        if "embeddings" not in output_names:
            raise RuntimeError(f"CLIP text model missing 'embeddings' output: {output_names}")
        emb = outputs[output_names.index("embeddings")].flatten().astype(np.float32)

        # L2-normalize
        norm = np.linalg.norm(emb)
        if norm > 0:
            emb = emb / norm
        return emb
    except Exception as e:
        log.warning("CLIP text embedding failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Similarity utilities
# ---------------------------------------------------------------------------


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two L2-normalized embeddings (= dot product)."""
    return float(np.dot(a, b))


def cosine_similarity_matrix(embeddings: np.ndarray) -> np.ndarray:
    """Pairwise cosine similarity matrix for a batch of L2-normalized embeddings.

    Args:
        embeddings: float32 array of shape (N, D).

    Returns:
        float32 array of shape (N, N) with similarity scores.
    """
    return embeddings @ embeddings.T


# ── Registry ───────────────────────────────────────────────────────
from bpp.scoring.model_base import ModelEntry, ModelRegistry  # noqa: E402

ModelRegistry.register(
    ModelEntry(
        name="CLIP visual",
        path=str(_MODEL_DIR / _MODEL_FILENAME),
        url=_MODEL_URL,
        sha256=_MODEL_SHA256,
        reset=_clip_visual.reset,
    )
)
ModelRegistry.register(
    ModelEntry(
        name="CLIP text",
        path=str(_MODEL_DIR / _TEXT_MODEL_FILENAME),
        url=_TEXT_MODEL_URL,
        sha256=_TEXT_MODEL_SHA256,
        reset=_clip_text.reset,
    )
)
