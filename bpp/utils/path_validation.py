"""Shared path-allowlist validation.

five blueprints/modules independently implemented the same
"is this path inside one of these allowed parents?" check, each
spelling it slightly differently:

  - `os.path.realpath(p)` then string compare
  - `Path(p).resolve()` then `is_relative_to`
  - mix of `==` for the parent itself and `is_relative_to` for descendants

The variants disagree on edge cases (symlinks resolved vs not,
trailing slashes, the parent itself) and a future change has to
remember to update all five copies.

This module is the single source of truth.
"""

from __future__ import annotations

import os
from pathlib import Path


def is_path_under_any(path: str, allowed_parents: list[str] | tuple[str, ...]) -> bool:
    """Return True iff ``path`` resolves to one of ``allowed_parents``
    or to a descendant of one.

    Both ``path`` and each entry in ``allowed_parents`` are resolved
    via ``os.path.realpath`` before the comparison, so symlinks
    can't be used to escape (a symlink at ``allowed/back-door``
    pointing to ``/etc`` resolves to ``/etc`` and fails the check).

    An empty ``allowed_parents`` list returns False — fail closed.
    """
    if not allowed_parents:
        return False
    real_path = Path(os.path.realpath(path))
    for parent in allowed_parents:
        if not parent:
            continue
        real_parent = Path(os.path.realpath(parent))
        if real_path == real_parent or real_path.is_relative_to(real_parent):
            return True
    return False


def build_library_allowlist(
    library_path: str | None = None,
    workdir: str | None = None,
    *,
    include_home: bool = False,
) -> list[str]:
    """Convenience builder for the most common allow-list shape:
    library root, work directory, and (optionally) the user's home.

    Each entry is realpath-resolved here so callers can mix this
    with `is_path_under_any` (which would re-resolve, but that's
    a cheap stat). Returns a fresh list — callers can append.

    None or empty inputs are dropped silently so the caller
    doesn't have to check before passing `ctx.state.get(...)`.
    """
    out: list[str] = []
    if library_path:
        out.append(os.path.realpath(library_path))
    if workdir:
        out.append(os.path.realpath(workdir))
    if include_home:
        out.append(os.path.realpath(os.path.expanduser("~")))
    return out
