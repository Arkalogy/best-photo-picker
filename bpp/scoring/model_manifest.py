"""Model manifest — single registry of every model the app may download.

Each entry describes:

  - the human-readable name shown in the consent dialog
  - the local cache path (resolved through bpp.utils.paths so the user's
    BPP_CACHE_DIR / BPP_MODELS_DIR overrides apply)
  - the upstream URL
  - the expected SHA-256
  - the approximate download size in MB (used in the consent prompt)
  - the host hostname (so the user can see at a glance who they're
    fetching from — github vs huggingface vs google)
  - an optional bundled-fallback path; entries with a present bundled
    file never trigger a download

The single source of truth for URLs / SHAs lives next to each model
(face.py, clip_embed.py, …); this module imports those constants so a
hash bump in one place propagates here.

The manifest powers the per-model consent prompt the user sees on first
analyze: instead of a vague "~50 MB will download", they see exactly
which models, which sizes, which hosts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class ModelEntry:
    """One downloadable model."""

    name: str
    path: Path
    url: str
    sha256: str
    size_mb: float
    host: str
    bundled_path: str | None = None
    #: Optional cross-reference into ``bpp.registry.model_registry``.
    #: Set for entries that have a legal-registry counterpart so the
    #: ``/api/v1/models/pending`` endpoint can check restriction +
    #: acceptance status and filter out models the user can't actually
    #: download (the runtime gate would refuse, surfacing a confusing
    #: "blocked_needs_ack" toast). ``None`` for ancillary models with
    #: no licensing concern.
    legal_entry_id: str | None = None

    def is_present(self) -> bool:
        """True when the model is already on disk (cached or bundled)
        and so won't trigger a download."""
        if self.path.exists():
            return True
        return self.bundled_path is not None and Path(self.bundled_path).exists()


def _host_of(url: str) -> str:
    return urlparse(url).hostname or "unknown"


