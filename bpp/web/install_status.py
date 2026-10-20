"""What the page needs to know about installs: a restart pending, which
feature waits for it, interrupted installs to repair, and whether the face
install can be offered now.

Shared by GET /api/v1/install/state (polled every 30 s) and
/api/v1/status, so both say the same thing; paired devices get a
redacted copy.
"""

from __future__ import annotations

import time
from typing import Any

from bpp.scoring.install_hold import held_keys


def install_state(*, owner: bool = True) -> dict[str, Any]:
    """``restart_pending``: features installed from the app this session —
    they load after a restart.
    ``face_installable``: the face install can be offered now (owner).
    ``install_blocked``: why no install can start right now ("" if one can).
    ``interrupted``: how to repair installs pip was cut off in."""
    if not owner:  # paired device: all of it would be redacted anyway
        state: dict[str, Any] = {"restart_pending": []}
        redact_for_paired_device(state)
        return state
    # Asked on every page load and 30-second poll (and /status): what's
    # slow to find out and rarely changes is kept a moment (_slow_facts);
    # whether an install can start is asked afresh (work starts and ends any
    # moment).
    from bpp.web.bp_install import _pip_available, install_refusal
    from bpp.web.pip_process import notes_state

    installed = sorted(held_keys())
    interrupted, left_running = notes_state()  # one pass over the notes
    facts = _slow_facts("faces" in interrupted)
    refusal = install_refusal(left_running=left_running)
    can_install = _pip_available() and refusal is None  # as POST /install decides
    # (Waiting for the restart has its own banner.)
    blocked = refusal.user_message if refusal is not None and not installed else ""
    return {
        "face_license_needed": facts["face_license_needed"],
        "restart_pending": installed,
        "face_installable": not facts["faces_ok"] and "faces" not in installed and can_install,
        "install_blocked": blocked,
        "interrupted": [interrupted[key] for key in sorted(interrupted)],
        "interrupted_keys": sorted(interrupted),
    }


#: (when, what they were found for, the facts): see _slow_facts.
_facts_cache: tuple[float, object, dict[str, Any]] | None = None
_FACTS_CACHE_S = 3.0


def _slow_facts(faces_interrupted: bool) -> dict[str, Any]:
    """Whether face recognition is there (files, its license) — found again
    once an install ends or waits, or after a few seconds."""
    global _facts_cache
    from bpp.scoring.face_embed_sface_runtime import _sface_available
    from bpp.scoring.face_recognition_loader import license_accepted, looks_installed

    basis = (held_keys(), faces_interrupted)
    cached = _facts_cache
    if cached is not None and cached[1] == basis and time.monotonic() - cached[0] < _FACTS_CACHE_S:
        return cached[2]
    sface = _sface_available()
    present = looks_installed()
    facts = {
        # Say whether face recognition works without importing dlib to find
        # out. A face install stopped part way can leave face_recognition
        # findable but broken: offer it again (the repair).
        "faces_ok": (sface or present) and not faces_interrupted,
        # Installed, but its license isn't accepted: that's what to do, not
        # another install.
        "face_license_needed": not sface and present and not license_accepted(),
    }
    _facts_cache = (time.monotonic(), basis, facts)
    return facts


def quick_state() -> dict[str, list[str]]:
    """The part of the install state that is cheap to ask on every health
    poll: features waiting for a restart and installs pip was cut off in.
    No refusal check (it takes the install lock and releases stale claims)
    and no face-model probe."""
    from bpp.web.pip_process import notes_state

    interrupted, _left_running = notes_state()
    return {"restart_pending": sorted(held_keys()), "interrupted": sorted(interrupted)}


def install_status_fields(*, owner: bool) -> dict[str, Any]:
    """The install part of /api/v1/status (same source as /install/state)."""
    state = install_state(owner=owner)
    return {
        "install_restart_pending": state["restart_pending"],
        "face_installable": state["face_installable"],
        "face_license_needed": state["face_license_needed"],
        "install_interrupted_keys": state["interrupted_keys"],
        "install_interrupted": state["interrupted"],  # the repair commands
        "install_blocked": state["install_blocked"],
    }


def redact_for_paired_device(state: dict[str, Any]) -> None:
    """Paired devices can't install or restart the app (owner-only): no
    install offers (refused → the page reloads), no restart banner they
    can't act on, no package names or repair commands."""
    state["restart_pending"] = []
    state["face_installable"] = False
    state["face_license_needed"] = False
    state["install_blocked"] = ""
    state["interrupted"] = []
    state["interrupted_keys"] = []
