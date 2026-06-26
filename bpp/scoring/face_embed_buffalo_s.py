"""InsightFace buffalo_s face embedder (w600k_mbf ArcFace 512-d).

Standalone module for the buffalo_s face recognition path. Sits next
to :mod:`bpp.scoring.face_embed` (which holds SFace + dlib) so the
restricted-license code is grep-able by license — the legal-posture
review wanted that visual separation between permissive and
restricted recognition paths.

How it works
------------

* On first use, ``ensure_buffalo_s_model()`` downloads ``buffalo_s.zip``
  from the canonical InsightFace v0.7 release, verifies the zip's
  SHA-256, extracts ``w600k_mbf.onnx`` (the 13.6 MB MobileFaceNet
  recognition model) into the BPP model cache, and verifies the
  extracted file's SHA-256. Both hashes are pinned; a tamper at
  either layer is loud and propagates.
* ``embed_face(image, face_box)`` crops the bbox, resizes to 112x112,
  normalizes pixel values to [-1, 1], runs the ONNX session, and
  returns the L2-normalized 512-d embedding.
* The policy gate fires BEFORE any of this: the loader calls
  :func:`bpp.registry.enforce_load_policy_for("insightface_buffalo_s")`
  on first use, so a user who hasn't completed the click-through
  dialog never sees the download begin.

Face alignment
--------------

When the caller passes a YuNet detection row (15-element array
containing bbox + 5 landmarks + confidence), the embedder uses
5-point similarity-transform alignment — the same alignment
InsightFace's own pipelines use, producing maximally compatible
embeddings with the upstream reference. The reference landmark
template lives in :data:`_ARCFACE_REF_POINTS`.

When the caller passes just a plain bbox (no landmarks), the
embedder falls back to bbox-crop-resize. Recognition accuracy is
somewhat lower in this path because the face may be rotated or
off-center inside the crop, but it still produces a usable
embedding for clustering.

License
-------

The InsightFace project code is MIT; the ``buffalo_s`` weights are
research-only / non-commercial per the project's README License
section. The registry entry ``insightface_buffalo_s`` carries
``commercial_use_restriction_known=True`` and
``requires_explicit_ack=True``; the runtime gate in
:mod:`bpp.registry.policy` enforces this before any code in this
module executes.
"""

from __future__ import annotations

import contextlib
import logging
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

from bpp.scoring.model_base import ModelSingleton

log = logging.getLogger(__name__)

# ── Model: InsightFace buffalo_s (w600k_mbf.onnx, research-only) ──────
#: Canonical download URL — InsightFace v0.7 release on GitHub.
#: The zip bundles 5 ONNX files; we only extract w600k_mbf.onnx.
BUFFALO_S_ZIP_URL = (
    "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip"
)

#: SHA-256 of the upstream buffalo_s.zip. Pinning the zip lets us
#: trust the extracted bytes by transitivity.
BUFFALO_S_ZIP_SHA256 = (
    # Computed 2026-06-03 against the v0.7 release artifact. Update
    # this hash if/when InsightFace re-publishes a bit-modified zip
    # under the same release tag.
    "d85a87f503f691807cd8bb97128bdf7a0660326cd9cd02657127fa978bab8b5e"
)

#: SHA-256 of w600k_mbf.onnx (the recognition model inside the zip).
#: Pinning the inner file directly catches both upstream changes and
#: zip-extraction tampering.
W600K_MBF_SHA256 = "9cc6e4a75f0e2bf0b1aed94578f144d15175f357bdc05e815e5c4a02b319eb4f"

#: Standard ArcFace input dimension.
_ARC_INPUT_SIZE = 112


#: Canonical ArcFace reference landmarks (frontal pose). Coordinates
#: are pixel positions inside the 112x112 aligned output. Order is
#: [subject's left eye, subject's right eye, nose, subject's left
#: mouth corner, subject's right mouth corner]. Values are the
#: standard InsightFace reference template — do not change without
#: a corresponding registry version bump (recognition embeddings
#: trained on the canonical alignment would no longer match).
_ARCFACE_REF_POINTS = np.array(
    [
        [38.2946, 51.6963],  # subject left eye  (image right)
        [73.5318, 51.5014],  # subject right eye (image left)
        [56.0252, 71.7366],  # nose
        [41.5493, 92.3655],  # subject left mouth
        [70.7299, 92.2041],  # subject right mouth
    ],
    dtype=np.float32,
)


#: Length of a complete YuNet detection row: 4 (bbox) + 10 (5
#: landmarks) + 1 (confidence) = 15. Used to distinguish "plain
#: bbox" callers from "full detection" callers.
_YUNET_ROW_LEN = 15

