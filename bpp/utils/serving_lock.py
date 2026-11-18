"""PID lockfile so destructive CLI ops can detect a running server.

`bpp serve` (and the Tauri sidecar) writes its PID to
`<library>/data/.serving.lock` on startup and removes the file on
clean shutdown via an atexit hook. CLI commands that mutate the DB
out-of-band — `bpp db restore-backup` is the only one today, but
future maintenance commands will follow the same pattern — check
for the lock before doing anything destructive.

Why a PID file:
  * Standard Unix pattern (postgres, redis, sshd).
  * `os.kill(pid, 0)` reliably probes liveness without affecting
    the target process; raises ProcessLookupError if the PID is
    gone (so stale lockfiles from crashes don't permanently block
    recovery commands).
  * No platform-specific lock semantics — works the same on macOS,
    Linux, and (for the kill-probe) Windows.

Since the library lock is also taken by `bpp analyze`, the file is
also locked by the system (``flock``) for as long as its holder runs:
two processes can never hold it at once, and a holder that dies lets
go by itself. The PID inside is what the messages name, and what an
older version that only writes it is recognised by. Where the system
can't lock that file, only exclusive creation is used and a lock left
by a run that ended is never taken over on its own: the owner removes
it (the message says where).

Edge case: PID reuse. If the server crashes and the OS reassigns
that PID to an unrelated process (IDE, Chrome) within the recovery
window, the kill-probe will say "alive" and refuse. The `--force`
flag on restore-backup is the escape hatch for this rare case.
"""

from __future__ import annotations

import contextlib
import os
import threading

from bpp.errors import BppError
from bpp.utils.file_lock import pid_alive, release, same_file, try_lock
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_LOCK_FILENAME = ".serving.lock"

#: Locks this process holds, by the folder they are in (any spelling of
#: its path): the open file the system lock is on. Closing it, or the
#: process ending, lets go.
_held: dict[object, int] = {}
_held_guard = threading.Lock()
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _key(workdir: str) -> object:
    try:
        st = os.stat(workdir)
    except OSError:
        return os.path.realpath(workdir)
    if not st.st_ino:  # a volume that gives no file numbers
        return os.path.realpath(workdir)
    return (st.st_dev, st.st_ino)


class ServingLockError(BppError):
    """Raised when the serving lock cannot be acquired or verified
    due to a filesystem error (read-only FS, permission denied,
    etc.). distinguishes "lock unavailable due to I/O error"
    from "lock acquired" (None) and "lock held by live PID" (int) so
    a startup that can't honour the exclusive-server guarantee
    refuses to start instead of fail-opening into the same
    silent-corruption path the lock was supposed to prevent.

    Inherits :class:`BppError` so ``except BppError`` catches it.
    """

    http_status = 503
    code = "serving_lock_error"


def lock_path(workdir: str) -> str:
    """Resolve the lockfile path inside a library's data directory.

    Mirrors how callers compute the DB path (`<workdir>/<filename>`),
    so callers don't need to know the constant.
    """
    return os.path.join(workdir, _LOCK_FILENAME)


def write_lock(workdir: str) -> str | None:
    """Atomically write the current PID to the lockfile.

    Returns the path on success, None if the workdir doesn't exist
    or the write failed (e.g. read-only filesystem). Best-effort —
    a failure here downgrades the safety guarantee to "user remembers
    to stop the server first" but doesn't prevent the server from
    starting.

    NOTE: this does NOT acquire an exclusive lock — it just writes
    a PID. Two concurrent `bpp serve` invocations will both succeed
    here (last writer wins). Use `acquire_lock()` for exclusive
    acquisition; `write_lock` is preserved for callers that already
    know they hold the lock and just need to refresh the PID.
    """
    if not workdir or not os.path.isdir(workdir):
        return None
    path = lock_path(workdir)
    tmp = path + ".tmp"
    try:
        # Write through a tmp file + rename so a partial write can't
        # leave an empty/garbage lockfile that confuses the probe.
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        os.replace(tmp, path)
        return path
    except OSError as e:
        log.warning("Could not write serving lock at %s: %s", path, e)
        with __import__("contextlib").suppress(OSError):
            os.remove(tmp)
        return None


