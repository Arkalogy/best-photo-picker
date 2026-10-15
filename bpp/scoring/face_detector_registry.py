"""Public registry for face-detector backends.

``bpp.scoring.face`` had a private ``_DETECTORS`` dict
that captured the detector pipeline (YuNet / SCRFD / BlazeFace /
dlib / Haar) but exposed no API for plugin authors. The
orchestrator at ``_collect_detections`` referenced detectors by
hardcoded string keys.

This module promotes that private dict to a documented extension
point. A plugin can register its own detector — say, a custom
RetinaFace ONNX runner — via:

    from bpp.scoring.face_detector_registry import (
        FaceDetector, register_detector
    )

    def my_retinaface_detect(image, min_confidence):
        # ... return list[(x, y, w, h, confidence)]
        ...

    register_detector(FaceDetector(
        name="retinaface",
        detect=my_retinaface_detect,
        registry_id="my_retinaface",  # a ModelEntry you register_entry()'d
        toggle_key="model_retinaface",
        description="RetinaFace ONNX detector for tiny faces",
    ))

The orchestrator (``_collect_detections``) keeps its built-in
detector ordering and early exits (SCRFD short-circuits on a
confident face, YuNet runs as the fast first pass, …). Every
registered plugin detector then runs on each photo via
:func:`run_plugin_detectors`, and its boxes join the same NMS
merge; it adds to the built-in pipeline rather than replacing it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np

from bpp.scoring._registry_base import _ScoringRegistry
from bpp.utils.logging import get_logger

_log = get_logger(__name__)

# Type alias for the detector contract:
#   (image, min_confidence) -> list[(x, y, w, h, confidence)]
DetectFn = Callable[[np.ndarray, float], list[tuple[int, int, int, int, float]]]


@dataclass(frozen=True)
class FaceDetector:
    """Metadata + detect function for one detector backend.

    ``name``: short identifier used in the registry and (when
        applicable) the orchestrator's ordering. Match the existing
        keys (``yunet`` / ``scrfd`` / ``blazeface_fr`` /
        ``mediapipe_sr`` / ``dlib`` / ``haar``) only if you're
        replacing one of those backends; otherwise pick something
        unique.

    ``detect``: callable matching ``DetectFn``. Receives a BGR
        ndarray and a min-confidence float; returns
        ``list[(x, y, w, h, confidence)]``. Coordinates and box
        dimensions are pixel ints; confidence is in [0.0, 1.0].
        Empty list = no faces. Implementation must be thread-safe
        (the analyze worker pool calls into multiple detectors
        concurrently across photos).

    ``toggle_key``: matching DB setting key (typically
        ``model_<name>``). When set, the orchestrator skips this
        detector if ``model_toggles[toggle_key]`` is False. Use
        ``None`` for always-on detectors (e.g. the Haar fallback).

    ``registry_id``: id of the :mod:`bpp.registry` entry for the
        detector's weights. The license is read from that entry —
        it is never written here — so labels can't drift from the
        terms the user accepted.

    ``description``: one-line tooltip text. Optional.
    """

    name: str
    detect: DetectFn
    registry_id: str
    toggle_key: str | None = None
    description: str = ""

    @property
    def license_label(self) -> str:
        """Plain-English license, derived from the registry entry."""
        from bpp.registry.labels import license_label_for

        return license_label_for(self.registry_id)


_REGISTRY: _ScoringRegistry[FaceDetector] = _ScoringRegistry("face detector", _log)


#: Names of the built-in detectors. A plugin can't register under one of
#: them: the pipeline would ignore it (or, for an embedder, tag the
#: built-in's stored rows with the plugin's registry id).
BUILTIN_NAMES: set[str] = set()


def register_detector(detector: FaceDetector, *, builtin: bool = False) -> None:
    """Add an detector to the registry. Re-registering a name replaces it,
    except that only built-in code (``builtin=True``) may use a
    built-in name."""
    if builtin:
        BUILTIN_NAMES.add(detector.name)
    elif detector.name in BUILTIN_NAMES:
        raise ValueError(
            f"{detector.name!r} is a built-in face detector name; register the "
            "plugin under its own name."
        )
    _log.debug(
        "Registered face detector %r (registry_id=%s, toggle_key=%s)",
        detector.name,
        detector.registry_id,
        detector.toggle_key or "always-on",
    )
    _REGISTRY.register(detector, detector.name)


def get_detector(name: str) -> FaceDetector | None:
    """Return the registered detector by name, or None."""
    return _REGISTRY.get(name)


def list_detectors() -> list[FaceDetector]:
    """Return all registered detectors in insertion order."""
    return _REGISTRY.list_all()


def iter_detectors() -> Iterator[FaceDetector]:
    """Iterator alternative to ``list_detectors``."""
    return _REGISTRY.iter_all()


#: Plugin detectors that already logged a failure this process.
_plugin_warned: set[str] = set()


def _reset_plugin_warnings_for_tests() -> None:
    _plugin_warned.clear()


def run_plugin_detectors(
    image: np.ndarray,
    min_confidence: float,
    model_toggles: dict[str, bool],
    *,
    max_detections: int,
) -> list[tuple[int, int, int, int, float]]:
    """Run every registered detector that isn't a built-in.

    Called by the face pipeline once per photo, after the built-in
    detectors; the returned boxes join the same NMS merge. A detector
    is skipped while its ``toggle_key`` setting is off or its registry
    entry's license isn't accepted (logged once). Boxes below
    ``min_confidence`` or outside the image are dropped and at most
    ``max_detections`` (the highest-confidence ones) are kept — the
    same cap the built-ins get. A detector that raises is logged once
    at WARNING and skipped, so a plugin bug never breaks analysis.
    """
    from bpp.scoring.model_load_gate import license_allows

    h, w = image.shape[:2]
    faces: list[tuple[int, int, int, int, float]] = []
    for det in _REGISTRY.iter_all():
        if det.name in BUILTIN_NAMES:
            continue
        if det.toggle_key and not model_toggles.get(det.toggle_key, True):
            continue
        if not license_allows(det.registry_id, feature=f"Face detector {det.name}"):
            continue
        try:
            boxes = det.detect(image, min_confidence)
            for x, y, bw, bh, conf in boxes:
                # Clamp to the image; drop boxes with nothing left inside.
                x0, y0 = max(0, int(x)), max(0, int(y))
                x1, y1 = min(w, int(x + bw)), min(h, int(y + bh))
                if conf >= min_confidence and x1 > x0 and y1 > y0:
                    faces.append((x0, y0, x1 - x0, y1 - y0, float(conf)))
        except Exception:
            if det.name not in _plugin_warned:
                _plugin_warned.add(det.name)
                _log.warning("Plugin detector %r failed; skipping it", det.name, exc_info=True)
    faces.sort(key=lambda f: f[4], reverse=True)
    return faces[:max_detections]


def run_optional_detector(
    name: str,
    image: np.ndarray,
    min_confidence: float,
) -> list[tuple[int, int, int, int, float]]:
    """Run a registered detector by name. Returns its detection
    list, or an empty list if the detector isn't registered.

    Plugin authors call this from their own scoring extensions
    when they want to invoke an additional detector pass beyond
    what ``_collect_detections`` runs natively.
    """
    detector = get_detector(name)
    if detector is None:
        return []
    return detector.detect(image, min_confidence)
