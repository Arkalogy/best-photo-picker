"""Putting an unpacked archive in place.

An archive is unpacked whole into a new, empty folder of the app's own
(``<workdir>/.unpacking/run-…``), then moved into the archive's folder
by name. Nothing the archive names is ever written into a file that is
already there (a hard link of an exported copy, a link), whatever its
names turn into on this system. The folder is beside ``extracted/``,
never in it: archives unpacked by earlier versions went straight into
``extracted/`` and can have any name there.

An unpacking folder is held locked while it is written to. One that no
one holds was left by a run that ended early (a quit, a crash): it is
set aside and removed. Its dates say nothing (an archive can give its
folder any date). The lock tells another process, or a library opened
again in this one, to leave it alone. Where the system can't lock files,
the library's serving lock (one app per library) and this process's
own list of the folders it writes do.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import stat
import tempfile
import threading
from collections.abc import Iterator

from bpp.utils.file_lock import release, same_file
from bpp.utils.file_lock import try_lock as _try_lock
from bpp.utils.logging import get_logger

log = get_logger(__name__)

#: The app's own folder (in the workdir) that archives are unpacked in.
STAGING_DIR = ".unpacking"
#: In it: the folder of one unpacking...
RUN = "run-"
#: ...and one set aside, to be removed.
DISCARDED = "discarded-"
#: In an unpacking folder: the file held locked while it is in use...
IN_USE = "in-use.lock"
#: ...and the folder the archive's files go into, beside it.
CONTENT = "content"

#: Unpackings this process is writing, by the folder itself (any
#: spelling of its path: case, links), which guards them where the system
#: can't lock files; and the lock that orders taking one against a sweep.
_ours: set[tuple[int, int]] = set()
_ours_lock = threading.Lock()
_removers: list[threading.Thread] = []


def _hold(folder: str) -> int | None:
    """*folder*'s in-use file, opened and locked; None when someone else
    holds it, it was moved meanwhile (by a sweep elsewhere), or it is not
    a file this app made (left alone)."""
    lock = os.path.join(folder, IN_USE)
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return None
    if _try_lock(fd) is not False and same_file(fd, lock):
        return fd
    release(fd)
    return None


def _identity(folder: str) -> tuple[int, int]:
    st = os.stat(folder)
    return (st.st_dev, st.st_ino)


def _staging(workdir: str, *, make: bool = False) -> str | None:
    """``<workdir>/.unpacking`` as a real folder, never through a link (a
    library folder may have been prepared by someone else): made when
    *make*, else None when it is not there."""
    path = os.path.join(workdir, STAGING_DIR)
    if os.path.islink(path):
        if not make:
            return None
        log.warning("Removed a link at %s: archives are unpacked into a real folder", path)
        os.unlink(path)
    if make:
        os.makedirs(path, exist_ok=True)
    return path if os.path.isdir(path) else None


@contextlib.contextmanager
def unpacking_folder(workdir: str) -> Iterator[str]:
    """A new, empty folder to unpack an archive into, held as in use
    until the block ends; then it is removed."""
    base = _staging(workdir, make=True)
    with _ours_lock:
        for _ in range(5):
            folder = tempfile.mkdtemp(prefix=RUN, dir=base)
            fd = _hold(folder)
            if fd is not None:
                _ours.add(_identity(folder))
                break
        else:
            raise OSError(errno.EBUSY, "could not make a folder to unpack the archive into", base)
    identity = _identity(folder)
    try:
        content = os.path.join(folder, CONTENT)
        os.mkdir(content)
        yield content
    finally:
        with _ours_lock:
            _ours.discard(identity)
        release(fd)
        shutil.rmtree(folder, ignore_errors=True)


def _entries(workdir: str, prefix: str) -> list[os.DirEntry]:
    base = _staging(workdir)
    if base is None:
        return []
    try:
        with os.scandir(base) as entries:
            return [
                e for e in entries if e.name.startswith(prefix) and e.is_dir(follow_symlinks=False)
            ]
    except OSError:
        return []


def set_aside_unfinished_unpacking(workdir: str) -> int:
    """Rename every unpacking folder no one holds out of the way (its
    removal, a whole archive's worth of files, is left for later)."""
    moved = 0
    with _ours_lock:
        for entry in _entries(workdir, RUN):
            try:
                if _identity(entry.path) in _ours:
                    continue
            except OSError:
                continue  # gone meanwhile
            fd = _hold(entry.path)
            if fd is None:
                continue  # in use
            aside = os.path.join(os.path.dirname(entry.path), DISCARDED + entry.name[len(RUN) :])
            try:
                if os.name == "nt":
                    # Windows won't move a folder with a file open in it:
                    # let go first (one that is taken now stays put).
                    release(fd)
                    fd = -1
                os.replace(entry.path, aside)
                moved += 1
            except OSError:
                log.warning("Could not set aside %s", entry.path, exc_info=True)
            finally:
                if fd >= 0:
                    release(fd)
    if moved:
        log.info("Set aside %d unfinished archive unpacking(s) in %s", moved, workdir)
    return moved


def remove_discarded(workdir: str) -> int:
    """Remove what :func:`set_aside_unfinished_unpacking` set aside."""
    removed = 0
    for entry in _entries(workdir, DISCARDED):
        shutil.rmtree(entry.path, ignore_errors=True)
        removed += 1
    if removed:
        log.info("Removed %d unfinished archive unpacking(s) from %s", removed, workdir)
    return removed


def sweep_unfinished_unpacking(workdir: str) -> int:
    """Before an unpacking, and when the library is cleared: set aside
    and remove every unpacking no one holds."""
    set_aside_unfinished_unpacking(workdir)
    return remove_discarded(workdir)


def set_aside_and_remove_in_background(workdir: str) -> None:
    """When the library opens: set aside at once, remove in the background."""
    set_aside_unfinished_unpacking(workdir)
    if _entries(workdir, DISCARDED):
        remover = threading.Thread(
            target=remove_discarded, args=(workdir,), name="remove-unpacking-leftovers", daemon=True
        )
        _removers.append(remover)
        remover.start()


def wait_for_removals(timeout: float = 60.0) -> None:
    """Wait for background removals started so far (tests)."""
    for remover in list(_removers):
        remover.join(timeout)


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
            staged = entry.stat(follow_symlinks=False)
            move_into(entry.path, target)
            # Moving its files in changed the folder's date: give it back
            # the one the archive gave it.
            with contextlib.suppress(OSError):
                os.utime(target, ns=(staged.st_atime_ns, staged.st_mtime_ns))
        elif stat.S_ISDIR(st.st_mode):
            raise IsADirectoryError(
                errno.EISDIR, "the archive has a file where the library has a folder", target
            )
        else:
            os.replace(entry.path, target)
