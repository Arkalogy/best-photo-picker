"""Face embedding — public entry point + embedder dispatch.

This module is the front door: :func:`extract_face_embeddings` (the
``method`` dispatch), :func:`embedding_method` (which backend the
registry/settings select), :func:`is_available`, and back-compat
re-exports of everything below. Photos become 128-d (SFace/dlib) or
512-d (buffalo_s) embeddings stored in the same DB format; SFace is
L2-normalized for consistent clustering.

────────────────────────────────────────────────────────────────────
MODULE MAP — the ``face_embed_*`` family (read this before adding one)
────────────────────────────────────────────────────────────────────
The family splits along two axes: BACKEND (sface / dlib / buffalo_s)
and CONCERN (extractor = image→embeddings vs runtime = model load +
recognizer singleton). Each file is kept under the 500-LOC cap, which
is why a backend is sometimes two files.

  face_embed.py                 ← YOU ARE HERE: dispatch + public API
  face_embed_sface.py           SFace EXTRACTOR (_extract_sface: YuNet
                                detect → SFace embed)
  face_embed_sface_runtime.py   SFace RUNTIME: model download, license
                                gate, per-thread recognizer singleton,
                                extract_embedding_for_region. (Split
                                from the extractor purely for the LOC
                                cap — they can't merge, the sum is >500.)
  face_embed_extractors.py      dlib FALLBACK extractor + SCRFD
                                supplementary pass; also the back-compat
                                re-export hub for the sface/buffalo
                                extractors.
  face_embed_buffalo.py         buffalo_s (ArcFace) EXTRACTOR.
  face_embed_buffalo_s.py       buffalo_s RUNTIME: ONNX load + session.
  face_embed_landmarks.py       shared landmark validation + quality.
  face_embedder_registry.py     PLUGIN registry (register_embedder) —
                                the extension point for new backends.

ARCHITECTURE INVARIANT — DO NOT "MODERNIZE" TO ModelSingleton.
SFace/YuNet/dlib use per-thread storage (the recognizer singleton lives
in ``face_embed_sface_runtime``) because ``cv2.FaceRecognizerSF.feature()``
mutates internal DNN buffers and is not thread-safe; ThreadPoolExecutor
workers would corrupt each other's embeddings. Per-thread instances + a
one-time download lock + a negative-cache flag is the working pattern
(per the project conventions; regression gate at tests/test_face_thread_safety.py).
"""

from __future__ import annotations

import sqlite3

import numpy as np

from bpp.utils.logging import get_logger

log = get_logger(__name__)


def is_available() -> bool:
    """Check if face embedding extraction is available.

    SFace (via OpenCV) is always available.  Falls back to face_recognition
    if SFace model is missing.
    """
    if _sface_available():
        return True
    try:
        import face_recognition  # noqa: F401

        return True
    except ImportError:
        return False


def can_install() -> bool:
    """Return True if pip is available (faces extra can be installed at runtime)."""
    import shutil
    import sys

    return shutil.which(sys.executable) is not None


MAX_FACES_PER_PHOTO = 20

# Default minimum detector confidence for embedding extraction.
# Face *scoring* uses the user's threshold (default 0.3) where false positives
# only mildly inflate the face count.  Embedding extraction needs higher
# confidence because a non-face embedding corrupts the entire cluster.
# Calibrated on 150 photos: 0.65 gives 97.3% precision while losing <5% of
# true faces (mostly extreme angles that produce poor embeddings anyway).
DEFAULT_EMBEDDING_CONFIDENCE = 0.65

# Aspect ratio limits for face bounding boxes (width/height).
# Real faces range from ~0.6 (narrow/profile) to ~1.5 (wide baby face).
MIN_FACE_ASPECT = 0.45
MAX_FACE_ASPECT = 2.0

# Minimum landmark spread as fraction of bbox diagonal.
# Real faces have landmarks spread across the face; non-face detections
# (blankets, torsos) produce compressed/degenerate landmark layouts.
# Baby faces have features closer together — 0.12 accommodates infants.
MIN_LANDMARK_SPREAD = 0.12

# Embedding method identifier — stored in DB settings to detect when
# re-extraction is needed after switching methods.
EMBEDDING_METHOD_SFACE = "sface"
EMBEDDING_METHOD_DLIB = "dlib"
EMBEDDING_METHOD_BUFFALO_S = "buffalo_s"

