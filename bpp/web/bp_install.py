"""Runtime pip-package install endpoints (the /api/v1/install/* surface).

Split out of bp_models.py for the 500-LOC cap. Installs whitelisted pip
extras at runtime (faces, nudity, onnxruntime, inpaint) via a two-step
POST-then-GET-SSE dance. All routes are @requires_local_app — runtime
pip install is the most consequential network op in the app, so a paired
LAN device must never be able to trigger it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time

from flask import Blueprint, Response, jsonify

from bpp.errors import ConflictError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app

log = get_logger(__name__)

bp = Blueprint("install", __name__)


# --- Runtime dependency install ---

_install_lock = threading.Lock()
_install_running = False

# Whitelist of pip packages that can be installed at runtime.
# Maps short key → list of pip install specs. Using direct package names
# (instead of `bppicker[extra]`) so this works for users running from a
# local editable install (`pip install -e .`) — extras only resolve when
# bppicker is itself reachable on PyPI, which it isn't yet.
_INSTALLABLE_PACKAGES: dict[str, list[str]] = {
    "faces": ["face_recognition>=1.3", "scipy>=1.10", "mediapipe>=0.10"],
    # Match the [nudity] extra in pyproject.toml — pinned tight so
    # the SHA-256 verifier in bpp/scoring/nudity.py stays accurate.
    "nudity": ["nudenet>=3.3,<4"],
    "onnxruntime": ["onnxruntime"],
    # Pin to 0.1.0 (matches the pyproject extra). 0.1.1 pins
    # pillow<11; 0.1.2 pins pillow<10 (breaks HEIC). 0.1.0 has no
    # upper bound so pip resolves cleanly against our existing
    # Pillow >= 10 install. With this pin, --no-deps is no longer
    # required, so the runtime installer can pull torch /
    # torchvision / numpy on a base venv that doesn't already
    # have them via [faces] / [clip].
    "inpaint": ["simple-lama-inpainting==0.1.0"],
}


def _pip_available() -> bool:
    """Return True if pip is callable from the current Python."""
    return shutil.which(sys.executable) is not None


@bp.post("/api/v1/install/faces")
@requires_local_app
def api_install_faces() -> tuple[Response, int]:
    """Install bppicker[faces] via pip at runtime (legacy endpoint).

    LOCAL_APP-only — pip install is the most consequential network
    operation in the app. A paired LAN device triggering this could
    install arbitrary code from PyPI under the bpp process's
    privileges."""
    return _api_install_package("faces")


@bp.get("/api/v1/install/faces/progress")
@requires_local_app
def api_install_faces_progress() -> Response:
    """SSE stream for pip install progress (legacy endpoint).

    LOCAL_APP-only — the GET handler actually starts the pip
    subprocess inside its generator (see _api_install_progress).
    Without an owner gate, any LAN client could trigger pip install
    by hitting this URL directly, bypassing the POST start-gate."""
    return _api_install_progress("faces")


@bp.post("/api/v1/install/<key>")
@requires_local_app
def api_install_package(key: str) -> tuple[Response, int]:
    """Install a whitelisted pip package at runtime.

    LOCAL_APP-only — see api_install_faces for the threat model."""
    return _api_install_package(key)


@bp.get("/api/v1/install/<key>/progress")
@requires_local_app
def api_install_progress(key: str) -> Response:
    """SSE stream for pip install progress.

    LOCAL_APP-only — this generator runs the actual pip subprocess.
    The GET method is misleading: it has the side effect of spawning
    pip. Owner-only gate prevents a LAN client from kicking off
    a pip install by simply opening the URL."""
    return _api_install_progress(key)


def _api_install_package(key: str) -> tuple[Response, int]:
    global _install_running
    specs = _INSTALLABLE_PACKAGES.get(key)
    if not specs:
        raise ValidationError(f"Unknown package: {key}", key=key)
    if not _pip_available():
        raise ValidationError(
            "Cannot install: pip not available",
            reason="pip_missing",
        )
    with _install_lock:
        if _install_running:
            raise ConflictError("Install already in progress")
        _install_running = True
    return jsonify({"status": "started", "packages": specs}), 202


@bp.get("/api/v1/install/<key>/info")
def api_install_info(key: str) -> tuple[Response, int]:
    """Return the pip specs that the install endpoint will run for *key*.
    Powers the consent dialog so the user sees exactly which packages
    are about to be installed before clicking OK."""
    specs = _INSTALLABLE_PACKAGES.get(key)
    if specs is None:
        raise ValidationError(f"Unknown package: {key}", key=key)
    return jsonify({"key": key, "packages": specs, "host": "pypi.org"}), 200


def _api_install_progress(key: str) -> Response:
    specs = _INSTALLABLE_PACKAGES.get(key)
    if not specs:
        raise ValidationError(f"Unknown package: {key}", key=key)

    global _install_running
    # Refuse unless POST /install/<key> was just called (it sets
    # _install_running=True). Without this gate, the GET handler
    # itself would launch pip — the @requires_local_app decorator
    # plus this start-gate make the contract: install is a two-step
    # POST-then-GET dance, never a one-shot GET.
    with _install_lock:
        if not _install_running:
            raise ConflictError(
                "No install in progress; POST /install/<key> first",
                hint="post_first",
            )

    # Outside-cap on the whole pip invocation. Slow PyPI mirrors,
    # network stalls, or a hung pip resolver can otherwise block the
    # SSE stream — and the worker thread holding _install_running —
    # indefinitely.
    _PIP_HARD_TIMEOUT_S = 600  # 10 minutes
    # Grace period for pip to exit after we close its stdout (or after
    # the client disconnects mid-stream and we terminate the process).
    _PIP_TERM_GRACE_S = 5

    def generate():
        proc: subprocess.Popen[str] | None = None
        try:
            log.info("pip install %s starting", " ".join(specs))
            yield f"data: {json.dumps({'type': 'start'})}\n\n"
            proc = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", *specs],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert proc.stdout is not None  # guaranteed by stdout=PIPE
            start = time.monotonic()
            # Keep the last few non-empty log lines so we can surface them
            # in the error message. "pip exited with code 1" alone tells
            # the user nothing.
            recent_lines: list[str] = []
            for line in proc.stdout:
                if time.monotonic() - start > _PIP_HARD_TIMEOUT_S:
                    raise TimeoutError(f"pip install exceeded {_PIP_HARD_TIMEOUT_S}s; aborting")
                line = line.rstrip()
                if line:
                    recent_lines.append(line)
                    if len(recent_lines) > 6:
                        recent_lines.pop(0)
                    yield f"data: {json.dumps({'type': 'log', 'message': line})}\n\n"
            # stdout closed → pip should be exiting. Bound the wait so a
            # post-close hang can't lock the worker thread.
            proc.wait(timeout=_PIP_TERM_GRACE_S)
            if proc.returncode == 0:
                log.info("pip install %s completed", " ".join(specs))
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
            else:
                # Find the most informative line from the tail. Prefer
                # ones that start with ERROR: which pip uses for its
                # actual diagnostic; fall back to the very last line.
                hint = next(
                    (line_ for line_ in reversed(recent_lines) if line_.startswith("ERROR:")),
                    recent_lines[-1] if recent_lines else "",
                )
                msg = f"pip exited with code {proc.returncode}"
                if hint:
                    msg += f" — {hint}"
                log.error("pip install %s failed: %s", " ".join(specs), msg)
                yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
        except GeneratorExit:
            # Client disconnected mid-stream. Don't yield (the connection
            # is gone), but DO clean the subprocess in `finally` so it
            # can't orphan with an open pipe.
            raise
        except Exception as e:
            log.error("Dependency install failed: %s", e, exc_info=True)
            msg = "Installation failed. Check server logs for details."
            yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
        finally:
            # Subprocess teardown: close stdout, then terminate→kill if
            # still running. This runs on normal completion AND on
            # GeneratorExit (client disconnect) AND on exception, so
            # there's no path that leaks an orphan pip process.
            if proc is not None:
                if proc.stdout is not None and not proc.stdout.closed:
                    import contextlib

                    with contextlib.suppress(OSError):
                        proc.stdout.close()
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=_PIP_TERM_GRACE_S)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        try:
                            proc.wait(timeout=_PIP_TERM_GRACE_S)
                        except subprocess.TimeoutExpired:
                            log.error(
                                "pip install (%s) refused SIGKILL — leaked process",
                                " ".join(specs),
                            )
            global _install_running
            with _install_lock:
                _install_running = False

    return Response(generate(), mimetype="text/event-stream")
