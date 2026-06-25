"""SFace recognizer runtime — model load, license gate, and the
per-thread recognizer singleton, split out of :mod:`bpp.scoring.face_embed`
for the 500-LOC cap. ``face_embed`` re-exports every public name here so
existing import paths keep working.

ARCHITECTURE INVARIANT — DO NOT "MODERNIZE" TO ModelSingleton.
SFace uses per-thread storage (``_sface_tls``) because
``cv2.FaceRecognizerSF.feature()`` mutates internal DNN buffers and is
not thread-safe; ThreadPoolExecutor workers running face extraction in
parallel would corrupt each other's embeddings. Per-thread instances +
a one-time download lock + a negative-cache flag is the working pattern
(same exemption as YuNet/dlib; per the project conventions; regression gate at
tests/test_face_thread_safety.py).
"""

from __future__ import annotations

import os
import threading

import cv2
import numpy as np

from bpp.constants import FACE_CONFIDENCE_FLOOR
from bpp.scoring.face_embed_landmarks import _face_quality_from_landmarks
from bpp.scoring.model_load_gate import MemoizedLoadGate
from bpp.utils.logging import get_logger

# route through models_dir() so BPP_CACHE_DIR / BPP_MODELS_DIR
# overrides apply uniformly with every other model.
from bpp.utils.paths import models_dir as _sface_models_dir

log = get_logger(__name__)

# Scale factor applied to L2-normalized SFace embeddings so that
# Euclidean distances fall in the same range as dlib's face_recognition:
#   dlib:  same-person ~0.4,  different ~0.9,  default threshold 0.55
#   SFace: same-person ~0.52, different ~0.88  (after scaling by 0.65)
# This lets the existing clustering threshold slider (0.3-1.0) work unchanged.
SFACE_DISTANCE_SCALE = 0.65


# ── SFace recognizer singleton ──

_sface_tls = threading.local()  # per-thread recognizer instances
_SFACE_AVAILABLE: bool | None = None
_sface_lock = threading.Lock()

# Load-time license gate for SFace (``sface_yunet``). ``download_file``
# already gates SFace at *download* time, but ``_ensure_sface_model``
# returns early on a cache hit BEFORE reaching that path, and SFace has
# no other load-time check — so a revoked/absent acceptance (or weights
# that arrived by any non-download path) would otherwise load unchecked.
# This gate is the load-time enforcement point. Memoized per process via
# the shared MemoizedLoadGate (workers are per-run subprocesses, so a
# between-run accept/revoke re-evaluates).
_sface_gate = MemoizedLoadGate("sface_yunet")
#: Raises ModelLoadBlockedError when SFace isn't accepted; no-op once passed.
_enforce_sface_policy = _sface_gate.enforce


_SFACE_MODEL_PATH = str(_sface_models_dir() / "face_recognition_sface_2021dec.onnx")
# ── Model: SFace face recognition (December 2021 release) ──────────
# What:   converts a detected face crop (typically from YuNet) into a
#         128-dim L2-normalized embedding. Two faces are "the same
#         person" if cosine distance < ~0.55. Powers the per-cluster
#         person tab.
# Where:  opencv_zoo, same source as YuNet.
# Why this one: SFace is the OpenCV-recommended embedder, paired
#         with YuNet for an end-to-end OpenCV face pipeline. Smaller
#         and faster than dlib's face_recognition while delivering
#         comparable cluster purity on personal libraries (~5-50k
#         photos).
# License: Apache 2.0 (opencv_zoo).
# Pinned:  2021dec — long-term-stable tag, no newer dated release
#         in opencv_zoo's main as of bpp's pin.
# To bump: replace 2021dec with a newer tag; the 128-dim output
#         contract has been stable across SFace's history so cluster
#         IDs in existing libraries will continue to compare cleanly
#         after a re-extract.
_SFACE_MODEL_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
    "models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
)
_SFACE_MODEL_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"