def all_models() -> list[ModelEntry]:
    """Every downloadable model the app may pull on first use.

    Computed each call so env-var overrides (BPP_CACHE_DIR /
    BPP_MODELS_DIR) take effect at request time, not import time.

    Importing the model modules below is cheap — the heavy ML library
    imports are deferred to ``ModelSingleton.create_fn`` which we don't
    trigger here.
    """
    # URL / SHA / PATH constants live next to each model. Importing
    # those modules just to read the strings is fine — model libraries
    # (mediapipe, onnxruntime, ai_edge_litert, dlib) are loaded lazily
    # via the ModelSingleton import_check + create_fn pattern.
    from bpp.scoring import face as _face
    from bpp.scoring import face_blazeface_fr as _ffr
    from bpp.scoring import face_embed as _femb
    from bpp.scoring import face_expression as _fex
    from bpp.scoring import face_hand_filter as _fhf
    from bpp.scoring import face_scrfd as _fsc
    from bpp.scoring.clip_embed import (
        _MODEL_DIR as _CLIP_DIR,
    )
    from bpp.scoring.clip_embed import (
        _MODEL_FILENAME as _CLIP_VISUAL_FN,
    )
    from bpp.scoring.clip_embed import (
        _MODEL_SHA256 as _CLIP_VISUAL_SHA,
    )
    from bpp.scoring.clip_embed import (
        _MODEL_URL as _CLIP_VISUAL_URL,
    )
    from bpp.scoring.clip_embed import (
        _TEXT_MODEL_FILENAME as _CLIP_TEXT_FN,
    )
    from bpp.scoring.clip_embed import (
        _TEXT_MODEL_SHA256 as _CLIP_TEXT_SHA,
    )
    from bpp.scoring.clip_embed import (
        _TEXT_MODEL_URL as _CLIP_TEXT_URL,
    )
    from bpp.scoring.clip_tokenizer import (
        _VOCAB_DIR,
        _VOCAB_FILENAME,
        _VOCAB_SHA256,
        _VOCAB_URL,
    )
    from bpp.scoring.pets import _MODEL_FILENAME as _YOLO_FN
    from bpp.scoring.pets import _MODEL_SHA256 as _YOLO_SHA
    from bpp.scoring.pets import _MODEL_URL as _YOLO_URL
    from bpp.scoring.pose import _POSE_MODEL_PATH, _POSE_MODEL_SHA256, _POSE_MODEL_URL
    from bpp.scoring.segmentation import (
        _SEGMENTER_PATH,
        _SEGMENTER_SHA256,
        _SEGMENTER_URL,
    )
    from bpp.utils.paths import models_dir

    md = models_dir()

    return [
        # Face detection
        ModelEntry(
            name="BlazeFace short-range face detection",
            path=Path(_face._MODEL_PATH),
            url=_face._MODEL_URL,
            sha256=_face._MODEL_SHA256,
            size_mb=0.2,
            host=_host_of(_face._MODEL_URL),
            bundled_path=str(Path(_face._BUNDLED_DIR) / "blaze_face_short_range.tflite"),
        ),
        ModelEntry(
            name="BlazeFace full-range face detection",
            path=Path(_ffr._FR_MODEL_PATH),
            url=_ffr._FR_MODEL_URL,
            sha256=_ffr._FR_MODEL_SHA256,
            size_mb=0.6,
            host=_host_of(_ffr._FR_MODEL_URL),
        ),
        ModelEntry(
            name="YuNet face detection",
            path=Path(_face._YUNET_MODEL_PATH),
            url=_face._YUNET_MODEL_URL,
            sha256=_face._YUNET_MODEL_SHA256,
            size_mb=0.3,
            host=_host_of(_face._YUNET_MODEL_URL),
            # OpenCV Zoo, Apache-2.0 attribution — restricted. Without
            # this link the pre-flight dialog would offer it as a free
            # download (the loader's own gate still blocks it, but the
            # dialog would lie). See registry/builtins.py YUNET_ENTRY.
            legal_entry_id="opencv_yunet",
        ),
        ModelEntry(
            name="SCRFD face detection",
            path=Path(_fsc._SCRFD_MODEL_PATH),
            url=_fsc._SCRFD_MODEL_URL,
            sha256=_fsc._SCRFD_MODEL_SHA256,
            size_mb=16,
            host=_host_of(_fsc._SCRFD_MODEL_URL),
            # MIT — permissive; linked for parity so the dialog reads
            # its legal status from the registry rather than guessing.
            legal_entry_id="insightface_scrfd_25g",
        ),
        # Face features
        ModelEntry(
            name="SFace face recognition",
            path=Path(_femb._SFACE_MODEL_PATH),
            url=_femb._SFACE_MODEL_URL,
            sha256=_femb._SFACE_MODEL_SHA256,
            size_mb=38,
            host=_host_of(_femb._SFACE_MODEL_URL),
            # SFace ONNX, restricted (attribution). MUST be linked or the
            # pre-flight dialog offers it as a free download — the exact
            # bug this fixes. See registry/builtins.py SFACE_ENTRY.
            legal_entry_id="sface_yunet",
        ),
        ModelEntry(
            name="Face landmarker (expressions)",
            path=Path(_fex._LANDMARKER_PATH),
            url=_fex._LANDMARKER_URL,
            sha256=_fex._LANDMARKER_SHA256,
            size_mb=4,
            host=_host_of(_fex._LANDMARKER_URL),
        ),
        ModelEntry(
            name="Hand landmarker (occlusion filter)",
            path=Path(_fhf._HAND_MODEL_PATH),
            url=_fhf._HAND_MODEL_URL,
            sha256=_fhf._HAND_MODEL_SHA256,
            size_mb=5,
            host=_host_of(_fhf._HAND_MODEL_URL),
        ),
        # Pose / composition
        ModelEntry(
            name="Pose landmarker",
            path=Path(_POSE_MODEL_PATH),
            url=_POSE_MODEL_URL,
            sha256=_POSE_MODEL_SHA256,
            size_mb=5,
            host=_host_of(_POSE_MODEL_URL),
        ),
        ModelEntry(
            name="Selfie segmenter",
            path=Path(_SEGMENTER_PATH),
            url=_SEGMENTER_URL,
            sha256=_SEGMENTER_SHA256,
            size_mb=0.3,
            host=_host_of(_SEGMENTER_URL),
        ),
        # Pets
        ModelEntry(
            name="YOLO11n pet detector",
            path=md / _YOLO_FN,
            url=_YOLO_URL,
            sha256=_YOLO_SHA,
            size_mb=11,
            host=_host_of(_YOLO_URL),
            # AGPL-3.0 — restricted; needs click-through before download.
            legal_entry_id="ultralytics_yolov11n_pets",
        ),
        # CLIP (semantic search + smart dedup) — large, optional
        ModelEntry(
            name="CLIP visual encoder",
            path=_CLIP_DIR / _CLIP_VISUAL_FN,
            url=_CLIP_VISUAL_URL,
            sha256=_CLIP_VISUAL_SHA,
            size_mb=336,
            host=_host_of(_CLIP_VISUAL_URL),
        ),
        ModelEntry(
            name="CLIP text encoder",
            path=_CLIP_DIR / _CLIP_TEXT_FN,
            url=_CLIP_TEXT_URL,
            sha256=_CLIP_TEXT_SHA,
            size_mb=242,
            host=_host_of(_CLIP_TEXT_URL),
        ),
        ModelEntry(
            name="CLIP BPE vocabulary",
            path=_VOCAB_DIR / _VOCAB_FILENAME,
            url=_VOCAB_URL,
            sha256=_VOCAB_SHA256,
            size_mb=1.3,
            host=_host_of(_VOCAB_URL),
        ),
    ]


def pending_downloads() -> list[ModelEntry]:
    """Subset of :func:`all_models` that aren't on disk yet — i.e. the
    set of downloads the next analyze run would actually trigger."""
    return [m for m in all_models() if not m.is_present()]