#: Output embedding dimension (MobileFaceNet w600k variant).
EMBEDDING_DIM = 512

#: Registry id this loader corresponds to. The runtime policy gate
#: looks up this entry before any model file is touched.
REGISTRY_ID = "insightface_buffalo_s"


def _model_cache_dir() -> Path:
    """Where to cache the extracted ONNX file. Honors BPP_MODELS_DIR
    via the same helper the other models use."""
    from bpp.utils.paths import models_dir

    return Path(models_dir()) / "buffalo_s"


def _extracted_model_path() -> Path:
    return _model_cache_dir() / "w600k_mbf.onnx"


def is_available() -> bool:
    """Return True if onnxruntime + numpy are importable.

    Does NOT check whether the model file is downloaded — that's
    the loader's job. ``is_available`` is the "could this run at
    all" probe used by the picker and CLI surface tests.
    """
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def is_on_disk() -> bool:
    """Return True if the extracted buffalo_s ONNX file exists locally.

    Cheap existence check only — does NOT verify the SHA. The picker
    calls this to decide whether to surface "Download (~121.7 MB)" or
    "Use this model" as the next step. A tampered cache is caught and
    re-downloaded at load time by ``ensure_buffalo_s_model``; the
    picker never deletes files.
    """
    return _extracted_model_path().exists()


def remove_local_weights() -> int:
    """Delete the cached buffalo_s ONNX (and any orphan zip tmp).

    Returns the number of bytes freed (0 if nothing was on disk).
    Also resets the in-process ModelSingleton so the next load
    re-runs the ensure → download → verify chain rather than
    handing back the now-deleted Path.

    Used by the picker's Uninstall action for catalog entries.
    Symmetric counterpart to :func:`ensure_buffalo_s_model`.
    """
    import contextlib

    freed = 0
    extracted = _extracted_model_path()
    if extracted.exists():
        freed += extracted.stat().st_size
        with contextlib.suppress(OSError):
            extracted.unlink()
    zip_tmp = extracted.with_suffix(".zip.tmp")
    if zip_tmp.exists():
        with contextlib.suppress(OSError):
            zip_tmp.unlink()
    _buffalo_s_model.reset()
    return freed


def ensure_buffalo_s_model() -> str:
    """Download + extract + verify the buffalo_s recognition model.

    Returns the path to the verified ONNX file. Raises
    :class:`bpp.scoring.model_base.ModelIntegrityError` on a hash
    mismatch at either the zip layer or the extracted-file layer.

    Idempotent: if the cached file is already on disk and its hash
    matches, no network call is made.
    """
    from bpp.scoring.model_base import ModelIntegrityError
    from bpp.utils.download import download_file, verify_existing

    extracted_path = _extracted_model_path()
    if extracted_path.exists():
        try:
            verify_existing(str(extracted_path), sha256=W600K_MBF_SHA256)
            return str(extracted_path)
        except ModelIntegrityError:
            log.warning("Cached buffalo_s model fails integrity check; re-downloading")
            with contextlib.suppress(OSError):
                extracted_path.unlink()

    extracted_path.parent.mkdir(parents=True, exist_ok=True)
    zip_tmp = extracted_path.with_suffix(".zip.tmp")
    log.info(
        "Downloading buffalo_s.zip (122 MB) — first-use of the InsightFace face recognition path"
    )
    try:
        download_file(
            BUFFALO_S_ZIP_URL,
            str(zip_tmp),
            registry_id=REGISTRY_ID,
            sha256=BUFFALO_S_ZIP_SHA256,
        )
    except ModelIntegrityError:
        if zip_tmp.exists():
            zip_tmp.unlink()
        raise
    except Exception:
        if zip_tmp.exists():
            zip_tmp.unlink()
        raise

    try:
        with zipfile.ZipFile(zip_tmp) as zf:
            try:
                zf.extract("w600k_mbf.onnx", path=str(extracted_path.parent))
            except KeyError as exc:
                raise ModelIntegrityError(
                    "buffalo_s.zip does not contain w600k_mbf.onnx — "
                    "upstream may have restructured the bundle. "
                    f"Got entries: {zf.namelist()}"
                ) from exc
    finally:
        if zip_tmp.exists():
            with contextlib.suppress(OSError):
                zip_tmp.unlink()

    verify_existing(str(extracted_path), sha256=W600K_MBF_SHA256)
    log.info(
        "buffalo_s ready at %s (%.1f MB)",
        extracted_path,
        extracted_path.stat().st_size / (1024 * 1024),
    )
    return str(extracted_path)


