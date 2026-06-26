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

Edge case: PID reuse. If the server crashes and the OS reassigns
that PID to an unrelated process (IDE, Chrome) within the recovery
window, the kill-probe will say "alive" and refuse. The `--force`
flag on restore-backup is the escape hatch for this rare case.
"""

from __future__ import annotations

import os

from bpp.errors import BppError
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_LOCK_FILENAME = ".serving.lock"


class ServingLockError(BppError):
    """Raised when the serving lock cannot be acquired or verified
    due to a filesystem error (read-only FS, permission denied,
    etc.). distinguishes "lock unavailable due to I/O error"
    from "lock acquired" (None) and "lock held by live PID" (int) so
    a startup that can't honour the exclusive-server guarantee
    refuses to start instead of fail-opening into the same
    silent-corruption path the lock was supposed to prevent.

    P7: inherits :class:`BppError` so ``except BppError`` catches it.
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


def acquire_lock(workdir: str) -> int | None:
    """Try to atomically acquire the serving lock.

    Returns:
      - None on success (lock acquired, our PID written).
      - Existing live PID if another server is already running and
        holds the lock — caller should refuse to start.

    Raises:
      - ServingLockError if the workdir doesn't exist OR the lock
        cannot be acquired/verified due to a filesystem error
        (read-only FS, permission denied, etc.). returning
        None for I/O failures was a fail-open bug — startup would
        proceed without a lock, defeating the exclusive-server
        guarantee. Callers must catch ServingLockError and refuse
        to start.

    Uses `os.open(O_CREAT | O_EXCL | O_WRONLY)` for the create path:
    exactly one process can win the race. Losers either (a) find a
    living PID and bail, or (b) find a stale lockfile and atomically
    rotate it via os.replace(tmp, path).

    Stale recovery sequence:
      1. O_EXCL fails with FileExistsError.
      2. Read the existing PID and probe with kill(pid, 0).
      3. If alive → return that PID (caller refuses to start).
      4. If stale → write a tmp file with our PID, os.replace into
         place. Atomic on POSIX/Windows; if a second racing process
         already replaced first, our second probe loop iteration
         will see ITS pid and back off.

    Retries the read/probe loop a small number of times to handle
    the rare case where the lock is repeatedly stale and racing
    recovery attempts conflict.

    Replaces the older `write_lock`-only path which was not
    exclusive — two concurrent `bpp serve` could both write their
    PID and step on each other's DB.
    """
    if not workdir:
        raise ServingLockError("Cannot acquire serving lock: empty workdir")
    if not os.path.isdir(workdir):
        raise ServingLockError(f"Cannot acquire serving lock: workdir does not exist: {workdir!r}")
    path = lock_path(workdir)
    pid = os.getpid()
    pid_bytes = str(pid).encode("utf-8")

    last_error: OSError | None = None
    for _ in range(5):
        # First attempt: try to create exclusively. The fast path
        # succeeds with no contention.
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            # Lockfile already exists. Decide stale vs live.
            existing = read_lock_pid_if_alive(workdir)
            if existing is not None and existing != pid:
                return existing  # Live owner; caller refuses to start.
            # Stale (or unreadable). Try to atomically replace via
            # tmp + rename so a concurrent acquirer can't end up
            # with a corrupt lockfile.
            tmp = path + f".tmp.{pid}"
            try:
                with open(tmp, "wb") as f:
                    f.write(pid_bytes)
                os.replace(tmp, path)
            except OSError as e:
                log.warning("Stale-lock recovery failed at %s: %s", path, e)
                last_error = e
                with __import__("contextlib").suppress(OSError):
                    os.remove(tmp)
                continue  # Try again

            # D-03: os.replace is atomic but cannot fail if a third
            # process raced us — both contenders' replaces succeed,
            # last writer wins. Verify the lockfile now contains
            # OUR pid before returning success. If a different pid
            # is there, the other process won the race; report it
            # as the holder so we refuse to start.
            try:
                with open(path, "rb") as f:
                    actual = f.read().strip()
            except OSError as e:
                log.warning("Could not verify lock contents after replace: %s", e)
                last_error = e
                continue
            if actual == pid_bytes:
                return None  # We won.
            try:
                winner_pid = int(actual)
            except ValueError:
                # Garbage content somehow — treat as another loop iteration
                # rather than fabricating a holder PID.
                continue
            if winner_pid == pid:
                return None  # Belt-and-suspenders: same as match above.
            log.info(
                "Lost stale-lock recovery race at %s: holder is now PID %d",
                path,
                winner_pid,
            )
            return winner_pid
        except OSError as e:
            # Other OS error (read-only fs, etc.) — fail closed.
            # was return None (silent fail-open). A startup
            # that can't acquire the lock must NOT proceed as if
            # it had — that's the silent two-server-corrupting-DB
            # scenario the lock exists to prevent.
            log.error("Could not acquire serving lock at %s: %s", path, e)
            raise ServingLockError(f"OS error acquiring lock: {e}") from e
        else:
            # Won O_EXCL. Write our PID and close.
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(pid_bytes)
                return None
            except OSError as e:
                log.error("Could not write PID after O_EXCL at %s: %s", path, e)
                with __import__("contextlib").suppress(OSError):
                    os.remove(path)
                raise ServingLockError(f"OS error writing PID: {e}") from e

    # All retries exhausted. If the lockfile holds a live PID, report
    # that as the holder. Otherwise the retries were all I/O failures
    # — raise so the caller refuses to start.
    if last_error is not None:
        raise ServingLockError(
            f"Could not recover stale lock after 5 retries: {last_error}"
        ) from last_error
    holder = read_lock_pid_if_alive(workdir)
    if holder is not None:
        return holder
    raise ServingLockError(f"Could not acquire serving lock at {path!r}: retries exhausted")


def clear_lock(workdir: str) -> None:
    """Remove the lockfile. Safe to call when no lock exists.

    Called from an atexit handler on clean shutdown. SIGKILL'd
    processes won't reach this — that's what the kill-probe in
    `read_lock_pid_if_alive` is for.
    """
    if not workdir:
        return
    path = lock_path(workdir)
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

    The "is the PID alive?" probe is `os.kill(pid, 0)` — signal 0
    is a no-op (no signal actually delivered) but the syscall still
    raises `ProcessLookupError` if the PID is gone. Standard Unix
    pattern; works on macOS and Linux. On Windows the semantics
    differ slightly but the same call shape works for the common case.

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

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        # Different user owns it; the PID exists but we can't signal.
        # Treat as alive (conservative) — the user shouldn't restore
        # under another user's running server either.
        return pid
    except OSError as e:
        # Unknown errno; fail open (treat as no-lock) rather than
        # blocking recovery indefinitely.
        log.warning("Unexpected error probing PID %d: %s", pid, e)
        return None
    return pid
