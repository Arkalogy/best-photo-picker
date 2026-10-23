"""File-level helpers for :mod:`bpp.registry.acceptance_log`: owner-only
file / directory modes, permission hardening, and detecting a truncated
last line before appending. Split out for the 500-LOC cap."""

from __future__ import annotations

import os
from pathlib import Path

from bpp.utils.logging import get_logger

_log = get_logger("bpp.registry.acceptance_log")


#: Owner-only file mode for the acceptance log. The log records
#: which restricted models the user accepted, their declared use
#: context, and free-text source-of-rights notes — sensitive
#: metadata that should never leak to other local users on a shared
#: machine. ``0o600`` = read/write for the owner; nothing for group
#: or world.
_LOG_FILE_MODE = 0o600

#: Owner-only directory mode for the parent ``XDG_CONFIG_HOME/bpp/``
#: directory. Prevents directory listing by other local users, which
#: would otherwise reveal that BPP is installed and that an
#: acceptance log exists.
_LOG_DIR_MODE = 0o700


def _harden_permissions(target: Path) -> None:
    """Tighten the acceptance-log file and parent directory to
    owner-only permissions. Called after every write so a pre-
    existing file from an older BPP version (created with the
    default umask) gets re-tightened on the next user action.

    POSIX-only effect: on Windows the mode bits don't map to ACL
    semantics, and ``os.chmod`` is a no-op for the read/write bits
    of interest. The privacy claim downgrades to "trust the user
    profile is appropriately ACL'd" on that platform; the call is
    safe to make regardless.
    """
    try:
        os.chmod(target, _LOG_FILE_MODE)
    except OSError as exc:
        _log.debug(
            "Could not tighten acceptance-log file mode on %s: %s",
            target,
            exc,
        )
    try:
        os.chmod(target.parent, _LOG_DIR_MODE)
    except OSError as exc:
        _log.debug(
            "Could not tighten acceptance-log dir mode on %s: %s",
            target.parent,
            exc,
        )


def _ends_mid_line(target: Path) -> bool:
    try:
        with open(target, "rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return False
            f.seek(-1, os.SEEK_END)
            return f.read(1) != b"\n"
    except OSError:
        return False