def _buffalo_s_create(_path: Path | None) -> Any:
    """``ModelSingleton.create_fn`` for buffalo_s.

    The path argument is ignored — buffalo_s downloads and extracts
    a ZIP via :func:`ensure_buffalo_s_model`, which manages its own
    paths and SHA verification (both at the zip layer and the
    extracted-file layer). ModelSingleton's stock download path
    handles a single URL → single file flow; the ZIP-extract case
    doesn't fit, so we register with ``model_url=None`` and let the
    create_fn do the full ensure → load dance.
    """
    import onnxruntime as ort

    from bpp.scoring.onnx_providers import get_providers

    model_path = ensure_buffalo_s_model()
    return ort.InferenceSession(model_path, providers=get_providers())


#: Process-wide singleton for the buffalo_s ONNX inference session.
#: Replaces the hand-rolled threading lock + module-global cache
#: pattern. Uses the canonical ``ModelSingleton`` helper so the
#: lifecycle (lazy init, double-checked locking, ``reset()`` hook)
#: matches every other ML model in BPP — the project conventions
#: explicitly require this for new models.
_buffalo_s_model = ModelSingleton(
    name="InsightFace buffalo_s (w600k_mbf)",
    model_path=None,  # managed by ensure_buffalo_s_model
    model_url=None,  # ZIP-extract flow doesn't fit the stock download path
    create_fn=_buffalo_s_create,
    registry_id=REGISTRY_ID,
    import_check=lambda: __import__("onnxruntime"),
)


def _get_session():
    """Return the cached ONNX inference session for the recognition
    model.

    The policy gate fires here, BEFORE any model file is touched
    and BEFORE the singleton's first init. A denial raises cleanly
    without ever calling ``_buffalo_s_create``, so the singleton
    state stays uncorrupted and the next call after policy unblocks
    re-attempts cleanly.
    """
    from bpp.registry import enforce_load_policy_for

    enforce_load_policy_for(REGISTRY_ID)
    return _buffalo_s_model.get()


def _align_with_landmarks(image: np.ndarray, src_points: np.ndarray) -> np.ndarray | None:
    """Align a face crop using a 5-point similarity transform.

    ``src_points`` is a 5x2 float32 array of (x, y) pixel
    coordinates in the source image, in canonical ArcFace order:
    [subject left eye, subject right eye, nose, subject left
    mouth, subject right mouth]. Maps onto
    :data:`_ARCFACE_REF_POINTS` and warps the image accordingly.

    Returns the 112x112 BGR aligned crop, or ``None`` if the
    transform estimator failed (collinear points, NaN inputs).
    """
    import cv2

    matrix, _inliers = cv2.estimateAffinePartial2D(
        src_points.astype(np.float32),
        _ARCFACE_REF_POINTS,
        method=cv2.LMEDS,
    )
    if matrix is None:
        return None
    aligned = cv2.warpAffine(
        image,
        matrix,
        (_ARC_INPUT_SIZE, _ARC_INPUT_SIZE),
        borderValue=0.0,
    )
    return aligned


def _yunet_landmarks_to_arcface_order(face_row: np.ndarray) -> np.ndarray:
    """Extract landmarks from a YuNet detection row in ArcFace order.

    YuNet row layout: [x, y, w, h,
        right_eye_x(4), right_eye_y(5),    # subject's left eye
        left_eye_x(6),  left_eye_y(7),     # subject's right eye
        nose_x(8),      nose_y(9),
        mouth_right_x(10), mouth_right_y(11),  # subject's left mouth
        mouth_left_x(12),  mouth_left_y(13),   # subject's right mouth
        confidence(14)]

    OpenCV YuNet uses image-frame "right" / "left" labels (which is
    the OPPOSITE of subject-frame), but the resulting points happen
    to be in the same order ArcFace expects when interpreted in
    SUBJECT frame: image-right eye = subject left eye, so YuNet's
    "right_eye" maps to ArcFace position 0 directly. No reorder
    needed; the labels are confusing but the positions align."""
    return np.array(
        [
            [face_row[4], face_row[5]],  # subject left eye
            [face_row[6], face_row[7]],  # subject right eye
            [face_row[8], face_row[9]],  # nose
            [face_row[10], face_row[11]],  # subject left mouth
            [face_row[12], face_row[13]],  # subject right mouth
        ],
        dtype=np.float32,
    )


