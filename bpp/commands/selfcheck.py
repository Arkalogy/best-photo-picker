"""``bpp selfcheck``: load the native libraries the desktop app bundles.

The checks are the list in ``CHECKS``: the base install plus the web,
HEIC, RAW, faces (MediaPipe, LiteRT, SciPy) and inpainting extras. The
optional dlib face backend of a pip install is not among them (the app
leaves it out). It opens no photo library: each check only imports a
package (or loads the shared library it loads at runtime), exactly as
bpp does at first use, and reports one of

* ``ok``      — it loaded;
* ``missing`` — the package isn't installed (an optional part of a pip
  install, e.g. ``bppicker[raw]``);
* ``FAILED``  — it is installed but would not load;
* ``skipped`` — this processor can't run it, and bpp turns it off
  (MediaPipe on an x86-64 processor without AVX).

An import may write what it would write at first use anyway (MediaPipe
pulls in matplotlib, which builds its font cache once), and starting
``bpp`` reads the model registry online as every command does (set
``BPP_DISABLE_REMOTE_REGISTRY=1`` to skip that).

The Mac app's release check runs it with ``--require-all`` in the signed
server, so a library macOS refuses to load fails the release instead of
a user's first import, face scan or inpaint.
"""

from __future__ import annotations

import argparse
import ctypes
import importlib
import importlib.util
import os
import sys
from collections.abc import Callable

OK = "ok"
MISSING = "missing"
FAILED = "FAILED"
SKIPPED = "skipped"


def _import(module: str) -> Callable[[], object]:
    return lambda: importlib.import_module(module)


def _opencv() -> None:
    import cv2

    cascade = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    if not os.path.isfile(cascade):  # the last-resort face detector reads it
        raise FileNotFoundError(cascade)


def _onnxruntime() -> None:
    import onnxruntime

    if not onnxruntime.get_available_providers():
        raise RuntimeError("onnxruntime reports no execution providers")


def _cryptography() -> None:
    from cryptography.hazmat.primitives.asymmetric import ed25519

    ed25519.Ed25519PrivateKey.generate()  # the model-registry verifier's backend


def _pillow_heif() -> None:
    from pillow_heif import register_heif_opener

    register_heif_opener()


def _mediapipe() -> None:
    from bpp.scoring.cpu_support import refuse_if_cpu_unsupported

    # Loading it would crash this process on a processor without AVX; bpp
    # checks first everywhere it imports mediapipe, and so does this.
    refuse_if_cpu_unsupported("mediapipe")
    import mediapipe

    # Load the native code the Tasks API runs on now, so a library the OS
    # refuses shows up here. Newer releases load a C library with ctypes
    # at first use (through mediapipe_c_bindings); older ones (e.g. 0.10.21,
    # which `mediapipe>=0.10` allows) ship a Python extension instead.
    if importlib.util.find_spec("mediapipe.tasks.python.core.mediapipe_c_bindings"):
        name = {"darwin": "libmediapipe.dylib", "linux": "libmediapipe.so"}.get(sys.platform)
        if name:
            ctypes.CDLL(os.path.join(os.path.dirname(mediapipe.__file__), "tasks", "c", name))
    else:
        importlib.import_module("mediapipe.python._framework_bindings")


def _litert() -> None:
    from ai_edge_litert.interpreter import Interpreter  # noqa: F401


def _inpainting() -> None:
    from simple_lama_inpainting import SimpleLama  # noqa: F401  (loads PyTorch)


# (name shown, top-level module whose absence means "not installed", check)
CHECKS: list[tuple[str, str, Callable[[], object]]] = [
    ("numpy", "numpy", _import("numpy")),
    ("Pillow", "PIL", _import("PIL.Image")),
    ("OpenCV", "cv2", _opencv),
    ("ONNX Runtime", "onnxruntime", _onnxruntime),
    ("cryptography", "cryptography", _cryptography),
    ("Flask", "flask", _import("flask")),
    ("HEIC (pillow-heif)", "pillow_heif", _pillow_heif),
    ("camera RAW (rawpy)", "rawpy", _import("rawpy")),
    ("SciPy clustering", "scipy", _import("scipy.cluster.hierarchy")),
    ("MediaPipe", "mediapipe", _mediapipe),
    ("LiteRT", "ai_edge_litert", _litert),
    ("inpainting (PyTorch)", "simple_lama_inpainting", _inpainting),
]


def run_check(top: str, check: Callable[[], object]) -> tuple[str, str]:
    """(status, detail) for one check. Only the package itself being
    absent is "missing"; an installed package that lacks one of its own
    modules (a partial install, a module a frozen build left out) failed."""
    from bpp.scoring.cpu_support import CpuUnsupported

    try:
        check()
    except CpuUnsupported as e:
        return SKIPPED, str(e)
    except ModuleNotFoundError as e:
        if e.name == top:
            return MISSING, ""
        return FAILED, f"{type(e).__name__}: {e}"
    except Exception as e:  # anything else means installed but broken
        return FAILED, f"{type(e).__name__}: {e}"
    return OK, ""


def do_selfcheck(args: argparse.Namespace) -> int:
    failed = absent = 0
    for name, top, check in CHECKS:
        status, detail = run_check(top, check)
        print(f"{status:<8}{name}" + (f": {detail}" if detail else ""), flush=True)
        failed += status == FAILED
        absent += status in (MISSING, SKIPPED)
    if failed or (absent and args.require_all):
        return 1
    return 0
