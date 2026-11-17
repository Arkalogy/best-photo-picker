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
        home = os.path.realpath(os.path.expanduser("~"))
        # A service account or a container uid without a passwd entry
        # can have HOME=/ — trusting it would allow every path.
        if home != os.path.realpath(os.sep):
            out.append(home)
    return out


class FolderSet:
    """Folders a file may be read from, for checking many files against
    the same few folders (an export run).

    A file is inside when the folder it really sits in (links resolved)
    is one of the roots or below one. Folders are compared by what they
    are on disk, not only by spelling, so a root typed in another case
    or Unicode form on a volume that ignores those still matches. Roots
    are resolved once and each source folder is decided once.
    """

    def __init__(
        self, roots: list[str] | tuple[str, ...], followed: list[str] | tuple[str, ...] = ()
    ) -> None:
        """*followed* are folders of the owner's own that were scanned
        with links followed: a file reached through a link inside one
        was imported from there on the owner's say, so it is inside by
        its path as written. (Never the library's own folders: links in
        those are not the owner's.)"""
        self._followed = [os.path.abspath(f) for f in followed if f]
        self._paths: list[str] = []
        self._ids: set[tuple[int, int]] = set()
        self._decided: dict[str, bool] = {}
        for root in roots:
            if not root:
                continue
            try:
                real = os.path.realpath(root)
                st = os.stat(real)
            except (OSError, ValueError):
                continue  # a folder that isn't there holds nothing
            self._paths.append(real)
            if st.st_ino:
                self._ids.add((st.st_dev, st.st_ino))

    def holds(self, path: str) -> bool:
        """True iff ``path`` resolves to a file in or below a root. A path
        that can't be resolved (a NUL byte, a link loop) is not."""
        try:
            folder = os.path.dirname(os.path.realpath(path))
            written = os.path.dirname(os.path.abspath(path))
        except (OSError, ValueError):
            return False
        # As written means as written: a ".." after a link names another
        # folder than the spelling suggests, so such a path is never
        # accepted this way.
        stepped_up = os.pardir in str(path).replace("\\", "/").split("/")
        if not stepped_up and any(
            written == f or written.startswith(f.rstrip(os.sep) + os.sep) for f in self._followed
        ):
            return True
        decided = self._decided.get(folder)
        if decided is None:
            decided = self._decided[folder] = self._inside(folder)
        return decided

    def _inside(self, folder: str) -> bool:
        if is_path_under_any(folder, self._paths):
            return True
        while self._ids:
            try:
                st = os.stat(folder)
                if st.st_ino and (st.st_dev, st.st_ino) in self._ids:
                    return True
            except OSError:
                pass  # a missing level: keep climbing
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
        return False


def split_existing_folders(values: list[str]) -> tuple[list[str], list[str]]:
    """Folders named on a command line, as ``(existing, missing)``: each
    with ``~`` expanded and made absolute."""
    existing: list[str] = []
    missing: list[str] = []
    for value in values:
        folder = os.path.abspath(os.path.expanduser(value))
        (existing if os.path.isdir(folder) else missing).append(folder)
    return existing, missing