# Map each embedding METHOD string to the canonical model-registry entry id
# stored in face_embeddings.producing_model_id. The derived-data purge
# (registry/derived_data_purge.py) deletes rows WHERE producing_model_id =
# <registry id> at model-removal time, so the WRITE side must store the
# registry id, not the bare method. A method missing from this map would
# tag rows with the short string and the purge would silently miss them —
# exactly the buffalo_s bug ("buffalo_s" stored vs "insightface_buffalo_s"
# purged). Single source of truth; test_face_embedding_method_dispatch
# asserts every entry resolves to a real registry id. BYOM extractions pass
# their own byom_<hash> id through `method` and fall through unchanged.
EMBEDDING_METHOD_TO_REGISTRY_ID = {
    EMBEDDING_METHOD_SFACE: "sface_yunet",
    EMBEDDING_METHOD_DLIB: "dlib_face_recognition_resnet_v1",
    EMBEDDING_METHOD_BUFFALO_S: "insightface_buffalo_s",
}


def producing_model_id_for(method: str) -> str:
    """Canonical model-registry id for a stored embedding's ``method``.

    Unknown methods (e.g. BYOM ``byom_<hash>``) pass through unchanged so
    the purge can still match them by the id they were tagged with.
    """
    return EMBEDDING_METHOD_TO_REGISTRY_ID.get(method, method)


# Minimum face size in pixels (width or height) for embedding extraction.
# Below this, faces are too small for reliable recognition.
MIN_FACE_PX = 30

# Minimum quality score (0.0-1.0) for an embedding to be kept.
# Quality combines frontality, face size, and detector confidence.
# Faces below this produce noisy embeddings that degrade clustering.
MIN_EMBEDDING_QUALITY = 0.25

# SFace recognizer runtime — model load, license gate, the per-thread
# recognizer singleton, and region embedding — lives in
# bpp.scoring.face_embed_sface_runtime (split out for the 500-LOC cap).
# Re-exported here so existing import paths keep working
# (face_embed._get_sface_recognizer, ensure_sface_model,
# extract_embedding_for_region, _SFACE_MODEL_PATH, SFACE_DISTANCE_SCALE, ...).
from bpp.scoring.face_embed_sface_runtime import (  # noqa: E402, F401
    _SFACE_MODEL_PATH,
    _SFACE_MODEL_SHA256,
    _SFACE_MODEL_URL,
    SFACE_DISTANCE_SCALE,
    _enforce_sface_policy,
    _ensure_sface_model,
    _get_sface_recognizer,
    _reset_sface_cache,
    _sface_available,
    ensure_sface_model,
    extract_embedding_for_region,
)