def _pid_in(fd: int) -> int | None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        pid = int(os.read(fd, 32).strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def acquire_lock(workdir: str) -> int | None:
    """Try to take the library lock in *workdir*.

    Returns None when this process now holds it (or already did), else
    the PID of the holder (0 when it can't be told). Raises
    ServingLockError when the workdir doesn't exist or the lock can't
    be taken for a filesystem reason (read-only, permission denied):
    the caller must refuse to start rather than run without it.

    The lock file is locked by the system for as long as this process
    holds it, so exactly one process can hold it, however their starts
    interleave. A file that names a live process without being locked
    is honoured too: an older version holds its lock that way."""
    if not workdir:
        raise ServingLockError("Cannot acquire serving lock: empty workdir")
    if not os.path.isdir(workdir):
        raise ServingLockError(f"Cannot acquire serving lock: workdir does not exist: {workdir!r}")
    path = lock_path(workdir)
    me = os.getpid()
    key = _key(workdir)
    with _held_guard:
        mine = _held.get(key)
        if mine is not None:
            if same_file(mine, path):
                return None
            del _held[key]  # its folder was replaced, or its file removed
            release(mine)
        for _ in range(5):
            created = True
            try:
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o600)
            except FileExistsError:
                created = False
                try:
                    fd = os.open(path, os.O_RDWR | _NOFOLLOW)
                except FileNotFoundError:
                    continue  # its holder removed it as we looked: again
                except OSError as e:
                    log.error("Could not open serving lock at %s: %s", path, e)
                    raise ServingLockError(f"OS error acquiring lock: {e}") from e
            except OSError as e:
                log.error("Could not acquire serving lock at %s: %s", path, e)
                raise ServingLockError(f"OS error acquiring lock: {e}") from e
            locked = try_lock(fd)
            if locked is False:
                # Someone else holds it, whatever PID it names (in
                # another container the holder can have this PID too).
                holder = _pid_in(fd)
                release(fd)
                return holder or 0
            if not same_file(fd, path):
                release(fd)  # its holder removed it as we opened it: again
                continue
            if locked is None:
                # This system can't lock the file: whoever created it
                # holds it. One left by a run that ended is not taken
                # over (two processes taking over the same one could both
                # believe they won): the caller's message says to remove it.
                if not created:
                    release(fd)
                    return read_lock_pid_if_alive(workdir) or 0
            else:
                named = _pid_in(fd)
                if named is not None and named != me and pid_alive(named):
                    release(fd)
                    return named  # an older version's lock (or a reused PID)
            _write_pid(fd, path, me, created)
            _held[key] = fd
            return None
    raise ServingLockError(f"Could not acquire serving lock at {path!r}: retries exhausted")


def _write_pid(fd: int, path: str, pid: int, created: bool) -> None:
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(pid).encode("utf-8"))
        os.fsync(fd)
    except OSError as e:
        # A file this call made and could not fill is not left behind:
        # where files can't be locked it would keep everyone out.
        if created and same_file(fd, path):
            with contextlib.suppress(OSError):
                os.remove(path)
        release(fd)
        log.error("Could not write PID to serving lock at %s: %s", path, e)
        raise ServingLockError(f"OS error writing PID: {e}") from e


def lock_advice(workdir: str, holder: int) -> str:
    """What to tell the owner when *holder* (from :func:`acquire_lock`)
    keeps them out of the library in *workdir*."""
    path = lock_path(workdir)
    who = f"process {holder}" if holder > 0 else "another process"
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW)
    except OSError:
        fd = None
    if fd is not None:
        busy = try_lock(fd) is False
        release(fd)
        if busy:
            return (
                f"another bpp app or `bpp analyze` run ({who}) is using it: "
                "wait for it to finish, or close it"
            )
    return (
        f"its lock names {who}, which may be an older bpp app still running. If no "
        f"bpp app or analysis is running, remove {path} and try again"
    )


def clear_library_locks(*workdirs: str | None) -> None:
    """:func:`clear_lock` for each of a library's lock folders."""
    for workdir in dict.fromkeys(w for w in workdirs if w):
        clear_lock(workdir)


def clear_lock(workdir: str) -> None:
    """Remove this process's lockfile. Safe to call when no lock exists.

    Only a lock that names this process is removed: after a library
    switch (or a refused switch) the lock at that path may belong to
    another server, and the atexit handler registered for an earlier
    library must not take it away from that server.

    SIGKILL'd processes won't reach this — that's what the kill-probe
    in `read_lock_pid_if_alive` is for.
    """
    if not workdir:
        return
    path = lock_path(workdir)
    with _held_guard:
        fd = _held.pop(_key(workdir), None)
    if fd is not None:
        if same_file(fd, path):
            if os.name == "nt":
                # Windows won't remove a file open here: let go first.
                release(fd)
                fd = None
            try:
                # Removed while still locked (elsewhere): one that opens
                # the old file meanwhile sees it is no longer at this path.
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as e:
                log.warning("Could not remove serving lock at %s: %s", path, e)
        if fd is not None:
            release(fd)
        return
    # Not held through this process's lock: removed only when it names
    # this process (or nothing) and no one holds it now. A process that
    # is starting has it locked before it writes its PID.
    try:
        probe = os.open(path, os.O_RDWR | _NOFOLLOW)
    except FileNotFoundError:
        return
    except OSError as e:
        log.warning("Could not read serving lock at %s: %s", path, e)
        return
    holder = _pid_in(probe)
    in_use = try_lock(probe) is False
    release(probe)
    if in_use or (holder is not None and holder != os.getpid()):
        log.info("Serving lock at %s belongs to another process; left in place", path)
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("Could not remove serving lock at %s: %s", path, e)


def read_lock_pid_if_alive(workdir: str) -> int | None:
    """Return the PID inside the lockfile if that process is alive.

    Returns None when:
      * The lockfile doesn't exist.
      * The lockfile is empty / unparseable (treat as stale).
      * The PID inside the file no longer exists (stale lock from
        a crash).

    Whether the PID is alive is asked without signalling it
    (:func:`bpp.utils.file_lock.pid_alive`).

    Stale lockfiles (file present, PID gone) are NOT auto-removed
    here — the caller decides whether to clear or keep. Tests need
    that flexibility.
    """
    if not workdir:
        return None
    path = lock_path(workdir)
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("Could not read serving lock at %s: %s", path, e)
        return None

    try:
        pid = int(raw)
    except ValueError:
        # Empty or garbage content — treat as stale. Don't remove
        # automatically; the caller decides recovery.
        return None
    if pid <= 0:
        return None

    # Another user's process counts as running (the user shouldn't
    # restore under another user's server either).
    return pid if pid_alive(pid) else None
