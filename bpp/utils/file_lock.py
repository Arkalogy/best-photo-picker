"""Locking an open file against other processes, on every system.

Used for the library lock (``serving_lock``) and for an archive's
unpacking folder (``archive_placement``). The system releases such a
lock when the file is closed or its process ends.
"""

from __future__ import annotations

import contextlib
import errno
import os

#: Where the lock is taken in the file on Windows: past anything a
#: holder writes there (a PID), which others must still be able to read.
_WINDOWS_LOCK_OFFSET = 1 << 30


def try_lock(fd: int) -> bool | None:
    """Lock the open file *fd* without waiting: True when locked, False
    when another holder has it, None when this system can't lock it."""
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES, errno.EDEADLK):
            return False
        return None
    return True


def release(fd: int) -> None:
    """Let go of *fd*'s lock (if any) and close it."""
    if os.name == "nt":
        import msvcrt

        with contextlib.suppress(OSError):  # not locked by this handle
            os.lseek(fd, _WINDOWS_LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    os.close(fd)


def same_file(fd: int, path: str) -> bool:
    """Is *path* (not followed if a link) the file *fd* has open?"""
    try:
        return os.path.samestat(os.fstat(fd), os.stat(path, follow_symlinks=False))
    except OSError:
        return False


def pid_alive(pid: int) -> bool:
    """Is process *pid* running? (One that can't be asked counts as
    running.) Never signals it: on Windows ``os.kill(pid, 0)`` would
    send it Ctrl+C."""
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # another user's process
    except OSError:
        return False
    return True


def _windows_pid_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    query_limited_information, still_active, access_denied = 0x1000, 259, 5
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == access_denied
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == still_active
    finally:
        kernel32.CloseHandle(handle)
