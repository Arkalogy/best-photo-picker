"""SQLite backup rotation + backup metadata.

Extracted from :mod:`bpp.db.connection` (LOC gate split, 2026-06-12).
Re-exported from connection so historical imports keep working.
"""

from __future__ import annotations

import contextlib
import datetime
import os
import shutil
import sqlite3

from bpp.constants import SQLITE_TIMEOUT_S
from bpp.db.integrity import check_integrity, full_integrity_check
from bpp.utils.logging import get_logger

_log = get_logger(__name__)

# process-wide flag set when `_consume_restore_sentinel` fires.
# Tells `_migrate()` to skip ALL per-step backups for this run, since
# rotating `.backup ↔ .backup.prev` after the user just restored from
# `.backup.prev` would clobber the very snapshot they recovered from
# (e.g. forward migrations re-running on the just-restored DB push
# the bad upgrade's state into `.backup` and the recovery snapshot
# into oblivion). Re-armed at process boot — single-run scope.
_post_restore_skip_backup = False


def set_post_restore_skip_backup(value: bool) -> None:
    """Cross-module setter so `_consume_restore_sentinel` (in state.py)
    can signal `_migrate()` (in schema.py) without an import cycle."""
    global _post_restore_skip_backup
    _post_restore_skip_backup = value


def is_post_restore_skip_backup() -> bool:
    return _post_restore_skip_backup


def backup_db(db_path: str, preserve_prev: bool = False) -> str | None:
    """Copy db_path → db_path.backup before any mutations.

    Uses rotation to avoid overwriting a good backup with a corrupt DB:
    - If the current DB fails integrity check, skip the backup (preserve
      the existing .backup which may be the last good copy).
    - Otherwise rotate: .backup → .backup.prev, then create new .backup.
    - **Verify the new backup** by running quick_check on the copy. A
      partial-write or truncated copy would otherwise sit on disk
      claiming to be a backup and silently fail at restore time. On
      verify failure, the bad copy is deleted and we restore .backup
      from .backup.prev so the user isn't left without ANY backup.

    ``preserve_prev=True`` skips the rotation step (the
    `.backup → .backup.prev` copy). Used by `_migrate()` for the
    second-and-later steps of a multi-step migration so the
    pre-upgrade snapshot stays in `.backup.prev` for the duration of
    the whole `_migrate()` run, not just for the first step. Without
    this guard, a v23 → v29 upgrade (6 steps) clobbers `.backup.prev`
    on step 2, leaving the user no path back to v23 if step 5 fails.

    Returns the backup path on success, None if skipped or failed.
    Skips if the DB file doesn't exist or is empty.
    """
    if not os.path.isfile(db_path) or os.path.getsize(db_path) == 0:
        return None

    # Don't overwrite a good backup with a potentially corrupt DB
    if not check_integrity(db_path):
        _log.warning("DB failed integrity check — NOT overwriting existing backup")
        return None

    backup_path = db_path + ".backup"
    prev_path = db_path + ".backup.prev"

    # Track whether we already rotated so a verification failure can
    # roll back instead of leaving the user with no .backup at all.
    rotated = False

    try:
        # Rotate: current backup → .prev (keep one generation).
        # Skipped when `preserve_prev=True` (multi-step
        # migration path) — the existing `.backup.prev` is already
        # the pre-upgrade snapshot we want to keep.
        if os.path.isfile(backup_path) and not preserve_prev:
            shutil.copy2(backup_path, prev_path)
            for suffix in ("-wal", "-shm"):
                bwal = backup_path + suffix
                if os.path.isfile(bwal):
                    shutil.copy2(bwal, prev_path + suffix)
                else:
                    # Stale .prev WAL/SHM would falsely advertise a
                    # WAL we no longer have — drop it.
                    pwal = prev_path + suffix
                    if os.path.isfile(pwal):
                        os.remove(pwal)
            from bpp.db.connection import _restrict_db_perms

            _restrict_db_perms(prev_path)
            rotated = True

        # Create new backup from current DB
        shutil.copy2(db_path, backup_path)
        for suffix in ("-wal", "-shm"):
            wal = db_path + suffix
            if os.path.isfile(wal):
                shutil.copy2(wal, backup_path + suffix)
            else:
                # Clean up stale backup WAL/SHM if source no longer has them
                bwal = backup_path + suffix
                if os.path.isfile(bwal):
                    os.remove(bwal)

        # Verify the COPY, not just the source. A partial / truncated
        # write would otherwise silently sit on disk as a "backup".
        if not check_integrity(backup_path):
            # timestamped quarantine name. The previous shape
            # used a fixed `.backup.corrupt` target, which collided
            # if a prior failure left a quarantine file behind (or
            # if two backup runs raced). Now every failure lands a
            # unique file, evidence is never overwritten, and the
            # earlier remove-existing-quarantine step is no longer
            # needed.
            #
            # all log lines in this
            # cascade share a quarantine_event_id so an operator
            # triaging server.log can thread them. UTC timestamp
            # doubles as both the unique suffix and the event ID.
            #
            # every step that can OSError logs with
            # `exc_info=True` so a silent quarantine failure is
            # impossible. If quarantine AND fallback removal both
            # fail, an explicit ERROR records that manual cleanup
            # is required.
            # 1-second resolution collided when two backup
            # verifications failed in the same wall-clock second
            # (concurrent runs, fast retries). Microsecond precision
            # makes `.corrupt-*` filenames effectively unique without
            # changing the lexically-sortable ordering. The trailing
            # `Z` stays so the suffix is still ISO-8601 UTC.
            quarantine_event_id = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S.%fZ")
            quarantine = f"{backup_path}.corrupt-{quarantine_event_id}"
            _log.error(
                "[quarantine=%s] Backup at %s failed verification — "
                "quarantining and restoring from .backup.prev",
                quarantine_event_id,
                backup_path,
            )
            try:
                shutil.move(backup_path, quarantine)
            except OSError:
                _log.warning(
                    "[quarantine=%s] Could not quarantine corrupt "
                    "backup %s -> %s; attempting to remove the corrupt "
                    "copy in place",
                    quarantine_event_id,
                    backup_path,
                    quarantine,
                    exc_info=True,
                )
                # Fallback: at least remove the corrupt copy so it
                # can't masquerade as a valid backup.
                try:
                    os.remove(backup_path)
                except OSError:
                    _log.error(
                        "[quarantine=%s] Corrupt backup at %s could "
                        "neither be quarantined nor removed — it will "
                        "remain on disk and may be mistaken for a valid "
                        "backup. Manual cleanup required.",
                        quarantine_event_id,
                        backup_path,
                        exc_info=True,
                    )
            for suffix in ("-wal", "-shm"):
                bad = backup_path + suffix
                if os.path.isfile(bad):
                    try:
                        os.remove(bad)
                    except OSError:
                        _log.warning(
                            "[quarantine=%s] Could not remove bad backup sidecar %s",
                            quarantine_event_id,
                            bad,
                            exc_info=True,
                        )

            # Restore .backup from .backup.prev if we rotated, so the
            # user still has the previous good generation. If no
            # rotation happened (first-run, no prior backup), we
            # simply have no .backup — which is honest.
            if rotated and os.path.isfile(prev_path):
                shutil.copy2(prev_path, backup_path)
                for suffix in ("-wal", "-shm"):
                    pwal = prev_path + suffix
                    if os.path.isfile(pwal):
                        shutil.copy2(pwal, backup_path + suffix)
            return None

        # Write a metadata sidecar next to the verified backup so
        # `bpp db restore-backup` can compute staleness from the
        # backup's creation timestamp instead of relying on filesystem
        # mtime, which a `touch` or copy-with-mtime-preserve can lie
        # about. Failure to write metadata is non-fatal — the restore
        # path falls back to mtime and warns.
        try:
            _write_backup_meta(db_path, backup_path)
        except OSError as e:
            _log.warning("Could not write backup metadata: %s", e)

        from bpp.db.connection import _restrict_db_perms

        _restrict_db_perms(backup_path)
        _log.info("DB backup created and verified: %s", backup_path)
        return backup_path
    except OSError as e:
        _log.warning("Failed to create DB backup: %s", e)
        return None


