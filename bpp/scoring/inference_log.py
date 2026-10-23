"""Report a detector's inference failure without flooding the log.

The MediaPipe detectors (expression, pose, segmentation, the hand
filter) return a neutral value when ``detect`` raises, so a broken model
file or library shows up only as every photo scoring the same. The first
failure per detector is a WARNING with the traceback; later ones are
DEBUG, so a detector that fails on every photo of a 30k run reports once
at a level an operator reads.
"""

from __future__ import annotations

import threading

from bpp.utils.logging import get_logger

log = get_logger(__name__)

_warned: set[str] = set()
_lock = threading.Lock()


def report_inference_failure(detector: str) -> None:
    """Call from an ``except`` block: logs the active exception."""
    with _lock:
        first = detector not in _warned
        _warned.add(detector)
    if first:
        log.warning(
            "%s inference failed; its photos get the neutral score "
            "(later failures of this detector log at DEBUG)",
            detector,
            exc_info=True,
        )
    else:
        log.debug("%s inference failed again", detector, exc_info=True)


def _reset_for_tests() -> None:
    with _lock:
        _warned.clear()
