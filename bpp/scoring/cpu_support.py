"""Whether this processor can run mediapipe's native library.

mediapipe's Linux x86-64 wheels are compiled for AVX. On a processor
without it (many NAS Celerons and Atoms, or an emulator) loading the
Tasks library kills the whole process with SIGILL, no exception to
catch. So, on Linux x86-64 only, the library is first loaded in a
throwaway child, once at process start (:func:`probe_before_threads`);
the answer is kept in the environment, which worker processes inherit. (Reading /proc/cpuinfo is
not enough: under emulation it describes the host's processor.)
"""

from __future__ import annotations

import importlib.util
import os
import platform
import signal
import subprocess
import sys
import threading

from bpp.utils.logging import get_logger

log = get_logger(__name__)

_ENV = "BPP_MEDIAPIPE_CPU_OK"
_lock = threading.Lock()
_verdict: bool | None = None

#: Shown in the analysis log and the server log when the check fails.
WARNING = (
    "MediaPipe can't run on this processor (it needs AVX): pose, expression, "
    "hand, subject-segmentation and BlazeFace short-range scoring are skipped"
)
_PROBE = (
    "import ctypes, os, mediapipe, mediapipe.tasks.python.vision\n"
    "lib = os.path.join(os.path.dirname(mediapipe.__file__), 'tasks', 'c', 'libmediapipe.so')\n"
    "if os.path.exists(lib): ctypes.CDLL(lib)\n"
)


class CpuUnsupported(ImportError):
    """The package is installed, but this processor can't run it."""


def _probe() -> bool:
    try:
        done = subprocess.run(
            [sys.executable, "-c", _PROBE], capture_output=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return True  # inconclusive; don't turn scorers off on a guess
    # Only an illegal instruction means the processor can't run it. A
    # missing package is an ordinary ImportError for the caller; another
    # death (an out-of-memory kill) says nothing about the processor.
    return done.returncode != -signal.SIGILL


def mediapipe_cpu_supported() -> bool:
    """False when loading mediapipe's native library would crash."""
    global _verdict
    if _verdict is not None:
        return _verdict
    with _lock:
        if _verdict is None:
            _verdict = _decide()
    return _verdict


def _decide() -> bool:
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        return True
    if os.environ.get(_ENV) in ("0", "1"):
        return os.environ[_ENV] == "1"
    if importlib.util.find_spec("mediapipe") is None:
        return True
    ok = _probe()
    if not ok:
        log.warning(WARNING)
    return ok


def probe_before_threads() -> None:
    """Decide once, at process start, and leave the answer in the
    environment for every worker this process starts. Writing the
    environment later, with native thread pools running, isn't safe."""
    ok = mediapipe_cpu_supported()
    os.environ.setdefault(_ENV, "1" if ok else "0")


def _reset_for_tests() -> None:
    global _verdict
    _verdict = None


def cpu_unsupported(module: str) -> bool:
    """True if *module* is mediapipe and this processor would crash loading it."""
    return module.split(".")[0] == "mediapipe" and not mediapipe_cpu_supported()


def refuse_if_cpu_unsupported(module: str) -> None:
    """Raise :class:`CpuUnsupported` before importing mediapipe on a
    processor that would crash loading it."""
    if cpu_unsupported(module):
        raise CpuUnsupported(f"{module}: this processor can't run it (no AVX)", name=module)