def _write_backup_meta(db_path: str, backup_path: str) -> None:
    """Write a JSON metadata sidecar next to the backup file.

    The sidecar records the actual creation time (UTC) plus a few
    sanity fingerprints so a future restore can detect when the
    backup file's mtime has been touched / copied with the wrong
    preservation.

    Schema (subject to additive change — `version` field signals
    forward compat):
        {
            "version": 1,
            "created_at_utc": "<isoformat>",
            "created_at_epoch": <float>,
            "source_db_mtime": <float>,
            "source_db_size": <int>,
            "user_version": <int>,
        }
    """
    import datetime
    import json

    user_version = 0
    try:
        conn = sqlite3.connect(db_path, timeout=SQLITE_TIMEOUT_S)
        try:
            row = conn.execute("PRAGMA user_version").fetchone()
            user_version = int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.Error:
        pass  # user_version is best-effort

    now = datetime.datetime.now(datetime.UTC)
    try:
        st = os.stat(db_path)
        source_mtime = st.st_mtime
        source_size = st.st_size
    except OSError:
        source_mtime = 0.0
        source_size = 0

    meta = {
        "version": 1,
        "created_at_utc": now.isoformat(),
        "created_at_epoch": now.timestamp(),
        "source_db_mtime": source_mtime,
        "source_db_size": source_size,
        "user_version": user_version,
    }
    meta_path = backup_path + ".meta.json"
    tmp = meta_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, meta_path)
    import contextlib

    with contextlib.suppress(OSError):
        os.chmod(meta_path, 0o600)