def embedding_method(conn: sqlite3.Connection | None = None) -> str:
    """Return the active embedding method name.

    When ``conn`` is provided, the ``face_embedding_method`` setting in
    the DB is consulted first: a stored ``"dlib"`` value forces the
    dlib path even when SFace is loadable. This is the toggle a user
    flips when they want a different embedder. Without ``conn``, the
    function falls back to model-availability detection — SFace if
    loadable, else dlib — matching the pre-fix behavior so call sites
    without DB access keep working.

    When the stored preference points at an embedder whose model is
    not loadable (e.g., ``"sface"`` but the ONNX file is missing) the
    fall-back chain runs and a warning is logged. This means a user
    who configures ``"dlib"`` always gets dlib (face_recognition
    bundles the model file on install), and a user who configures
    ``"sface"`` gets SFace when available and dlib otherwise.

    Note: this used to ignore the setting entirely and *write* it to
    the DB based on availability — making the setting decorative.
    Phase 1 of extraction calls this with ``ctx.conn`` so the user's
    choice is honored.
    """
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='face_embedding_method'"
            ).fetchone()
            preferred = row[0] if row else None
        except Exception:
            log.warning(
                "Failed to read face_embedding_method setting; "
                "falling back to model-availability check",
                exc_info=True,
            )
            preferred = None
        if preferred == EMBEDDING_METHOD_DLIB:
            return EMBEDDING_METHOD_DLIB
        if preferred == EMBEDDING_METHOD_SFACE:
            if _get_sface_recognizer() is not None:
                return EMBEDDING_METHOD_SFACE
            log.warning(
                "face_embedding_method=sface but SFace model unavailable; falling back to dlib",
            )
            return EMBEDDING_METHOD_DLIB
        if preferred == EMBEDDING_METHOD_BUFFALO_S:
            # Resolve only if the runtime gate passes and
            # onnxruntime is available. The buffalo_s loader does
            # its own ensure_buffalo_s_model() download on first
            # extract — but here we just check the import is
            # possible. If the user hasn't accepted, downstream
            # extract_face_embeddings will hit the gate and the
            # face worker will report the error cleanly.
            try:
                from bpp.scoring import face_embed_buffalo_s

                if face_embed_buffalo_s.is_available():
                    return EMBEDDING_METHOD_BUFFALO_S
            except ImportError:
                pass
            log.warning(
                "face_embedding_method=buffalo_s but onnxruntime "
                "is not available; falling back to SFace.",
            )
            if _get_sface_recognizer() is not None:
                return EMBEDDING_METHOD_SFACE
            return EMBEDDING_METHOD_DLIB
    # No setting or unrecognized value: consult the model registry's
    # default-for-kind entry before falling back to availability
    # detection. Batch 2 of the legal-posture rollout (item 1) lifts
    # the "what is the default embedder" decision into the registry so
    # a future change cannot quietly land users on a restricted model
    # by re-ordering availability checks here. When the registry has
    # no face-embedder default registered (test contexts that reset
    # the registry, fresh imports, etc.) the historical
    # availability-only behavior remains the last-resort fallback.
    try:
        from bpp.registry import get_default_for_kind

        default_entry = get_default_for_kind("face_embedder")
    except Exception:
        default_entry = None
    if default_entry is not None:
        if default_entry.id == "sface_yunet":
            if _get_sface_recognizer() is not None:
                return EMBEDDING_METHOD_SFACE
            log.warning(
                "Registry default is sface_yunet but SFace model unavailable; falling back to dlib",
            )
            return EMBEDDING_METHOD_DLIB
        if default_entry.id == "dlib_face_recognition_resnet_v1":
            return EMBEDDING_METHOD_DLIB
        # Unknown registry id — log + fall through. A plugin that
        # registered a non-built-in embedder as the default needs
        # explicit dispatch wiring elsewhere; this function still
        # returns one of the two strings consumers know how to handle.
        log.warning(
            "Registry face_embedder default %r has no dispatch mapping in "
            "embedding_method(); falling back to availability check",
            default_entry.id,
        )
    if _get_sface_recognizer() is not None:
        return EMBEDDING_METHOD_SFACE
    return EMBEDDING_METHOD_DLIB


# Landmark validation + quality scoring live in
# bpp.scoring.face_embed_landmarks. Re-exported here for back-compat.
# The three extractor implementations live in
# bpp.scoring.face_embed_extractors. Re-exported here for back-compat.
from bpp.scoring.face_embed_extractors import (  # noqa: E402, F401
    _bbox_iou,
    _dlib_face_quality,
    _extract_buffalo_s,
    _extract_dlib,
    _extract_sface,
    _supplement_with_scrfd,
)
from bpp.scoring.face_embed_landmarks import (  # noqa: E402, F401
    _blur_score,
    _face_quality_from_landmarks,
    _validate_face_landmarks,
    _validate_yunet_landmarks,
)

# ── Public API ──


