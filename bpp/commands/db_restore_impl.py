"""Inner body of the ``bpp db restore-backup`` command.

Extracted from :mod:`bpp.commands.db_restore` during the v0.1 cleanup
so the host module stays under the 500-LOC soft cap. The outer
``do_db_restore_backup`` owns the maintenance-lock acquire/release and
delegates the actual restore work to :func:`_do_restore_locked` here.

Re-exported from :mod:`bpp.commands.db_restore` for back-compat.
"""

from __future__ import annotations

import os

from bpp.utils.logging import get_logger


def _do_restore_locked(args, db_path: str, backup_path: str, check_integrity) -> int:
    """Restore body that runs under the maintenance lock. Extracted
    from do_db_restore_backup so the outer function can release the
    lock in a single try/finally on every exit path."""
    import contextlib  # noqa: F401  # used in inner finallies
    import datetime
    import shutil
    import sys

    print(f"Restoring DB from: {backup_path}")
    print(f"           target: {db_path}")
    print()

    # refuse to restore if a `.corrupt-<timestamp>` sibling
    # exists alongside the chosen backup. A stranded quarantine file
    # is a red flag — the previous backup_db() couldn't cleanly
    # quarantine a corrupt copy. Operator should review (and most
    # likely delete) the .corrupt file before proceeding, so they
    # don't inadvertently restore from a state the system already
    # flagged as suspect.
    #
    # also catch the legacy `.backup.corrupt` (no
    # timestamp suffix) shape. Pre-R11 builds wrote that fixed
    # name, and a user upgrading across versions could have one
    # left over. The preflight refusal must cover both forms so
    # the upgrade path is fail-closed too.
    import glob as _glob

    stranded = _glob.glob(backup_path + ".corrupt-*")
    legacy = backup_path + ".corrupt"
    if os.path.exists(legacy):
        stranded.append(legacy)
    if stranded:
        print(
            f"error: corrupt-quarantine sibling(s) exist alongside {backup_path}:",
            file=sys.stderr,
        )
        for s in stranded:
            print(f"  {s}", file=sys.stderr)
        print(
            "An earlier backup failed verification and was quarantined. "
            "Review the file(s) above (e.g., bring them to a maintainer) "
            "and delete them before retrying. Refusing to restore until "
            "they're cleaned up so you don't unknowingly restore over a "
            "state the system flagged as suspect.",
            file=sys.stderr,
        )
        return 6

    # Verify the source backup before doing anything destructive.
    if not check_integrity(backup_path):
        print(
            f"error: {backup_path} failed integrity check — refusing to "
            "restore from a corrupt backup. The current DB has been left "
            "untouched.",
            file=sys.stderr,
        )
        return 3

    # refuse to restore a backup whose schema is NEWER than
    # the running binary's `SCHEMA_VERSION`. A user who downgraded
    # bpp (e.g. v0.2 → v0.1) would otherwise restore a v29 `.backup`
    # onto a binary that knows v23; the older binary then either
    # silently misreads columns it doesn't know about, or runs its
    # forward migrations again and corrupts the live DB. Either way
    # the user thinks "I restored my data" but in fact lost it.
    # Fail closed and tell them to upgrade bpp first.
    import sqlite3 as _sqlite3

    from bpp.db.connection import get_db
    from bpp.db.schema import SCHEMA_VERSION

    try:
        _check = get_db(backup_path)
        row = _check.execute("PRAGMA user_version").fetchone()
        backup_user_version = int(row[0]) if row else 0
        if backup_user_version == 0:
            has_photos = _check.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='photos'"
            ).fetchone()
            if not has_photos:
                print(
                    f"error: {backup_path} is a valid SQLite file but has no bpp "
                    "schema (user_version=0, no 'photos' table). This does not "
                    "look like a bpp database — refusing to restore.",
                    file=sys.stderr,
                )
                return 3
    except _sqlite3.Error as e:
        print(
            f"error: could not read PRAGMA user_version from {backup_path}: {e}",
            file=sys.stderr,
        )
        return 3

    if backup_user_version > SCHEMA_VERSION:
        print(
            f"error: backup at {backup_path} is at schema v{backup_user_version}, "
            f"but this bpp binary expects v{SCHEMA_VERSION} or older. The backup "
            "appears to have been written by a NEWER bpp than the one running. "
            "Upgrade bpp before restoring, or pick a different backup.",
            file=sys.stderr,
        )
        return 4

    # Staleness check: backup_db() runs once per server startup, so a
    # rarely-run app accumulates a stale backup. Restoring a 60-day-old
    # backup silently loses 60 days of imports/edits — exactly what a
    # nervous user reaching for `restore-backup` does NOT want.
    #
    # Prefer the metadata sidecar's `created_at_epoch` over
    # filesystem mtime. mtime can be touched/copied without preserving
    # the original creation time, so a stale backup with a fresh
    # mtime would otherwise sail through this guard. Fall back to
    # mtime when the sidecar is missing or malformed (older bpp
    # versions wrote no sidecar) and warn so the operator knows the
    # estimate is weaker.
    from bpp.db.connection import read_backup_meta

    meta = read_backup_meta(backup_path)
    live_mtime = os.path.getmtime(db_path)
    backup_path_mtime = os.path.getmtime(backup_path)
    # Time skew tolerance: 1 hour. Clocks differ across hosts, an NTP
    # adjustment can move time slightly, etc. A sidecar more than 1h
    # in the future is suspect.
    _SKEW_TOLERANCE_S = 3600.0
    now = __import__("time").time()

    if meta is not None and "created_at_epoch" in meta:
        meta_epoch = float(meta["created_at_epoch"])
        # Reject future-dated sidecars (D-04). A forged or corrupt
        # `created_at_epoch` set in the future would otherwise produce
        # age=0 (max(0.0, ...) clamp) and bypass the staleness guard.
        if meta_epoch > now + _SKEW_TOLERANCE_S:
            print(
                f"warning: backup metadata claims to be from the future "
                f"(created_at_epoch={meta_epoch}, now={now}). Treating as "
                "untrustworthy and falling back to mtime + treating as STALE.",
                file=sys.stderr,
            )
            backup_mtime = backup_path_mtime
            meta_source = "mtime (sidecar rejected as future-dated)"
        else:
            # Use the OLDER of metadata vs mtime. A backup that was
            # really written N days ago will have BOTH old metadata
            # and old mtime; trusting whichever is older catches the
            # case where one of them was tampered to look fresh.
            backup_mtime = min(meta_epoch, backup_path_mtime)
            if backup_mtime == backup_path_mtime and backup_path_mtime < meta_epoch:
                meta_source = "mtime (older than sidecar — sidecar may be forged)"
            else:
                meta_source = "metadata"
    else:
        backup_mtime = backup_path_mtime
        meta_source = "mtime (no sidecar)"
        print(
            "note: backup metadata sidecar missing — falling back to "
            "filesystem mtime for staleness estimate (less reliable; "
            "a `touch` or copy could mask a stale backup).",
            file=sys.stderr,
        )

    age_seconds = max(0.0, live_mtime - backup_mtime)
    age_days = age_seconds / 86400.0
    backup_dt = datetime.datetime.fromtimestamp(backup_mtime)
    live_dt = datetime.datetime.fromtimestamp(live_mtime)
    backup_human = backup_dt.strftime("%Y-%m-%d %H:%M") + f" [{meta_source}]"
    live_human = live_dt.strftime("%Y-%m-%d %H:%M")

    is_stale = age_days > 7.0
    is_very_stale = age_days > 30.0

    if is_stale:
        print(
            f"WARNING: backup is {age_days:.0f} days old.\n"
            f"  backup written:  {backup_human}\n"
            f"  live DB written: {live_human}\n"
            f"  delta:           {age_days:.0f} days\n"
            "Restoring will move the current DB aside and replace it with\n"
            "this older snapshot. Anything done in the last "
            f"{age_days:.0f} days (imports, edits, curation, face merges,\n"
            "etc.) will not be in the restored DB.\n",
            file=sys.stderr,
        )

    # Non-interactive guard: --yes alone is not enough for a stale
    # backup. The most common automation path (CI, scripted recovery)
    # shouldn't silently destroy weeks of work.
    if args.yes and is_stale and not args.accept_stale:
        print(
            "error: backup is stale (>7 days) and --accept-stale was not "
            "passed. Refusing to restore non-interactively. Run without "
            "--yes to confirm interactively, or pass --accept-stale to "
            "explicitly authorize the data loss.",
            file=sys.stderr,
        )
        return 5

    if not args.yes:
        print(
            "This will move the current DB aside and replace it.\n"
            "Stop any running `bpp serve` first.\n"
        )
        # Very-stale (>30 days) requires typing RESTORE, not just y —
        # the bigger the data-loss window, the higher the friction.
        prompt = (
            "Type RESTORE to proceed (case-sensitive): " if is_very_stale else "Proceed? [y/N]: "
        )
        try:
            ans = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.", file=sys.stderr)
            return 1
        if is_very_stale:
            if ans != "RESTORE":
                print("Aborted.")
                return 1
        elif ans.lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    # Move current DB + WAL/SHM aside (timestamped) so the user can
    # inspect / recover them later if the restore turns out wrong.
    # Wrap each shutil call in `retry_io`: if the library lives
    # on a NAS / iCloud / Dropbox path, a transient EIO / ESTALE / SMB
    # timeout mid-restore would otherwise leave the live DB moved aside
    # but the backup not yet copied — a worse recovery state than the
    # one the user was trying to leave.
    from bpp.utils.retry import retry_io

    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    aside = db_path + f".before-restore-{stamp}"
    retry_io(shutil.move, db_path, aside, label="restore: move live DB aside")
    moved = [aside]
    for suffix in ("-wal", "-shm"):
        wal = db_path + suffix
        if os.path.isfile(wal):
            target = aside + suffix
            retry_io(shutil.move, wal, target, label=f"restore: move {suffix} aside")
            moved.append(target)

    # Copy backup → DB. Sibling WAL/SHM, if any, come too.
    retry_io(shutil.copy2, backup_path, db_path, label="restore: copy backup -> DB")
    for suffix in ("-wal", "-shm"):
        bwal = backup_path + suffix
        if os.path.isfile(bwal):
            retry_io(shutil.copy2, bwal, db_path + suffix, label=f"restore: copy {suffix}")

    # Final verify on the restored DB so the user gets immediate
    # feedback if something went sideways during the copy itself.
    if not check_integrity(db_path):
        print(
            "error: restored DB failed integrity check — see the moved-"
            f"aside files at {moved} for recovery. "
            "Please file a security/security advisory if you can repro.",
            file=sys.stderr,
        )
        return 4

    # Drop a sentinel so the next startup's backup_db() skips
    # ONE rotation. Without this, the live DB (now identical to
    # .backup) would be re-copied to .backup, rotating the OLD
    # .backup into .backup.prev — overwriting the user's last-good
    # fallback the moment they start the server. The sentinel is
    # consumed (and deleted) on the first backup_db() call after
    # restore, so subsequent rotations resume normally.
    sentinel = db_path + ".restore-pending"
    try:
        with open(sentinel, "w", encoding="utf-8") as f:
            f.write("Created by `bpp db restore-backup`. Consumed on next startup.\n")
    except OSError as e:
        # Sentinel write failure is non-fatal — restore itself
        # succeeded. Log so the operator can manually preserve
        # .backup.prev if they want a multi-step rollback.
        print(
            f"warning: could not write restore sentinel ({e}). "
            "Next startup will rotate .backup.prev. To preserve a "
            "multi-step rollback path, copy .backup.prev aside before "
            "starting the server.",
            file=sys.stderr,
        )

    print()
    print(f"Restored.  Moved-aside files: {', '.join(moved)}")
    print(
        "Once you've confirmed the app starts and your library looks "
        "correct, you can delete the moved-aside files manually."
    )
    # persist restore success in server.log so the next
    # `bpp serve` startup has an explicit breadcrumb of what
    # happened. Without this, a script that does
    # `bpp db restore-backup && bpp serve` left no log evidence
    # the restore even ran — only stdout, which scripts often
    # discard.
    log = get_logger(__name__)
    log.info(
        "Database restored from %s (moved-aside files: %s)",
        backup_path,
        ", ".join(moved),
    )
    return 0