def read_backup_meta(backup_path: str) -> dict | None:
    """Read the metadata sidecar for `backup_path`. Returns None if
    missing, unparseable, or has the wrong shape — caller falls back
    to filesystem mtime."""
    import json

    meta_path = backup_path + ".meta.json"
    if not os.path.isfile(meta_path):
        return None
    try:
        with open(meta_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "created_at_epoch" not in data:
        return None
    return data


def restore_from_backup_if_corrupt(db_path: str) -> str | None:
    """Auto-restore from ``.backup`` when the current DB is corrupt.

    Returns:
        - ``None`` if no restore was needed (DB integrity passes).
        - The path of the corrupt-DB sidecar we kept (``.corrupted-{ts}``)
          on successful restore — the caller logs it so the user can
          decide whether to triage / forensic the broken file.

    Raises:
        ``RuntimeError`` if the DB is corrupt AND no good backup is
        available. The caller (server startup) catches and surfaces a
        clear "DB corrupted, no backup, options:" message instead of
        crashing on the next query.

    This is exactly the manual sequence the user ran during the Jun-2
    incident. Auto-running it at boot means a transient
    SIGKILL-mid-write that corrupts the WAL doesn't require operator
    intervention — the server starts clean and the user sees a
    one-line warning in Activity.

    Concurrency:
        Primarily intended for startup (before any pool connection
        exists). As of P-11, the swap goes through a staging file +
        atomic ``os.replace`` so ``db_path`` is never absent during
        the operation — a plugin calling this from a non-startup
        context won't observe "file not found." A live pool reader
        would still see the file flip from corrupt to restored at an
        unpredictable moment; close the pool first if you don't want
        that.
    """
    if not os.path.isfile(db_path):
        return None
    ok, errors = full_integrity_check(db_path)
    if ok:
        return None

    _log.error(
        "DB integrity check FAILED at %s — %d error(s): %s",
        db_path,
        len(errors),
        errors[:3],  # first 3 inline; full list in DEBUG below
    )
    _log.debug("Full integrity check errors: %s", errors)

    backup_path = db_path + ".backup"
    if not os.path.isfile(backup_path):
        raise RuntimeError(
            f"DB at {db_path} is corrupt and no .backup found. "
            f"Cannot auto-restore. Options: (1) restore .backup.prev "
            f"manually if it exists, (2) bpp db restore-backup --force, "
            f"(3) start with a fresh library."
        )
    backup_ok, backup_errors = full_integrity_check(backup_path)
    if not backup_ok:
        raise RuntimeError(
            f"DB at {db_path} is corrupt AND .backup is also corrupt "
            f"({len(backup_errors)} error(s) in backup). "
            f"Try .backup.prev (bpp db restore-backup --prev) or start "
            f"with a fresh library."
        )

    import datetime as _dt
    import shutil as _shutil

    ts = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    corrupt_sidecar = db_path + f".corrupted-{ts}"
    staging_path = db_path + ".restoring"

    # P-11: never leave ``db_path`` absent during the operation.
    #
    # Old sequence was move(db_path → corrupt) then copy(backup → db_path).
    # Between those two steps ``db_path`` didn't exist; a concurrent
    # opener (a plugin firing on_db_restore-triggered work, a future
    # admin UI calling this directly) would see "file not found"
    # instead of either the corrupt or the restored DB.
    #
    # New sequence:
    #   1. Copy ``backup_path`` to ``staging_path`` while the corrupt
    #      DB is still in place. This is the slow step; ``db_path``
    #      remains readable throughout.
    #   2. Hard-link ``db_path`` to ``corrupt_sidecar`` so the corrupt
    #      file survives the swap (the original inode now has two
    #      names — db_path and the sidecar).
    #   3. ``os.replace(staging_path, db_path)`` — atomic on both
    #      POSIX and Windows. ``db_path`` flips from corrupt to
    #      restored in one inode swap; never absent.
    #   4. Remove the stale WAL / SHM siblings now that they're
    #      paired with neither file's state.
    #
    # If hard-linking is unavailable on the filesystem (rare; some
    # FUSE backends, FAT) we fall back to the old move-then-copy
    # sequence with a logged warning so the gap is at least visible.
    _shutil.copy2(backup_path, staging_path)
    try:
        try:
            os.link(db_path, corrupt_sidecar)
        except (OSError, NotImplementedError) as link_exc:
            _log.warning(
                "Hard link unavailable (%s); falling back to move(). "
                "Live db_path will be briefly absent during the swap.",
                link_exc,
            )
            _shutil.move(db_path, corrupt_sidecar)
        os.replace(staging_path, db_path)
    except Exception:
        # Cleanup staging on failure so a half-completed restore
        # doesn't leave a phantom .restoring file around to confuse
        # the next attempt.
        if os.path.isfile(staging_path):
            with contextlib.suppress(OSError):
                os.remove(staging_path)
        raise
    # Wipe -wal and -shm too — they're keyed to the corrupt main DB.
    for suffix in ("-wal", "-shm"):
        side = db_path + suffix
        if os.path.isfile(side):
            try:
                os.remove(side)
            except OSError as exc:
                _log.warning("Could not remove stale %s: %s", side, exc)
    _log.warning(
        "Auto-restored %s from %s; corrupt file kept at %s. "
        "User-visible changes since the last backup may be lost.",
        db_path,
        backup_path,
        corrupt_sidecar,
    )
    return corrupt_sidecar