def extract_face_embeddings(
    image: np.ndarray,
    *,
    min_confidence: float = 0.2,
    embedding_confidence: float = DEFAULT_EMBEDDING_CONFIDENCE,
    min_embedding_quality: float = MIN_EMBEDDING_QUALITY,
    method: str | None = None,
) -> list[dict]:
    """Extract face locations and 128-d embeddings from a BGR image.

    Returns list of dicts: [{bbox: (x, y, w, h), embedding: np.array(128,)}, ...]
    Photos with more than MAX_FACES_PER_PHOTO detections are treated as
    false-positive spam (texture/crowd noise) and return empty.

    ``method`` selects the embedder. ``"sface"`` uses YuNet + SFace
    (BSD licensed) as the primary pipeline with a dlib fallback for
    photos where YuNet finds nothing. ``"dlib"`` skips SFace entirely
    and goes straight to the face_recognition / dlib path — used when
    the user picks dlib in settings. ``None`` (the default) preserves
    the pre-toggle behavior: SFace first, dlib fallback.

    Args:
        min_confidence: Detection threshold (lower finds more candidates).
        embedding_confidence: Minimum confidence for a detection to get an
            embedding extracted.  Higher = fewer false embeddings in clusters
            but may miss some faces.  Default 0.65 was calibrated on 150
            real-world photos (97.3% precision, <5% true face loss).
        method: ``"sface"`` | ``"dlib"`` | ``None``. See above.
    """
    if method == EMBEDDING_METHOD_DLIB:
        # User explicitly chose dlib — skip SFace entirely.
        return _extract_dlib(
            image,
            min_confidence,
            embedding_confidence,
            min_quality=min_embedding_quality,
        )

    if method == EMBEDDING_METHOD_BUFFALO_S:
        # User picked the restricted ArcFace path. The detector is
        # whatever SCRFD/YuNet returns; we hand off those bboxes
        # to the buffalo_s embedder for the recognition step. The
        # runtime gate fires inside _get_session on first use; an
        # unaccepted model raises ModelLoadBlockedError which the
        # face worker logs and treats as "this photo got no
        # embeddings."
        return _extract_buffalo_s(
            image,
            min_confidence,
            embedding_confidence,
            min_quality=min_embedding_quality,
        )

    # method == "sface" or None: SFace first, dlib fallback.
    result = _extract_sface(
        image,
        min_confidence,
        embedding_confidence,
        min_quality=min_embedding_quality,
    )

    if result is not None:
        return result

    # Fall back to dlib (uses detect_faces_with_confidence which includes SCRFD)
    return _extract_dlib(
        image,
        min_confidence,
        embedding_confidence,
        min_quality=min_embedding_quality,
    )


# ── public embedder registry ──
#
# Document the two built-in embedding backends in the public
# `face_embedder_registry` so plugins can introspect / extend.
# The orchestration in `extract_face_embeddings` keeps its
# SFace-first / dlib-fallback ordering — these registrations
# don't replace it, they make the contract visible to plugin
# authors who want to add a third backend (e.g. ArcFace).
#
# `embed=` here is None because both built-in paths are deeply
# coupled to OpenCV's `FaceRecognizerSF` API (SFace) or
# `face_recognition.face_encodings` (dlib) and a clean per-face
# `(image, bbox) -> ndarray` extraction would need a substantial
# refactor of `extract_face_embeddings`. The registry is the
# extension point; the orchestrator stays as-is until a plugin
# author actually wants to swap in a new backend, at which point
# we'll wire the orchestrator to consult the registry.
from bpp.scoring.face_embedder_registry import FaceEmbedder, register_embedder  # noqa: E402


def _embed_not_directly_callable(*_args, **_kwargs):
    """Placeholder — see `extract_face_embeddings` for the actual
    orchestration. The registry entries are descriptive metadata
    until the orchestrator is refactored to consult them."""
    raise NotImplementedError(
        "Built-in embedders run through extract_face_embeddings(); they "
        "are not directly callable. Register your own embedder via "
        "face_embedder_registry.register_embedder() if you need the "
        "(image, bbox) -> ndarray contract."
    )


register_embedder(
    FaceEmbedder(
        name="sface",
        embed=_embed_not_directly_callable,
        embedding_dim=128,
        license_id="Apache-2.0",
        description="OpenCV SFace — primary, BSD-licensed, fast",
    )
)
register_embedder(
    FaceEmbedder(
        name="dlib",
        embed=_embed_not_directly_callable,
        embedding_dim=128,
        license_id="BSD-3-Clause",
        description="face_recognition / dlib fallback (requires bppicker[faces])",
    )
)
register_embedder(
    FaceEmbedder(
        name="buffalo_s",
        embed=_embed_not_directly_callable,
        embedding_dim=512,
        license_id="research_non_commercial",
        description=(
            "InsightFace buffalo_s — MobileFaceNet ArcFace (512-d). "
            "Research-only / non-commercial weights. Requires "
            "click-through acceptance."
        ),
    )
)