def _preprocess(
    image: np.ndarray,
    face_box: tuple[int, int, int, int],
    yunet_row: np.ndarray | None = None,
) -> np.ndarray | None:
    """Produce the model input tensor (1, 3, 112, 112) float32.

    When ``yunet_row`` is provided (15-element YuNet detection row),
    aligns the face using 5-point similarity transform. Otherwise
    falls back to bbox-crop-resize.

    Returns ``None`` if the bbox is degenerate or alignment fails
    so the caller can skip the face without raising.
    """
    import cv2

    if yunet_row is not None and len(yunet_row) >= _YUNET_ROW_LEN - 1:
        src = _yunet_landmarks_to_arcface_order(yunet_row)
        aligned = _align_with_landmarks(image, src)
        if aligned is None:
            return None
        crop_bgr = aligned
    else:
        ih, iw = image.shape[:2]
        x, y, w, h = face_box
        if w <= 0 or h <= 0:
            return None
        x = max(0, min(int(x), iw - 1))
        y = max(0, min(int(y), ih - 1))
        w = max(1, min(int(w), iw - x))
        h = max(1, min(int(h), ih - y))
        crop = image[y : y + h, x : x + w]
        if crop.size == 0:
            return None
        crop_bgr = cv2.resize(
            crop,
            (_ARC_INPUT_SIZE, _ARC_INPUT_SIZE),
            interpolation=cv2.INTER_LINEAR,
        )

    # OpenCV gives BGR by default; ArcFace was trained on RGB.
    crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    arr = crop_rgb.astype(np.float32)
    arr = (arr - 127.5) / 127.5
    # NCHW
    return arr.transpose(2, 0, 1)[None]


#: Distance-scale factor applied to the L2-normalized embedding so
#: the resulting Euclidean distances land in the same numeric range
#: as SFace's scaled output. This lets the existing
#: FACE_CLUSTER_THRESHOLD_FALLBACK (0.55) work for buffalo_s without
#: a per-method config knob. Calibrated heuristically: same value
#: SFace uses (see SFACE_DISTANCE_SCALE in face_embed.py). May want
#: separate tuning once a real photo library is available — ArcFace
#: intra-class similarity is typically slightly tighter than SFace's,
#: so 0.55 should still separate same-person from different-person
#: clusters reliably.
_BUFFALO_S_DISTANCE_SCALE = 0.65


def embed_face(
    image: np.ndarray,
    face_box: tuple[int, int, int, int],
    yunet_row: np.ndarray | None = None,
) -> np.ndarray | None:
    """Run the recognition model on a face and return the scaled
    512-d embedding for clustering.

    When ``yunet_row`` is provided (full YuNet detection: bbox +
    5 landmarks + confidence), uses 5-point similarity-transform
    alignment — same alignment InsightFace uses, producing
    embeddings maximally compatible with the upstream reference.
    Otherwise falls back to bbox-crop-resize.

    Returns ``None`` on any failure (model unavailable, degenerate
    bbox, inference error). The face_worker tolerates None and skips
    that face cleanly — propagating raises would break a 50k-photo
    batch on a single bad crop.

    The very first call may take a few seconds while the model
    downloads and the ONNX session initialises; subsequent calls run
    inference in milliseconds.

    The returned vector is L2-normalized then scaled by
    :data:`_BUFFALO_S_DISTANCE_SCALE` so Euclidean distances on the
    output land in the same range the existing clusterer expects
    (calibrated against SFace).
    """
    if not is_available():
        return None

    try:
        x = _preprocess(image, face_box, yunet_row=yunet_row)
        if x is None:
            return None
        session = _get_session()
        # The model accepts the input named "input.1"; surface the
        # name from get_inputs to stay forward-compatible.
        input_name = session.get_inputs()[0].name
        out = session.run(None, {input_name: x})[0]
        vec = out[0].astype(np.float32)
        n = float(np.linalg.norm(vec))
        if n == 0.0:
            return None
        return (vec / n) * _BUFFALO_S_DISTANCE_SCALE
    except Exception as exc:
        log.warning("buffalo_s embed failed for bbox=%s: %s", face_box, exc)
        return None


def register_with_face_embedder_registry() -> None:
    """Register the buffalo_s embedder so the dispatch layer can
    find it by name. Called from
    :mod:`bpp.scoring.face_embedder_registry` at import time when
    the feature is wired."""
    from bpp.scoring.face_embedder_registry import (
        FaceEmbedder,
        register_embedder,
    )

    register_embedder(
        FaceEmbedder(
            name="buffalo_s",
            embed=embed_face,
            embedding_dim=EMBEDDING_DIM,
            license_id="research_non_commercial",
            description=(
                "InsightFace buffalo_s — MobileFaceNet ArcFace "
                "(512-d). Research-only / non-commercial weights."
            ),
        )
    )
