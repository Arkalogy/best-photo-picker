"""Replace built-in face detectors for a test through the registry.

The detection pipeline binds detectors through
``bpp.scoring.face_detector_registry`` alone; tests that want a detector
to return canned boxes register a replacement and restore the original.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from dataclasses import replace

from bpp.scoring.face_detector_registry import get_detector, register_detector


@contextlib.contextmanager
def stub_detectors(stubs: dict[str, Callable]) -> Iterator[None]:
    import bpp.scoring.face  # noqa: F401  (registers the built-ins)

    originals = {}
    for name, fn in stubs.items():
        current = get_detector(name)
        assert current is not None, f"no built-in detector {name!r}"
        originals[name] = current
        register_detector(replace(current, detect=fn), builtin=True)
    try:
        yield
    finally:
        for det in originals.values():
            register_detector(det, builtin=True)
