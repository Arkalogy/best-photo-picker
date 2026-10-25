"""Runtime pip-package install endpoints (the /api/v1/install/* surface).

Split out of bp_models.py for the 500-LOC cap. Installs whitelisted pip
extras at runtime (faces, nudity, onnxruntime, inpaint) via a two-step
POST-then-GET-SSE dance. All routes are @requires_local_app — runtime
pip install is the most consequential network op in the app, so a paired
LAN device must never be able to trigger it.
"""

from __future__ import annotations

import contextlib
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from flask import Blueprint, Response, jsonify

from bpp.errors import ConflictError, ValidationError
from bpp.scoring.install_hold import held_keys
from bpp.utils.logging import get_logger
from bpp.web import pip_process
from bpp.web.install_busy import busy_workers as _busy_workers
from bpp.web.install_specs import INSTALLABLE_PACKAGES as _INSTALLABLE_PACKAGES
from bpp.web.install_status import install_state
from bpp.web.share import principal_is_local_app, requires_local_app

log = get_logger(__name__)

bp = Blueprint("install", __name__)


# --- Runtime dependency install ---


@dataclass
class _Claim:
    """The install POST /install claimed (one at a time)."""

    key: str
    #: Its identity: a stream only acts on the claim it took.
    token: int
    #: When it was claimed (monotonic seconds); None once its progress
    #: stream has started (the stream then always releases it).
    claimed_at: float | None
    #: A progress stream has taken it (a second would run a second pip).
    stream_taken: bool = False


_install_lock = threading.Lock()
#: The running install, if any (read and replaced under _install_lock).
_claim: _Claim | None = None
_last_token = 0
#: A claim whose stream hasn't started by then is released (tab closed
#: between the POST and the GET), so it can't block work forever.
_CLAIM_TTL_S = 30.0
#: Long work under way that an install mustn't start during (a library
#: switch: opening a library starts recovery work that loads packages).
#: What it is, for the "wait for …" message.
_long_work: list[str] = []


def _release_stale_claim_locked() -> None:
    """Call with _install_lock held."""
    global _claim
    claimed = _claim.claimed_at if _claim is not None else None
    if claimed is not None and time.monotonic() - claimed > _CLAIM_TTL_S:
        log.warning("An install was started but its progress never connected; releasing it")
        _claim = None


def _clear_claim_locked(token: int) -> None:
    """Release the install claim *token* (call with _install_lock held) —
    only if it's still that claim, not a newer install's."""
    global _claim
    if _claim is not None and _claim.token == token:
        _claim = None


def installable_specs(key: str) -> list[str] | None:
    """The pip specs install *key* runs (None: not installable)."""
    return _INSTALLABLE_PACKAGES.get(key)


def claim_for_stream(key: str) -> int:
    """The claim a progress stream for *key* may run (its token), or
    ConflictError: none claimed for *key* (POST first), or another stream
    already runs it."""
    with _install_lock:
        _release_stale_claim_locked()
        claim = _claim
        if claim is None or claim.key != key:
            raise ConflictError(
                "No install in progress; POST /install/<key> first", hint="post_first"
            )
        if claim.stream_taken:
            # Another tab (or a retried connection) already runs this
            # install's pip; a second would run alongside it.
            raise ConflictError("This install is already running", hint="already_streaming")
        return claim.token


def take_stream(token: int) -> str:
    """A stream starts for claim *token*: "ok" (it runs pip now), "taken"
    (another stream started first), or "gone" (released as stale)."""
    with _install_lock:
        claim = _claim
        if claim is None or claim.token != token:
            return "gone"
        if claim.stream_taken:
            return "taken"
        claim.stream_taken = True
        claim.claimed_at = None  # the stream owns it now: it always releases it
        return "ok"


def finish_claim(token: int, settle: Callable[[], None]) -> None:
    """The stream for claim *token* is over: *settle* (the hold) and release
    the claim, together under the lock — no request sees the claim gone
    but the hold not yet settled."""
    with _install_lock:
        try:
            settle()
        finally:
            _clear_claim_locked(token)


def _pip_available() -> bool:
    """Return True if pip is callable from the current Python."""
    if getattr(sys, "frozen", False):
        return False  # frozen desktop app: sys.executable is the server, not Python
    return shutil.which(sys.executable) is not None


def install_refusal(*, left_running: bool | None = None) -> ConflictError | None:
    """Why an install would be refused right now (None: it can start) —
    the same checks POST /install makes, for the page to ask first.
    *left_running*: pip_left_running(), when the caller has it already."""
    left = _left_running_refusal(left_running)
    if left is not None:
        return left
    with _install_lock:
        _release_stale_claim_locked()  # a closed tab's claim doesn't count
        return _refusal_locked()


