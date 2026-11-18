"""Putting an unpacked archive in place.

An archive is unpacked whole into a new, empty folder of its own
(``extracted/.unpacking-…``), then moved into the archive's folder by
name. Nothing the archive names is ever written into a file that is
already there (a hard link of an exported copy, a link), whatever its
names turn into on this system.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import stat

from bpp.db.analysis_leftovers import EXTRACTED_DIR
from bpp.utils.logging import get_logger

log = get_logger(__name__)

#: Name prefix of the folder an archive is unpacked into before it is moved into place.
UNPACKING = ".unpacking-"


def sweep_unfinished_unpacking(workdir: str, *, older_than_s: float = 0) -> int:
    """Remove the unpacking folders a run that ended early (a quit, a
    crash) left in ``workdir/extracted``: whole archives' worth of files
    nothing shows. Run when the library opens (only folders untouched
    for *older_than_s*: an analysis may just be starting) and before
    each unpacking. Returns how many went."""
    import time

    base = os.path.join(workdir, EXTRACTED_DIR)
    removed = 0
    with contextlib.suppress(OSError), os.scandir(base) as entries:
        for entry in list(entries):
            if not entry.name.startswith(UNPACKING) or not entry.is_dir(follow_symlinks=False):
                continue
            with contextlib.suppress(OSError):
                if time.time() - entry.stat(follow_symlinks=False).st_mtime < older_than_s:
                    continue
            shutil.rmtree(entry.path, ignore_errors=True)
            removed += 1
    if removed:
        log.info("Removed %d unfinished archive unpacking(s) from %s", removed, base)
    return removed


def first_clash(src: str, dst: str) -> str | None:
    """The first name (relative to *dst*) that is a folder on one side
    and a file on the other, or None. Checked before anything is moved,
    so a clash leaves the library as it was."""
    for parent, folders, files in os.walk(src):
        rel = os.path.relpath(parent, src)
        for name, is_folder in [*((f, True) for f in folders), *((f, False) for f in files)]:
            target = os.path.normpath(os.path.join(dst, rel, name))
            try:
                st = os.lstat(target)
            except OSError:
                continue
            if not stat.S_ISLNK(st.st_mode) and stat.S_ISDIR(st.st_mode) != is_folder:
                return os.path.normpath(os.path.join(rel, name))
    return None


def move_into(src: str, dst: str) -> None:
    """Move what was unpacked in *src* into the archive's folder *dst*.

    Everything is moved by name (``os.replace``) and nothing is written
    into an existing file: a name already in *dst* (a hard link an export
    made, a link) is replaced as a name, and any other name of that file
    keeps its bytes. A folder that is not there yet is moved whole, with
    the times the archive gave it."""
    for entry in sorted(os.scandir(src), key=lambda e: e.name):
        target = os.path.join(dst, entry.name)
        try:
            st = os.lstat(target)
        except FileNotFoundError:
            os.replace(entry.path, target)
            continue
        if stat.S_ISLNK(st.st_mode):
            os.unlink(target)
            os.replace(entry.path, target)
        elif entry.is_dir(follow_symlinks=False):
            if not stat.S_ISDIR(st.st_mode):
                raise NotADirectoryError(
                    errno.ENOTDIR, "the archive has a folder where the library has a file", target
                )
            move_into(entry.path, target)
        elif stat.S_ISDIR(st.st_mode):
            raise IsADirectoryError(
                errno.EISDIR, "the archive has a file where the library has a folder", target
            )
        else:
            os.replace(entry.path, target)
