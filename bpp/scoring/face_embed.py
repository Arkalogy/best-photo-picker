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
    from bpp.scoring.face_recognition_loader import load_face_recognition

    return load_face_recognition() is not None


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


def producing_model_id_for(method: str) -> str:
    """Model-registry id stored with an embedding made by ``method``.

    Read from the embedder's own ``registry_id`` — the one place the
    link is written — so the derived-data purge (which deletes rows
    ``WHERE producing_model_id = <registry id>``) always matches.
    Unknown methods (e.g. BYOM ``byom_<hash>``) pass through unchanged
    so the purge can still match them by the id they were tagged with.
    """
    from bpp.scoring.face_embedder_registry import get_embedder

    embedder = get_embedder(method)
    return embedder.registry_id if embedder is not None else method


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
        if preferred and preferred not in _BUILTIN_METHODS:
            from bpp.scoring.face_embedder_registry import get_embedder

            if get_embedder(preferred) is not None:
                return preferred  # a plugin embedder the user picked
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
    # detection. The "what is the default embedder" decision lives in
    # the registry so a future change cannot quietly land users on a restricted model
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
        method: ``"sface"`` | ``"dlib"`` | ``"buffalo_s"`` | a registered
            plugin embedder's name | ``None``. See above.
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

    if method and method not in _BUILTIN_METHODS:
        from bpp.scoring.face_embedder_registry import get_embedder

        embedder = get_embedder(method)
        if embedder is None and not method.startswith("byom_"):
            # Never fall back to SFace: its vectors would be stored under
            # the plugin's name (or rejected for the wrong size).
            if method not in _unknown_method_warned:
                _unknown_method_warned.add(method)
                log.warning(
                    "Face embedder %r isn't loaded in this process; no faces embedded", method
                )
            return []
        if embedder is not None:
            # A plugin embedder picked in Settings: never mixed with the
            # built-ins (embeddings from different models don't compare).
            return (
                _extract_plugin(
                    image, embedder, min_confidence, embedding_confidence, min_embedding_quality
                )
                or []
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


_unknown_method_warned: set[str] = set()


def is_known_method(method: str) -> bool:
    """A built-in method, a BYOM id, or a loaded plugin embedder."""
    from bpp.scoring.face_embedder_registry import get_embedder

    return (
        method in _BUILTIN_METHODS or method.startswith("byom_") or get_embedder(method) is not None
    )


_BUILTIN_METHODS = frozenset(
    {EMBEDDING_METHOD_SFACE, EMBEDDING_METHOD_DLIB, EMBEDDING_METHOD_BUFFALO_S}
)


def _extract_plugin(image, embedder, min_confidence, embedding_confidence, min_quality):
    """Embed every detected face with a plugin's ``embed(image, box)``,
    once its registry entry's license allows it.

    Each vector must have the declared ``embedding_dim`` and finite,
    non-zero values; it is then normalised to unit length and scaled
    like SFace's, so the face-grouping threshold applies. Anything else
    is dropped (one warning per plugin)."""
    from bpp.scoring.face_embed_buffalo import _extract_with_embedder
    from bpp.scoring.model_load_gate import license_allows

    feature = f"Face embedder {embedder.name}"
    if not license_allows(embedder.registry_id, feature=feature):
        return []

    def embed(img, box, _row):
        raw = embedder.embed(img, box)
        if raw is None:  # the contract's "can't embed this face"
            return None
        vec = np.asarray(raw, dtype=np.float64).ravel()
        norm = float(np.linalg.norm(vec)) if np.isfinite(vec).all() else 0.0
        if vec.shape != (embedder.embedding_dim,) or norm <= 0.0:
            _warn_bad_plugin_vector(embedder, vec)
            return None
        return vec / norm * SFACE_DISTANCE_SCALE

    return _extract_with_embedder(
        image, min_confidence, embedding_confidence, min_quality, embed, embedder.name
    )


_bad_vector_warned: set[str] = set()


def _warn_bad_plugin_vector(embedder, vec) -> None:
    if embedder.name in _bad_vector_warned:
        return
    _bad_vector_warned.add(embedder.name)
    log.warning(
        "Face embedder %r returned an unusable vector (shape %s, declared %d floats, "
        "or non-finite / all-zero); those faces are skipped.",
        embedder.name,
        vec.shape,
        embedder.embedding_dim,
    )


# ── public embedder registry ──
#
# The built-in embedders are registered so their names and registry ids
# live in one place (producing_model_id_for reads them). They run through
# their own paths in `extract_face_embeddings` — SFace and dlib are tied
# to OpenCV's FaceRecognizerSF / face_recognition APIs — so their `embed`
# is a placeholder. A plugin embedder's `embed(image, box)` is called per
# face when `face_embedding_method` names it.
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
        registry_id="sface_yunet",
        description="OpenCV SFace — primary, fast",
    ),
    builtin=True,
)
register_embedder(
    FaceEmbedder(
        name="dlib",
        embed=_embed_not_directly_callable,
        embedding_dim=128,
        registry_id="dlib_face_recognition_resnet_v1",
        description="face_recognition / dlib fallback (requires bppicker[faces])",
    ),
    builtin=True,
)
register_embedder(
    FaceEmbedder(
        name="buffalo_s",
        embed=_embed_not_directly_callable,
        embedding_dim=512,
        registry_id="insightface_buffalo_s",
        description="InsightFace buffalo_s — MobileFaceNet ArcFace (512-d)",
    ),
    builtin=True,
)