def _left_running_refusal(left_running: bool | None = None) -> ConflictError | None:
    # Asked outside the lock: it may list processes (slow).
    if left_running if left_running is not None else pip_process.pip_left_running():
        return ConflictError(
            "An install from before the app last stopped is still running — "
            "wait a few minutes, then restart Best Photo Picker",
            reason="busy",
        )
    return None


def _refusal_locked() -> ConflictError | None:
    """Call with _install_lock held: work running, another install, or one
    waiting for a restart."""
    if _claim is not None:
        return ConflictError("Install already in progress", reason="busy")
    if held_keys():
        # One install per restart: what the last one wrote isn't loaded
        # yet, so the next waits until it is.
        return ConflictError(
            "Restart Best Photo Picker to finish the last install first",
            reason="restart_pending",
        )
    if _long_work:
        return ConflictError(f"Wait for {_long_work[-1]} to finish, then install", reason="busy")
    busy = _busy_workers()
    if busy:
        return ConflictError(
            f"Wait for {' and '.join(busy)} to finish (or stop it), then install",
            reason="busy",
        )
    return None


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
    global _claim, _last_token
    specs = _INSTALLABLE_PACKAGES.get(key)
    if not specs:
        raise ValidationError(f"Unknown package: {key}", key=key)
    if not _pip_available():
        raise ValidationError(
            "Cannot install: pip not available",
            reason="pip_missing",
        )
    left = _left_running_refusal()
    if left is not None:
        raise left
    # One lock covers "is work running?" + "claim the install" here and
    # "is an install running?" + "start the work" in _worker_start_guard()
    # (every background worker's start), so the two never start together.
    with _install_lock:
        _release_stale_claim_locked()
        refusal = _refusal_locked()
        if refusal is not None:
            raise refusal
        _last_token += 1
        _claim = _Claim(key=key, token=_last_token, claimed_at=time.monotonic())
    return jsonify({"status": "started", "packages": specs}), 202


def _register_start_guard() -> None:
    from bpp.web.base_worker import set_start_guard

    set_start_guard(_worker_start_guard)


def _raise_if_claimed_locked() -> None:
    """Call with _install_lock held: refuse while this server installs."""
    _release_stale_claim_locked()
    if _claim is not None:
        raise ConflictError(
            "A package is being installed — wait for it to finish", blocker="install"
        )


def _raise_if_pip_left_running() -> None:
    left = _left_running_refusal()
    if left is not None:
        raise left


@contextlib.contextmanager
def _worker_start_guard(worker: Any) -> Iterator[None]:
    """Background work that loads installable packages doesn't start during
    any install, nor after one until the restart: pip swaps files — shared
    libraries too (numpy, Pillow…), which the processes this work starts
    would load fresh from disk, unlike the app — and the installed feature's
    models would silently be missing from the results. Work says whether
    it loads any of that (``loads_installable_packages``; None: unknown, so
    it does)."""
    if getattr(worker, "loads_installable_packages", None) is False:
        yield  # loads nothing an install writes
        return
    # A pip an earlier server left running still writes packages too.
    # (Asked outside the lock: it may list processes.)
    _raise_if_pip_left_running()
    with _install_lock:
        # Both under the lock: an install settles its hold before it
        # releases its claim, so no start sees neither.
        _raise_if_claimed_locked()
        if held_keys():
            raise ConflictError(
                "Restart Best Photo Picker to finish installing, then try again",
                reason="restart_pending",
            )
        yield  # held while it starts: no install can begin in between


@contextlib.contextmanager
def no_install_during(what: str) -> Iterator[None]:
    """Like the worker start guard, for long work (a library switch):
    instead of holding the lock throughout — blocking install requests,
    or deadlocking on a nested check — it's marked busy, so an install
    can't start until it ends."""
    _raise_if_pip_left_running()
    with _install_lock:
        _raise_if_claimed_locked()
        _long_work.append(what)
    try:
        yield
    finally:
        with _install_lock:
            _long_work.remove(what)


@bp.get("/api/v1/install/state")
def api_install_state() -> tuple[Response, int]:
    """Polled by every page (paired devices too, so no owner-only gate: a
    403 makes the page reload) so a pending restart shows even when the
    install finished out of view. Package names are for the owner only."""
    return jsonify(install_state(owner=principal_is_local_app())), 200


@bp.get("/api/v1/install/<key>/info")
@requires_local_app
def api_install_info(key: str) -> tuple[Response, int]:
    """Return the pip specs that the install endpoint will run for *key*.
    Powers the consent dialog so the user sees exactly which packages
    are about to be installed before clicking OK. Owner only, like the
    package names in ``/install/state``."""
    specs = _INSTALLABLE_PACKAGES.get(key)
    if specs is None:
        raise ValidationError(f"Unknown package: {key}", key=key)
    return jsonify({"key": key, "packages": specs, "host": "pypi.org"}), 200


def _api_install_progress(key: str) -> Response:
    from bpp.web.install_stream import install_progress

    return install_progress(key)


_register_start_guard()
