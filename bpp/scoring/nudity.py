"""ML-based nudity detection with the NudeNet 320n model.

BPP ships no NudeNet code and no NudeNet weights. The model file
(``320n.onnx``, AGPL-3.0) is downloaded on first use — only after the
user accepts it in the model registry's click-through — by
``bpp.utils.download.download_file`` from a commit-pinned URL, with
its SHA-256 verified before ONNX Runtime ever sees the bytes. It is
the same registry-coordinated chokepoint every other restricted model
uses (compare the YOLO pet detector in :mod:`bpp.scoring.pets`).

Inference is BPP's own code: the model is a standard YOLOv8-format
detector, so it runs through ONNX Runtime with the same letterbox
preprocessing as the pet detector, and the class names are read from
the model file's own metadata.

Scoring logic
-------------
The ``score_nudity`` function returns a float in [0, 1]:

* **Primary labels** (``FEMALE_GENITALIA_EXPOSED``, ``MALE_GENITALIA_EXPOSED``,
  ``ANUS_EXPOSED``) contribute their detection confidence at **full weight**.
* **Secondary labels** (``BUTTOCKS_EXPOSED``, ``FEMALE_BREAST_EXPOSED``)
  contribute at **30 %** weight — enough to flag obvious cases without
  penalising beach or bath photos heavily.
* The final score is ``min(1.0, max_primary + 0.3 * max_secondary)``.
* If no relevant detections are found the score is **0.0** (no penalty).

Thread safety
-------------
The ONNX session is initialised lazily as a module-level singleton.
ONNX Runtime inference is thread-safe, so sharing it across the
parallel analysis workers is fine.
"""

from __future__ import annotations

from pathlib import Path

from bpp.scoring.install_hold import requires
from bpp.scoring.model_base import ModelSingleton
from bpp.utils.logging import get_logger

log = get_logger(__name__)

# ── Model: NudeNet 320n (downloaded via registry chokepoint) ──────
#
# What:    YOLOv8-format detector for exposed-body-part classes. Powers
#          the optional "sensitive photo" flag / scoring penalty.
# Where:   notAI-tech/NudeNet on GitHub, fetched through our canonical
#          download_file gate. Commit-pinned URL so a future repo
#          history rewrite (force-push to v3 branch) can't silently
#          swap the file.
# Why this one: 320n is NudeNet's "nano" model (320x320 input,
#          smallest weight bundle); larger variants exist for higher
#          precision but the per-photo budget here is tight.
# License: see the bpp.registry entry ``nudenet_320n``.
# To bump: bump the URL's commit SHA + the matching weight hash in
#          ``bpp/registry/builtins.py`` (NUDENET_ENTRY). Re-validate
#          detection quality before pinning a new commit.
REGISTRY_ID = "nudenet_320n"

#: Upstream URL pinned to the notAI-tech/NudeNet commit that
#: published 320n.onnx at the SHA below. Commit-pinned (not branch-
#: or tag-pinned) so the URL is byte-stable even if the repo's
#: ``v3`` branch is force-pushed.
NUDENET_MODEL_URL = (
    "https://raw.githubusercontent.com/notAI-tech/NudeNet/"
    "21ccea100712f0844a4f6bb66c9f3972b2c32f85/nudenet/320n.onnx"
)

#: SHA-256 of 320n.onnx fetched from NUDENET_MODEL_URL. download_file
#: verifies this before the bytes are written to disk; ONNX never
#: sees unverified bytes.
NUDENET_MODEL_SHA256 = "c15d8273adad2d0a92f014cc69ab2d6c311a06777a55545f2c4eb46f51911f0f"

_MODEL_FILENAME = "320n.onnx"


def _model_cache_dir() -> Path:
    """Where to cache the NudeNet ONNX file. Honours BPP_MODELS_DIR
    via the shared :func:`bpp.utils.paths.models_dir`."""
    from bpp.utils.paths import models_dir

    return Path(models_dir()) / "nudenet"


def _local_model_path() -> Path:
    return _model_cache_dir() / _MODEL_FILENAME