def _ensure_sface_model() -> bool:
    """Download SFace ONNX model if needed. Returns True if ready.

    cached files are SHA-verified before reuse, same contract
    as YuNet's ensure helper. ModelIntegrityError propagates instead
    of being downgraded to "model unavailable" — a tampered cache
    must surface as a loud failure, not silent fallback to dlib.
    """
    from bpp.registry import ModelLoadBlockedError
    from bpp.scoring.model_base import ModelIntegrityError
    from bpp.utils.download import download_file, verify_existing

    # Load-time license gate — MUST run before the cache-hit early return
    # below, which would otherwise bypass the download-time gate for
    # already-present weights. Fail-closed: a block downgrades to
    # "unavailable" + fallback to dlib (same as a missing model).
    try:
        _enforce_sface_policy()
    except ModelLoadBlockedError as exc:
        log.warning("SFace model unavailable: %s", exc)
        return False

    if os.path.exists(_SFACE_MODEL_PATH):
        verify_existing(_SFACE_MODEL_PATH, sha256=_SFACE_MODEL_SHA256)
        return True
    try:
        os.makedirs(os.path.dirname(_SFACE_MODEL_PATH), exist_ok=True)
        tmp = _SFACE_MODEL_PATH + ".tmp"
        download_file(
            _SFACE_MODEL_URL,
            tmp,
            registry_id="sface_yunet",
            sha256=_SFACE_MODEL_SHA256,
        )
        os.replace(tmp, _SFACE_MODEL_PATH)
        log.info("Downloaded SFace model to %s", _SFACE_MODEL_PATH)
        return True
    except ModelIntegrityError:
        tmp = _SFACE_MODEL_PATH + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        log.error("SFace model integrity failure", exc_info=True)
        raise
    except Exception as exc:
        tmp = _SFACE_MODEL_PATH + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        log.warning("SFace model unavailable: %s", exc)
        return False


def _sface_available() -> bool:
    """Check if SFace model file exists or can be downloaded."""
    global _SFACE_AVAILABLE
    if _SFACE_AVAILABLE is not None:
        return _SFACE_AVAILABLE
    avail = os.path.exists(_SFACE_MODEL_PATH) or hasattr(cv2, "FaceRecognizerSF")
    return avail


def _get_sface_recognizer() -> cv2.FaceRecognizerSF | None:
    """Get or create a per-thread SFace recognizer.

    Each thread gets its own recognizer via thread-local storage
    so that ThreadPoolExecutor workers don't share mutable DNN state.
    """
    global _SFACE_AVAILABLE
    if _SFACE_AVAILABLE is False:
        return None

    # Check thread-local cache
    rec = getattr(_sface_tls, "recognizer", None)
    if rec is not None:
        return rec

    with _sface_lock:
        if _SFACE_AVAILABLE is False:
            return None
        if not _ensure_sface_model():
            _SFACE_AVAILABLE = False
            return None
    # Create per-thread recognizer outside the lock
    try:
        rec = cv2.FaceRecognizerSF.create(_SFACE_MODEL_PATH, "")
        _sface_tls.recognizer = rec
        _SFACE_AVAILABLE = True
        return rec
    except Exception as exc:
        log.warning("SFace recognizer creation failed: %s", exc)
        _SFACE_AVAILABLE = False
        return None


def ensure_sface_model() -> list[str]:
    """Pre-download SFace model. Returns list of warnings."""
    warnings = []
    if not _ensure_sface_model():
        warnings.append("SFace model unavailable — face recognition will use dlib fallback")
    return warnings


