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
        toggle_key="model_retinaface",
        license_id="MIT",
        description="RetinaFace ONNX detector for tiny faces",
    ))

Note that the orchestrator (``_collect_detections``) still
has detector-specific early-exit logic — SCRFD short-circuits
when it finds a confident face, YuNet runs as the fast first
pass, etc. That ordering captures real performance properties
that aren't generalizable. A plugin's detector runs through
``run_optional_detector(name, image, min_confidence)`` as an
additional pass; it doesn't replace the built-in pipeline.
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

    ``license_id``: SPDX-style identifier for the detector's model
        weights / code (``"BSD-2-Clause"``, ``"Apache-2.0"``,
        ``"MIT"``, ``"AGPL-3.0"``). Surfaces in the model registry
        UI so users can see what licences govern their pipeline.

    ``description``: one-line tooltip text. Optional.
    """

    name: str
    detect: DetectFn
    toggle_key: str | None = None
    license_id: str = ""
    description: str = ""


_REGISTRY: _ScoringRegistry[FaceDetector] = _ScoringRegistry("face detector", _log)


def register_detector(detector: FaceDetector) -> None:
    """Add a detector to the registry. Idempotent — replaces on same name."""
    _log.debug(
        "Registered face detector %r (license=%s, toggle_key=%s)",
        detector.name,
        detector.license_id or "unspecified",
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