def model_file() -> Path:
    """Where the NudeNet ONNX lives — the loader's path, the one source
    for the picker, Settings → Models and uninstall."""
    assert _nudenet.model_path is not None
    return _nudenet.model_path


def is_on_disk() -> bool:
    """Return True if the locally-cached NudeNet ONNX file exists.

    Cheap existence check only — does NOT verify the SHA. The picker
    uses this to decide whether to surface "Download (~11.6 MB)" or
    "Use this model" as the next step. A tampered cache is caught and
    re-downloaded by the shared loader, which the analysis preflight
    runs before scoring; the picker never deletes files.
    """
    return model_file().exists()


def ensure_nudenet_model() -> str:
    """Download + verify the NudeNet 320n ONNX. Returns the local path.

    Goes through the shared :class:`ModelSingleton` loader (retries,
    per-writer temp file, SHA verification and re-fetch of a corrupt
    cached file, the registry's license gate before any network call).

    Raises :class:`bpp.scoring.model_base.ModelIntegrityError` when a
    fresh download fails verification, and ``RuntimeError`` carrying the
    reason when the model can't be fetched.
    """
    return str(_nudenet.ensure_model_or_raise())


def remove_local_weights() -> int:
    """Delete the cached NudeNet ONNX and partial downloads. Returns the
    bytes freed.

    Used by the picker's Uninstall action for the NudeNet catalog
    entry. Waits for a download in progress and resets the in-process
    ModelSingleton so the next load re-runs ensure → download → verify.
    Symmetric counterpart to :func:`ensure_nudenet_model`.
    """
    return _nudenet.remove_files()


#: Input edge the 320n model was exported at (model metadata ``imgsz``).
_INPUT_SIZE = 320
#: Minimum class confidence for a detection to count.
_MIN_CONFIDENCE = 0.2


def _to_blob(img):
    """Square the image by padding black on the bottom/right, resize to
    the model input, scale to [0, 1], NCHW float32.

    Pixels are fed in BGR order and padded on the far edges because
    that is how the NudeNet 320n weights are conventionally run (the
    reference runner's colour conversion ends up passing BGR), and the
    0.7 ``sensitive_nudity_threshold`` was tuned against those scores.
    Changing either shifts every score, so keep them unless the
    threshold is re-tuned."""
    import cv2

    h, w = img.shape[:2]
    side = max(h, w)
    padded = cv2.copyMakeBorder(img, 0, side - h, 0, side - w, cv2.BORDER_CONSTANT, value=0)
    return cv2.dnn.blobFromImage(
        padded, 1.0 / 255, (_INPUT_SIZE, _INPUT_SIZE), (0, 0, 0), swapRB=False, crop=False
    )


#: IoU above which overlapping detections are merged (standard YOLO NMS).
_NMS_IOU = 0.45
#: Score floor applied when merging overlapping boxes.
_NMS_SCORE = 0.25


def _best_score_per_label(output, labels: list[str]) -> dict[str, float]:
    """Decode YOLOv8 output ``(1, 4 + n_classes, n_anchors)``: each
    anchor takes its single best class, low-confidence anchors are
    dropped, overlapping boxes are merged with class-agnostic NMS, and
    the best surviving confidence per label is returned."""
    import cv2
    import numpy as np

    preds = output[0].T  # (n_anchors, 4 + n_classes)
    class_scores = preds[:, 4:]
    class_ids = class_scores.argmax(axis=1)
    confs = class_scores.max(axis=1)
    keep = confs >= _MIN_CONFIDENCE
    if not keep.any():
        return {}
    cx, cy, w, h = preds[keep, :4].T
    boxes = np.stack([cx - w / 2, cy - h / 2, w, h], axis=1).tolist()
    kept_confs = confs[keep]
    kept_ids = class_ids[keep]
    kept = cv2.dnn.NMSBoxes(boxes, kept_confs.tolist(), _NMS_SCORE, _NMS_IOU)
    best: dict[str, float] = {}
    for i in np.asarray(kept).flatten():
        label = labels[int(kept_ids[i])]
        best[label] = max(best.get(label, 0.0), float(kept_confs[i]))
    return best


