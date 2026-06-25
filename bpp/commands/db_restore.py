"""`bpp db restore-backup` — recovery path from .backup / .backup.prev.

Extracted from bpp.commands during the v0.1 cleanup. The outer
`do_db_restore_backup` handles the maintenance lock acquire/release;
the body runs in `_do_restore_locked` so the outer function can
release the lock in a single try/finally on every exit path.

Re-exported from `bpp.commands` for backwards compatibility with
the CLI and tests (`test_db_restore_command`,
`test_migration_multi_step`, `test_state_change_logging`).
"""

from __future__ import annotations

import argparse
import os
import sys

from bpp.utils.logging import get_logger, setup_logging  # noqa: F401


def do_db_restore_backup(args: argparse.Namespace) -> int:
    """Restore the library DB from .backup (or .backup.prev).

    Recovery path for a failed schema migration or DB corruption.
    Migrations are forward-only — once a bad migration commits to
    the live DB, the user can't undo it from the running app. This
    command lets them roll back to the pre-migration `.backup`
    snapshot from the CLI.

    Behavior:
      1. Resolve the library DB path (`<library>/data/photopicker.db`).
      2. Pick the source: `.backup` by default, `.backup.prev` if
         `--previous` was passed.
      3. Verify the source's integrity. Refuse to restore from a
         corrupt backup — the user is better off keeping the current
         DB than copying garbage on top of it.
      4. Move the current DB aside with a timestamped suffix
         (`.before-restore-<utc-timestamp>`). Keep WAL/SHM
         alongside so the moved-aside DB stays readable.
      5. Copy the source `.backup` (and its sibling WAL/SHM if
         present) into place.
      6. Print clear next steps.

    Refuses to run if a server is currently bound to the library
    (best-effort: `data/.serving.lock` would be the cleanest signal
    but doesn't exist yet — for now the user is reminded to stop
    the server first).
    """
    import contextlib

    from bpp.db.connection import check_integrity

    library = os.path.expanduser(args.library)
    db_path = os.path.join(library, "data", "photopicker.db")
    workdir = os.path.dirname(db_path)
    backup_path = db_path + (".backup.prev" if args.previous else ".backup")

    if not os.path.isfile(db_path):
        print(f"error: library DB not found: {db_path}", file=sys.stderr)
        return 2
    if not os.path.isfile(backup_path):
        which = ".backup.prev" if args.previous else ".backup"
        print(f"error: {which} not found at {backup_path}", file=sys.stderr)
        if args.previous:
            print(
                "(.backup.prev only exists once a second backup has rotated.)",
                file=sys.stderr,
            )
        return 2

    # Running-server guard: refuse to restore while a server
    # has the DB open. Otherwise the running server keeps writing to
    # the now-orphaned old file and the user's "restored" library is
    # missing every change made between the restore and the next
    # restart. --force bypasses (last-resort escape hatch for stale
    # lockfiles that the kill-probe couldn't recover from, e.g.
    # PID reuse during a crash window).
    from bpp.utils.serving_lock import read_lock_pid_if_alive

    if not getattr(args, "force", False):
        live_pid = read_lock_pid_if_alive(workdir)
        if live_pid is not None:
            print(
                f"error: a server is running against this library "
                f"(PID {live_pid}). Stop it first:\n"
                f"  kill {live_pid}\n"
                f"or pass --force to bypass this check (only safe if "
                "you're sure no server / desktop app is attached).",
                file=sys.stderr,
            )
            return 6

    # per-library maintenance lock. The serving-lock guard
    # above prevents server vs restore races, but does NOT prevent
    # two restore-backup invocations from racing each other (e.g.,
    # one shell reads the .backup, mid-copy a second shell starts,
    # they interleave moves of live DB / WAL / SHM and the result
    # is a half-restored DB the user can't recover from).
    #
    # The maintenance lock is owned by THIS process for the entire
    # restore. --force does NOT bypass it — that would silently
    # re-introduce the race. If a prior restore crashed leaving a
    # stale lock, the user can manually remove it after confirming
    # no other restore is running.
    # write PID + create lockfile atomically via the
    # tmp+link pattern. The previous shape did O_EXCL → (later)
    # fdopen+write, which left a window where the lockfile existed
    # with empty content. A racing contender during that window
    # would read empty content → ValueError → treat as stale →
    # remove → silently win the lock too. Both processes then
    # entered the restore body together, racing the DB/WAL/SHM
    # moves the lock was supposed to serialize.
    #
    # Fix: write the PID to a process-unique tmp file FIRST, then
    # atomically link it into place at .restore.lock. os.link
    # fails-if-exists (atomic on POSIX); the lockfile, when it
    # appears, ALWAYS already contains the PID. The empty-content
    # window is gone.
    maintenance_lock = os.path.join(workdir, ".restore.lock")
    pid_str = str(os.getpid())
    pid_bytes = pid_str.encode("utf-8")
    tmp_lock = maintenance_lock + f".tmp.{pid_str}"

    def _try_link(_retry_after_stale: bool = False) -> int | None:
        """Acquire the maintenance lock atomically. Returns rc on
        failure, None on success."""
        # Write PID-bearing tmp file
        try:
            fd = os.open(tmp_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError as e:
            print(
                f"error: could not create maintenance-lock tmp file {tmp_lock}: {e}",
                file=sys.stderr,
            )
            return 7
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(pid_bytes)
        except OSError as e:
            with contextlib.suppress(OSError):
                os.remove(tmp_lock)
            print(
                f"error: could not write PID to maintenance-lock tmp: {e}",
                file=sys.stderr,
            )
            return 7
        # Atomic publish: link tmp → lockfile. Fails if lockfile
        # exists. The published file ALWAYS contains the PID.
        try:
            os.link(tmp_lock, maintenance_lock)
        except FileExistsError:
            # Another restore got there first. Inspect their PID.
            with contextlib.suppress(OSError):
                os.remove(tmp_lock)
            try:
                with open(maintenance_lock, encoding="utf-8") as f:
                    holder_raw = f.read().strip()
                holder_pid = int(holder_raw)
                os.kill(holder_pid, 0)
            except (OSError, ValueError):
                # Stale (PID gone, file unreadable, or empty content
                # from an old non-atomic version). Remove + retry once.
                if _retry_after_stale:
                    print(
                        "error: maintenance lock keeps being recreated; manual cleanup required.",
                        file=sys.stderr,
                    )
                    return 7
                with contextlib.suppress(OSError):
                    os.remove(maintenance_lock)
                return _try_link(_retry_after_stale=True)
            else:
                print(
                    f"error: another `bpp db restore-backup` is already "
                    f"running (PID {holder_pid}). Wait for it to finish, or "
                    f"remove {maintenance_lock} if you're sure no restore is "
                    "in progress.",
                    file=sys.stderr,
                )
                return 7
        except OSError as e:
            with contextlib.suppress(OSError):
                os.remove(tmp_lock)
            print(
                f"error: could not link maintenance lock at {maintenance_lock}: {e}",
                file=sys.stderr,
            )
            return 7
        # Linked successfully; tmp is no longer needed (lockfile is
        # the same inode now via hardlink, but keep the lockfile
        # canonical and remove the tmp).
        with contextlib.suppress(OSError):
            os.remove(tmp_lock)
        return None

    rc = _try_link()
    if rc is not None:
        return rc

    # from here through the end of the function, the
    # maintenance lock is held. Wrap the body in try/finally so
    # every exit path (success, validation refusal, mid-restore
    # error) releases the lock — otherwise a subsequent restore
    # would refuse to start with a stale lockfile.
    try:
        return _do_restore_locked(args, db_path, backup_path, check_integrity)
    finally:
        with contextlib.suppress(OSError):
            os.remove(maintenance_lock)


# Restore body lives in db_restore_impl.py since the v0.1 cleanup so this
# module stays under the 500-LOC soft cap. The outer wrapper above just
# owns the maintenance-lock; the meat is in the impl module.
from bpp.commands.db_restore_impl import _do_restore_locked  # noqa: E402
