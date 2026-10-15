"""Safe import of the optional ``face_recognition`` (dlib) package.

``face_recognition`` imports ``face_recognition_models``, which needs
``pkg_resources``. setuptools 81+ no longer ships ``pkg_resources``, and
when that import fails ``face_recognition`` prints an install hint and
calls ``quit()`` — ending the whole bpp process (with exit status 0)
in the middle of an analysis. Every bpp import of ``face_recognition``
goes through :func:`load_face_recognition`, which turns any failure,
including that ``SystemExit``, into "not available" plus one warning.

Importing the package loads dlib's landmark model, which is restricted
for commercial use (registry entry ``dlib_face_recognition_resnet_v1``),
so the loader is also that entry's license gate: until the user accepts
it, the package is reported unavailable and never imported. A refusal
isn't cached, so a later acceptance takes effect on the next call.
"""

from __future__ import annotations

import threading
from types import ModuleType

from bpp.scoring.model_load_gate import MemoizedLoadGate
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_gate = MemoizedLoadGate("dlib_face_recognition_resnet_v1")
_NOT_ACCEPTED = "not accepted (review its license in Settings → Models)"

_lock = threading.Lock()
_loaded = False
_module: ModuleType | None = None
_reason = ""


def load_face_recognition() -> ModuleType | None:
    """Return the ``face_recognition`` module, or None if it can't load."""
    global _loaded, _module, _reason
    from bpp.registry.policy import ModelLoadBlockedError

    try:
        _gate.enforce()
    except ModelLoadBlockedError:
        if not _loaded:
            _reason = _NOT_ACCEPTED
        return None
    if _loaded:
        return _module
    with _lock:
        if _loaded:
            return _module
        try:
            import face_recognition

            _module = face_recognition
        except ImportError as exc:
            _reason = f"not installed ({exc})"
        except (SystemExit, Exception) as exc:  # quit() raises SystemExit
            _reason = f"installed but failed to load ({type(exc).__name__}: {exc})"
            log.warning(
                "face_recognition (dlib) is %s; continuing without it. If it's "
                'installed, `pip install "setuptools<81"` usually fixes this '
                "(its model package still needs pkg_resources).",
                _reason,
            )
        _loaded = True
    return _module


def face_recognition_unavailable_reason() -> str:
    """Why :func:`load_face_recognition` returned None (empty if loaded)."""
    load_face_recognition()
    return _reason


def _reset_for_tests() -> None:
    global _loaded, _module, _reason
    with _lock:
        _loaded, _module, _reason = False, None, ""
    _gate.reset()