def _validate_labels(labels: list[str], output_shape) -> None:
    """Refuse a model whose class names don't support scoring.

    Missing or mismatched names used to score every photo 0 without a
    word. Raise :class:`ModelIntegrityError` instead — the same loud
    failure as a tampered file, which callers already never swallow.
    """
    from bpp.scoring.model_base import ModelIntegrityError

    if not labels:
        raise ModelIntegrityError("NudeNet model metadata has no class names; can't score nudity.")
    n_out = (
        output_shape[1] - 4 if len(output_shape) == 3 and isinstance(output_shape[1], int) else None
    )
    if n_out is not None and n_out != len(labels):
        raise ModelIntegrityError(
            f"NudeNet model has {len(labels)} class names but its output has {n_out} classes."
        )
    missing = sorted((_GENITAL_LABELS | _SECONDARY_LABELS) - set(labels))
    if missing:
        raise ModelIntegrityError(f"NudeNet model is missing scored classes: {missing}.")


class _NudeNetSession:
    """ONNX session + class names read from the model's own metadata."""

    def __init__(self, model_path: str) -> None:
        import ast

        import onnxruntime

        from bpp.scoring.onnx_providers import get_providers

        self.session = onnxruntime.InferenceSession(model_path, providers=get_providers())
        self.input_name = self.session.get_inputs()[0].name
        names = self.session.get_modelmeta().custom_metadata_map.get("names")
        try:
            parsed = ast.literal_eval(names) if names else None
            self.labels = [parsed[i] for i in sorted(parsed)] if parsed else []
        except (ValueError, SyntaxError, TypeError, KeyError):
            self.labels = []
        _validate_labels(self.labels, self.session.get_outputs()[0].shape)

    def label_scores(self, filepath: str) -> dict[str, float]:
        """Return ``{label: max confidence}`` for labels at/above the
        detection threshold. Raises the read error when the photo can't be read.

        Reads the file itself at full size (as it always has) rather than
        taking the pipeline's downscaled image: the 0.7 threshold was tuned
        on this input, and the downscale follows the "Max scoring
        resolution" setting (down to 256 px), which shifted scores by up to
        ~0.5 in measurements."""
        from bpp.scoring.aggregate import load_image_for_scoring
        from bpp.utils.raw import RawSupportMissing

        try:
            # A read failure propagates with its real cause, so
            # score_nudity reports it once per process, not per photo.
            img = load_image_for_scoring(filepath)
        except RawSupportMissing:
            return {}  # the RAW module already warned, once
        output = self.session.run(None, {self.input_name: _to_blob(img)})[0]
        return _best_score_per_label(output, self.labels)


def _create_nude_detector(path):
    """Build the ONNX session against the downloaded, verified file."""
    return _NudeNetSession(str(path))


#: Module-level singleton — the picker's catalog hook (in
#: bpp.web.bp_model_registry._catalog_loaders) drives explicit
#: Download / Uninstall through ``ensure_nudenet_model`` and
#: ``remove_local_weights`` above; the loader proper goes through
#: ``_get_detector`` which fires the policy gate before
#: ``_create_nude_detector`` runs.
_nudenet = ModelSingleton(
    name="content filter model (NudeNet)",
    model_path=_local_model_path(),
    model_url=NUDENET_MODEL_URL,
    create_fn=_create_nude_detector,
    registry_id=REGISTRY_ID,
    model_sha256=NUDENET_MODEL_SHA256,
    import_check=requires("onnxruntime"),
)


# Lifecycle registry entry for Settings → Models (name must match the
# ``_file_info`` label in bpp/web/models_status.py).
from bpp.scoring.model_base import ModelEntry, ModelRegistry  # noqa: E402

ModelRegistry.register(
    ModelEntry(
        name="NudeNet 320n",
        path=str(model_file()),
        url=NUDENET_MODEL_URL,
        sha256=NUDENET_MODEL_SHA256,
        reset=_nudenet.reset,
        legal_id=REGISTRY_ID,
    )
)

