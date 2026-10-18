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

A failed import (not installed, or installed but broken) is tried once
and the verdict cached: the per-photo paths call this, and a failing
import is slow. Installing it from the app takes effect after a restart
(the installer holds it until then: see :mod:`bpp.scoring.install_hold`).
"""

from __future__ import annotations

import threading
from types import ModuleType

from bpp.scoring.install_hold import (
    HeldForRestart,
    held_for_restart,
    held_reason,
    import_gated,
)
from bpp.scoring.model_load_gate import MemoizedLoadGate
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_gate = MemoizedLoadGate("dlib_face_recognition_resnet_v1")
_NOT_ACCEPTED = "not accepted (review its license in Settings → Models)"

_lock = threading.Lock()
#: ``(module, "")`` once loaded, ``(None, why)`` once an import failed,
#: None until tried. One tuple, so a reader never sees a module from one
#: attempt with the reason from another.
_state: tuple[ModuleType | None, str] | None = None

_FIX_HINT = (
    'If it\'s installed, `pip install "setuptools<81"` usually fixes this '
    "(its model package still needs pkg_resources)."
)


def load_face_recognition() -> ModuleType | None:
    """Return the ``face_recognition`` module, or None if it can't load."""
    return _load()[0]


def load_face_recognition_or_reason() -> tuple[ModuleType | None, str]:
    """The module and, when it's None, why — both from the same attempt."""
    return _load()


def _load() -> tuple[ModuleType | None, str]:
    global _state
    if not license_accepted():
        state = _state
        if state is not None and state[0] is None:
            return state  # a failed import is the more basic problem
        return None, _NOT_ACCEPTED
    state = _state
    # Installed from the app: it loads after a restart. Asked here because
    # a failure cached before the install ("not installed") would answer
    # otherwise; the import's own check (below) covers a hold that starts
    # while it runs.
    if (state is None or state[0] is None) and held_for_restart("face_recognition"):
        return None, held_reason("face_recognition")
    if state is not None:
        return state
    with _lock:
        if _state is None:
            try:
                _state = _try_import()
            except HeldForRestart:
                # Not a verdict to keep: the hold may be lifted (pip ended
                # without writing anything).
                return None, held_reason("face_recognition")
        return _state


def _try_import() -> tuple[ModuleType | None, str]:
    try:
        face_recognition = import_gated("face_recognition")
    except (SystemExit, Exception) as exc:  # quit() raises SystemExit
        if isinstance(exc, HeldForRestart):
            raise  # _load answers "restart" without caching it
        if isinstance(exc, ModuleNotFoundError) and exc.name == "face_recognition":
            return None, f"not installed ({exc})"
        if isinstance(exc, ModuleNotFoundError):
            why = f"missing {exc.name or exc}"
        else:
            why = f"{type(exc).__name__}: {exc}"
        reason = f"installed but failed to load ({why})"
        log.warning("face_recognition (dlib) is %s; continuing without it. %s", reason, _FIX_HINT)
        return None, reason
    return face_recognition, ""


def license_accepted() -> bool:
    """Whether its license gate lets it load (cached; imports nothing)."""
    from bpp.registry.policy import ModelLoadBlockedError

    try:
        _gate.enforce()
    except ModelLoadBlockedError:
        return False
    return True


def looks_installed() -> bool:
    """Without importing it (slow): loaded, or on disk and not known broken."""
    state = _state
    if state is not None:
        return state[0] is not None
    import importlib.util

    try:
        return importlib.util.find_spec("face_recognition") is not None
    except (ImportError, ValueError):
        return False


def _reset_for_tests() -> None:
    global _state
    with _lock:
        _state = None
    _gate.reset()
