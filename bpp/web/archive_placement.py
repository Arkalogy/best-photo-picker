"""Putting an unpacked archive in place.

An archive is unpacked whole into a new, empty folder of its own
(``extracted/.unpacking-…``), then moved into the archive's folder by
name. Nothing the archive names is ever written into a file that is
already there (a hard link of an exported copy, a link), whatever its
names turn into on this system.

An unpacking folder is held locked while it is written to. One that no
one holds was left by a run that ended early (a quit, a crash): it is
set aside and removed. Its dates say nothing (an archive can give its
folder any date), and the lock is what tells another process sharing
the library, or a library opened again in this one, to leave it alone.
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

from bpp.db.analysis_leftovers import EXTRACTED_DIR
from bpp.utils.logging import get_logger

log = get_logger(__name__)

#: Name prefix of the folder an archive is unpacked into before it is moved into place.
UNPACKING = ".unpacking-"
#: ...and of one set aside, to be removed.
DISCARDED = ".discarded-"
#: In an unpacking folder: the file held locked while it is in use...
IN_USE = "in-use.lock"
#: ...and the folder the archive's files go into, beside it.
CONTENT = "content"

#: Unpackings this process is writing (guards them where the system
#: can't lock files), and the lock that orders taking one against a sweep.
_ours: set[str] = set()
_ours_lock = threading.Lock()
_removers: list[threading.Thread] = []


def _try_lock(fd: int) -> bool | None:
    """Lock the open file *fd*: True when locked, False when another
    holder has it, None when this system can't lock it."""
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES, errno.EDEADLK):
            return False
        return None
    return True


def _hold(folder: str) -> int | None:
    """*folder*'s in-use file, opened and locked; None when someone else
    holds it or it was moved meanwhile (by a sweep elsewhere)."""
    lock = os.path.join(folder, IN_USE)
    try:
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError:
        return None
    try:
        if _try_lock(fd) is not False and os.path.samestat(os.fstat(fd), os.stat(lock)):
            return fd
    except OSError:
        pass
    os.close(fd)
    return None


@contextlib.contextmanager
def unpacking_folder(base: str) -> Iterator[str]:
    """A new, empty folder in *base* to unpack an archive into, held as
    in use until the block ends; then it is removed."""
    with _ours_lock:
        for _ in range(5):
            folder = tempfile.mkdtemp(prefix=UNPACKING, dir=base)
            fd = _hold(folder)
            if fd is not None:
                _ours.add(os.path.abspath(folder))
                break
        else:
            raise OSError(errno.EBUSY, "could not make a folder to unpack the archive into", base)
    try:
        content = os.path.join(folder, CONTENT)
        os.mkdir(content)
        yield content
    finally:
        with _ours_lock:
            _ours.discard(os.path.abspath(folder))
        os.close(fd)
        shutil.rmtree(folder, ignore_errors=True)


def _extracted_entries(workdir: str, prefix: str) -> list[os.DirEntry]:
    base = os.path.join(workdir, EXTRACTED_DIR)
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
        for entry in _extracted_entries(workdir, UNPACKING):
            if os.path.abspath(entry.path) in _ours:
                continue
            fd = _hold(entry.path)
            if fd is None:
                continue  # in use
            aside = os.path.join(
                os.path.dirname(entry.path), DISCARDED + entry.name[len(UNPACKING) :]
            )
            try:
                if os.name == "nt":
                    # Windows won't move a folder with a file open in it:
                    # let go first (one that is taken now stays put).
                    os.close(fd)
                    fd = -1
                os.replace(entry.path, aside)
                moved += 1
            except OSError:
                log.warning("Could not set aside %s", entry.path, exc_info=True)
            finally:
                if fd >= 0:
                    os.close(fd)
    if moved:
        log.info("Set aside %d unfinished archive unpacking(s) in %s", moved, workdir)
    return moved


def remove_discarded(workdir: str) -> int:
    """Remove what :func:`set_aside_unfinished_unpacking` set aside."""
    removed = 0
    for entry in _extracted_entries(workdir, DISCARDED):
        shutil.rmtree(entry.path, ignore_errors=True)
        removed += 1
    if removed:
        log.info("Removed %d unfinished archive unpacking(s) from %s", removed, workdir)
    return removed


def sweep_unfinished_unpacking(workdir: str) -> int:
    """Before an unpacking: set aside and remove every unpacking no one holds."""
    set_aside_unfinished_unpacking(workdir)
    return remove_discarded(workdir)


def set_aside_and_remove_in_background(workdir: str) -> None:
    """When the library opens: set aside at once, remove in the background."""
    set_aside_unfinished_unpacking(workdir)
    if _extracted_entries(workdir, DISCARDED):
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