# ---------------------------------------------------------------------------
# Availability check — mirrors face_embed.is_available()
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """Return *True* if detection CAN be turned on — ONNX Runtime is
    importable (the model itself is downloaded only after the user
    accepts it). Says nothing about whether it is on; see
    :func:`is_active`."""
    return _nudenet.is_available()


def is_active() -> bool:
    """Return *True* only when automatic detection will actually run:
    ONNX Runtime is importable, the license gate passes (accepted with
    the CURRENT acknowledgment text, under the current use context) and
    the weights are on disk.

    Drives the "is nudity detection on?" UI (polled). Cheap: the license
    check is cached until the acceptance files change, plus a stat — no
    model load.
    """
    from bpp.registry import ModelLoadBlockedError

    if not is_available() or not is_on_disk():
        return False
    try:
        _nudenet.enforce_license()
    except ModelLoadBlockedError:
        return False
    return True


# ---------------------------------------------------------------------------
# Singleton detector
# ---------------------------------------------------------------------------


def _get_detector():
    """Return (and lazily create) the shared NudeNet ONNX session.

    Enforces the registry policy gate FIRST. NudeNet is licensed
    under AGPL-3.0; the click-through acceptance dialog must have
    been completed (with separate-rights assertion if the user is
    in commercial mode) before the model downloads.
    """
    return _nudenet.get_or_raise()


# ---------------------------------------------------------------------------
# Label sets
# ---------------------------------------------------------------------------

# Labels that indicate exposed genitalia — the primary safety concern.
# These MUST match the class names in the 320n model's metadata;
# 320n.onnx is a v3 model. The v2-era names (EXPOSED_GENITALIA_F, …) never
# match v3 output, which silently zeroed every nudity score —
# test_nudity.py asserts these sets against the label names stored in
# the model file's own metadata.
_GENITAL_LABELS = frozenset(
    {
        "FEMALE_GENITALIA_EXPOSED",
        "MALE_GENITALIA_EXPOSED",
        "ANUS_EXPOSED",
    }
)

# Secondary labels — flagged at reduced weight so that beach / bath photos
# are not aggressively penalised.
_SECONDARY_LABELS = frozenset(
    {
        "BUTTOCKS_EXPOSED",
        "FEMALE_BREAST_EXPOSED",
    }
)

# ---------------------------------------------------------------------------
# Public scoring function
# ---------------------------------------------------------------------------


#: First per-photo scan failure is a WARNING; repeats are DEBUG so a
#: systematic problem is visible without flooding the log.
_scan_failure_logged = False


def _reset_scan_failure_log_for_tests() -> None:
    global _scan_failure_logged
    _scan_failure_logged = False


def score_nudity(filepath: str) -> float:
    """Compute a nudity score in [0, 1] for the image at *filepath*.

    The score is dominated by exposed-genitalia detections.  Secondary
    exposures (buttocks, breasts) contribute at 30 % weight.

    Returns ``0.0`` when no relevant detections are found **or** when the
    detector raises an exception during a SINGLE-IMAGE detect() call
    (safe default — no penalty applied for transient image errors).

    Integrity failures (`ModelIntegrityError`) are NOT swallowed here.
    They propagate up so the caller / pipeline can abort cleanly
    rather than silently report every photo as 0.0 with a tampered
    model.

    The image is read from *filepath* (EXIF-transposed, HEIC-capable)
    and letterboxed to the model's 320 px input.
    """
    detector = _get_detector()
    if detector is None:
        return 0.0
    try:
        scores = detector.label_scores(filepath)
    except Exception as e:
        global _scan_failure_logged
        if not _scan_failure_logged:
            _scan_failure_logged = True
            log.warning(
                "Content filter couldn't scan %s (%s); scored as not sensitive. "
                "Further scan failures this run are logged at debug level.",
                filepath,
                e,
            )
        else:
            log.debug("NudeNet detection failed for %s: %s", filepath, e)
        return 0.0

    max_genital = max((s for lbl, s in scores.items() if lbl in _GENITAL_LABELS), default=0.0)
    max_secondary = max((s for lbl, s in scores.items() if lbl in _SECONDARY_LABELS), default=0.0)

    score = max_genital + 0.3 * max_secondary
    return min(1.0, score)
