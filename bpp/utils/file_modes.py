"""Permission bits for files the app creates from someone else's file."""

from __future__ import annotations

import stat


def copy_mode(source_mode: int) -> int:
    """The permission bits a copy of a file gets: the source's, without
    set-user-ID / set-group-ID / sticky and without write for group or
    others. The copy belongs to the owner; whoever made the source does
    not get to make it a program that runs as them, or a file anyone
    can rewrite."""
    return stat.S_IMODE(source_mode) & 0o755