def extract_embedding_for_region(
    image: np.ndarray,
    bbox: tuple[int, int, int, int],
    *,
    expand_pct: float = 0.3,
    min_overlap: float = 0.5,
) -> dict | None:
    """Extract a 128-d embedding for a user-supplied bbox.

    Used by the drag-to-fix-face-bbox flow: the user dragged a wrong
    detection onto what they claim is the real face. We re-run YuNet
    inside the user's bbox (expanded by ``expand_pct`` so the detector
    has context); if a face is found and at least ``min_overlap`` of
    the smaller box (face or user) lies inside the larger one, we
    trust the real landmarks for SFace alignment. This tolerates loose
    user boxes (much larger than the actual face) and tight ones alike.

    **We deliberately do NOT fall back to synthesized landmarks.** SFace
    will happily produce a 128-d vector from any image crop (strawberries,
    wood grain, etc.), and that vector will land near _some_ cluster
    centroid — the matcher then "recognizes" the blanket as a person.
    If YuNet can't see a face in the region, the right answer is "no
    face here" — the caller surfaces that to the user as a 422 so they
    can retry with a tighter box around the actual face.

    Returns ``{bbox, embedding, quality, method}`` on success (method is
    always ``"yunet"`` in the current implementation) or ``None`` if
    SFace is unavailable or no face is detected in the user's region.
    """
    recognizer = _get_sface_recognizer()
    if recognizer is None:
        return None

    ih, iw = image.shape[:2]
    bx, by, bw, bh = bbox
    if bw <= 0 or bh <= 0:
        return None
    bx = max(0, min(bx, iw - 1))
    by = max(0, min(by, ih - 1))
    bw = max(1, min(bw, iw - bx))
    bh = max(1, min(bh, ih - by))

    ex = max(0, int(bx - bw * expand_pct))
    ey = max(0, int(by - bh * expand_pct))
    ew = min(iw - ex, int(bw * (1.0 + 2.0 * expand_pct)))
    eh = min(ih - ey, int(bh * (1.0 + 2.0 * expand_pct)))
    if ew <= 0 or eh <= 0:
        return None

    try:
        from bpp.scoring.face_yunet import _yunet_detect_raw

        crop = image[ey : ey + eh, ex : ex + ew]
        raw_faces = _yunet_detect_raw(crop, min_confidence=FACE_CONFIDENCE_FLOOR)
    except Exception as exc:
        log.debug("YuNet detection inside user bbox failed: %s", exc)
        return None

    if raw_faces is None or len(raw_faces) == 0:
        log.info(
            "Drag-bbox: no face detected at user region (%d,%d,%d,%d)",
            bx,
            by,
            bw,
            bh,
        )
        return None

    # Pick the YuNet detection whose overlap with the user's box, relative
    # to whichever box is smaller, is highest. Strict IoU rejects loose
    # user boxes (drawing a large rectangle around a face area produces
    # low IoU even when the detected face is fully inside the box). The
    # "fraction of the smaller box inside the larger" metric handles
    # loose, tight, and over-sized user boxes alike.
    user_box_in_crop = (bx - ex, by - ey, bw, bh)
    user_area = max(1, bw * bh)
    best_face = None
    best_score = 0.0
    for face in raw_faces:
        fx, fy, fw, fh = int(face[0]), int(face[1]), int(face[2]), int(face[3])
        ix1 = max(fx, user_box_in_crop[0])
        iy1 = max(fy, user_box_in_crop[1])
        ix2 = min(fx + fw, user_box_in_crop[0] + user_box_in_crop[2])
        iy2 = min(fy + fh, user_box_in_crop[1] + user_box_in_crop[3])
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        if inter <= 0:
            continue
        face_area = max(1, fw * fh)
        score = inter / float(min(face_area, user_area))
        if score > best_score:
            best_score = score
            best_face = face

    if best_face is None or best_score < min_overlap:
        log.info(
            "Drag-bbox: best detection overlap=%.2f with user box (min=%.2f) — rejecting",
            best_score,
            min_overlap,
        )
        return None

    face_orig = best_face.astype(np.float32).copy()
    face_orig[0] += ex
    face_orig[1] += ey
    for li in range(4, 14, 2):
        face_orig[li] += ex
        face_orig[li + 1] += ey

    # Upside-down faces: YuNet detects them with hallucinated upright
    # landmarks; align against the 180°-rotated view so the embedding
    # matches the person's upright cluster (see detect_inverted_face).
    from bpp.scoring.face_embed_detect import detect_inverted_face

    align_image, align_row = image, face_orig
    inverted_row = detect_inverted_face(image, face_orig)
    if inverted_row is not None:
        align_image = np.ascontiguousarray(image[::-1, ::-1])
        align_row = inverted_row

    try:
        aligned = recognizer.alignCrop(align_image, align_row)
        emb = recognizer.feature(aligned)
    except Exception as exc:
        log.warning("SFace alignCrop failed on detected face inside user bbox: %s", exc)
        return None

    # SFace native dtype is float32; see schema v35 migration for the
    # historical f64 promotion that this dtype change replaces.
    emb = emb.flatten().astype(np.float32)
    norm = np.linalg.norm(emb)
    if norm > 0:
        emb = emb / norm * SFACE_DISTANCE_SCALE
    quality = _face_quality_from_landmarks(align_row, align_image)
    return {
        "bbox": (
            int(face_orig[0]),
            int(face_orig[1]),
            int(face_orig[2]),
            int(face_orig[3]),
        ),
        "embedding": emb,
        "quality": quality,
        "method": "yunet",
    }


# ── Registry ───────────────────────────────────────────────────────
from bpp.scoring.model_base import ModelEntry, ModelRegistry  # noqa: E402


def _reset_sface_cache() -> None:
    """SFace uses a module-global negative cache (no ModelSingleton).
    Clearing it forces the next embed call to retry init.

    Also re-arms the license gate so a reset re-evaluates acceptance
    (e.g. after a revoke); otherwise a passed gate would let the next
    load skip the check."""
    global _SFACE_AVAILABLE
    _SFACE_AVAILABLE = None
    _sface_gate.reset()


ModelRegistry.register(
    ModelEntry(
        name="SFace recognition",
        path=_SFACE_MODEL_PATH,
        url=_SFACE_MODEL_URL,
        sha256=_SFACE_MODEL_SHA256,
        reset=_reset_sface_cache,
    )
)
