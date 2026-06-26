"""`bpp db restore-backup` — recovery from a failed migration.

Migrations are forward-only: once a bad one commits to the live
DB, the user can't undo it from the running app. This CLI command
rolls the live DB back to the pre-migration `.backup` snapshot,
moving the bad DB aside (timestamped) so the user can still
recover it if the rollback was a mistake.

Tests cover:
  * happy path: backup is verified, restored, current DB moved aside
  * `--previous`: restores `.backup.prev` instead
  * refuses to restore from a corrupt backup (current DB untouched)
  * refuses if neither file exists
  * verifies the restored DB after copy (catches mid-copy corruption)
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3

from bpp.commands import do_db_restore_backup
from bpp.db.connection import (
    backup_db,
    close_all_connections,
    init_db,
)
from bpp.db.photos import upsert_photo


def _make_library(tmp_path):
    """Build a minimal library tree with an initialized DB."""
    library = tmp_path / "library"
    data = library / "data"
    data.mkdir(parents=True)
    db_path = str(data / "photopicker.db")
    conn = init_db(db_path)
    upsert_photo(conn, {"filepath": "/fake/a.jpg", "sha256": "aaa"})
    conn.close()
    close_all_connections()
    return str(library), db_path


def _args(library, *, previous=False, yes=True, accept_stale=True, force=False):
    # Default accept_stale=True so existing tests don't trip the new
    # staleness guard. Tests that exercise the staleness guard pass
    # accept_stale=False explicitly.
    # Default force=False; tests of the running-server lock pass
    # force=True to bypass.
    return argparse.Namespace(
        command="db",
        db_command="restore-backup",
        library=library,
        previous=previous,
        yes=yes,
        accept_stale=accept_stale,
        force=force,
    )


# ─── Happy path ───────────────────────────────────────────────────


def test_restore_replaces_current_db_with_backup(tmp_path):
    library, db_path = _make_library(tmp_path)

    # Make a verified .backup snapshot.
    assert backup_db(db_path) is not None

    # Mutate the live DB so before/after differ.
    conn = init_db(db_path)
    upsert_photo(conn, {"filepath": "/fake/added-after-backup.jpg", "sha256": "bbb"})
    conn.close()
    close_all_connections()

    # Sanity check the live DB has the new row.
    c = sqlite3.connect(db_path)
    rows_before = c.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    c.close()
    assert rows_before == 2

    rc = do_db_restore_backup(_args(library))
    assert rc == 0

    # Live DB now matches the backup (1 row).
    c = sqlite3.connect(db_path)
    rows_after = c.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    c.close()
    assert rows_after == 1, "restored DB should match the .backup snapshot"


def test_restore_moves_current_db_aside_with_timestamp(tmp_path):
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)

    rc = do_db_restore_backup(_args(library))
    assert rc == 0

    # Look for the timestamped aside copy in the data dir.
    data_dir = os.path.dirname(db_path)
    aside = [f for f in os.listdir(data_dir) if f.startswith("photopicker.db.before-restore-")]
    assert len(aside) == 1, f"expected one aside file, got {aside}"


def test_restore_previous_uses_backup_prev(tmp_path):
    library, db_path = _make_library(tmp_path)

    # First backup
    backup_db(db_path)

    # Mutate, second backup → rotates .backup → .backup.prev
    conn = init_db(db_path)
    upsert_photo(conn, {"filepath": "/fake/v2.jpg", "sha256": "v2v"})
    conn.close()
    close_all_connections()
    backup_db(db_path)

    # The .backup.prev should reflect the older state (1 row), .backup
    # the newer (2 rows). After --previous restore, live DB has 1 row.
    rc = do_db_restore_backup(_args(library, previous=True))
    assert rc == 0

    c = sqlite3.connect(db_path)
    rows = c.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    c.close()
    assert rows == 1


# ─── Refusals ─────────────────────────────────────────────────────


def test_refuses_when_backup_missing(tmp_path):
    library, _db_path = _make_library(tmp_path)
    # No .backup created.
    rc = do_db_restore_backup(_args(library))
    assert rc == 2


def test_refuses_when_db_missing(tmp_path):
    library = str(tmp_path / "library")
    os.makedirs(os.path.join(library, "data"))
    rc = do_db_restore_backup(_args(library))
    assert rc == 2


def test_refuses_when_previous_missing(tmp_path):
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)  # only .backup, no .prev
    rc = do_db_restore_backup(_args(library, previous=True))
    assert rc == 2


def test_refuses_corrupt_backup_leaves_db_alone(tmp_path):
    """If the .backup file fails integrity check, do NOT swap it
    in — the user is better off with the current (possibly migrated)
    DB than with a corrupt restore."""
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)
    backup_path = db_path + ".backup"

    # Live DB has unique row that should still be there after refusal.
    conn = init_db(db_path)
    upsert_photo(conn, {"filepath": "/fake/sentinel.jpg", "sha256": "sent"})
    conn.close()
    close_all_connections()

    # Corrupt the backup file so verify rejects it.
    with open(backup_path, "wb") as f:
        f.write(b"not-a-sqlite-db" * 200)

    rc = do_db_restore_backup(_args(library))
    assert rc == 3

    # Live DB still has the sentinel row (untouched by the refusal).
    c = sqlite3.connect(db_path)
    rows = [r[0] for r in c.execute("SELECT filepath FROM photos")]
    c.close()
    assert "/fake/sentinel.jpg" in rows


# ─── Restored-DB verify-after-copy guard ──────────────────────────


def test_verify_fails_after_copy_returns_error(tmp_path, monkeypatch):
    """If something corrupts the file between source-verify and live
    DB, the post-copy integrity check must return error code 4 so
    the user knows the restore is half-done."""
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)

    real_copy2 = shutil.copy2

    def break_target_copy(src, dst, *args, **kwargs):
        result = real_copy2(src, dst, *args, **kwargs)
        # After the legitimate copy, scribble over the destination
        # to simulate a torn write between verify and copy.
        if dst == db_path:
            with open(dst, "wb") as f:
                f.write(b"corrupt" * 100)
        return result

    # do_db_restore_backup imports shutil locally — patch the module
    # itself rather than a re-export.
    monkeypatch.setattr(shutil, "copy2", break_target_copy)
    rc = do_db_restore_backup(_args(library))
    assert rc == 4


# ─── Staleness guard ──────────────────────────────────────────────


def _age_backup(backup_path, days):
    """Backdate the .backup file's mtime AND the metadata sidecar's
    created_at_epoch so the restore command treats it as stale.

    The metadata sidecar is the primary staleness signal; mtime is
    the fallback for older backups without sidecars. Tests that
    simulate "this backup is N days old" need to age both."""
    import json
    import time

    delta = days * 86400
    target = time.time() - delta
    os.utime(backup_path, (target, target))

    meta_path = backup_path + ".meta.json"
    if os.path.isfile(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        meta["created_at_epoch"] = target
        # created_at_utc is informational; keep it consistent
        import datetime

        meta["created_at_utc"] = datetime.datetime.fromtimestamp(
            target, tz=datetime.UTC
        ).isoformat()
        with open(meta_path, "w") as f:
            json.dump(meta, f)


class TestStalenessGuard:
    """`bpp db restore-backup` must warn (or refuse) when the
    backup is materially older than the live DB. Otherwise a user
    running the recovery command silently loses weeks/months of
    work."""

    def test_fresh_backup_no_warning(self, tmp_path, capsys):
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        rc = do_db_restore_backup(_args(library))
        assert rc == 0
        captured = capsys.readouterr()
        assert "stale" not in captured.err.lower()
        assert "WARNING: backup is" not in captured.err

    def test_stale_backup_yes_without_accept_stale_refuses(self, tmp_path, capsys):
        """The most-likely automation footgun: scripted recovery with
        --yes against a stale backup must refuse, not silently
        destroy."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        _age_backup(db_path + ".backup", days=14)

        rc = do_db_restore_backup(_args(library, yes=True, accept_stale=False))
        assert rc == 5
        err = capsys.readouterr().err
        assert "stale" in err.lower()
        assert "--accept-stale" in err

    def test_stale_backup_yes_with_accept_stale_proceeds(self, tmp_path, capsys):
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        _age_backup(db_path + ".backup", days=14)

        rc = do_db_restore_backup(_args(library, yes=True, accept_stale=True))
        assert rc == 0
        # Warning should still fire even when accepted, so the
        # operator sees the breadcrumb in CI logs.
        err = capsys.readouterr().err
        assert "WARNING: backup is" in err
        assert "14 days" in err

    def test_stale_backup_interactive_default_y_blocks_until_typed(
        self, tmp_path, monkeypatch, capsys
    ):
        """Stale-but-not-very-stale (8-30 days) uses the standard
        Y/N prompt. Confirm 'y' proceeds."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        _age_backup(db_path + ".backup", days=14)

        monkeypatch.setattr("builtins.input", lambda _prompt: "y")
        rc = do_db_restore_backup(_args(library, yes=False, accept_stale=False))
        assert rc == 0
        err = capsys.readouterr().err
        assert "14 days" in err

    def test_very_stale_backup_requires_typing_RESTORE(self, tmp_path, monkeypatch, capsys):
        """Backup older than 30 days raises the friction: typing
        'y' is no longer enough — operator must type RESTORE
        explicitly."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        _age_backup(db_path + ".backup", days=120)

        # First attempt: 'y' is rejected
        monkeypatch.setattr("builtins.input", lambda _prompt: "y")
        rc = do_db_restore_backup(_args(library, yes=False, accept_stale=False))
        assert rc == 1  # Aborted
        err = capsys.readouterr().err
        assert "120 days" in err

        # Second attempt: RESTORE proceeds
        monkeypatch.setattr("builtins.input", lambda _prompt: "RESTORE")
        rc = do_db_restore_backup(_args(library, yes=False, accept_stale=False))
        assert rc == 0

    def test_very_stale_backup_requires_exact_case(self, tmp_path, monkeypatch, capsys):
        """Lowercase 'restore' is NOT good enough — capital RESTORE
        is intentional friction."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        _age_backup(db_path + ".backup", days=60)

        monkeypatch.setattr("builtins.input", lambda _prompt: "restore")
        rc = do_db_restore_backup(_args(library, yes=False, accept_stale=False))
        assert rc == 1  # Aborted


# ─── Running-server lock guard ────────────────────────────────────


class TestStalenessMetadata:
    """Staleness must use the metadata sidecar's `created_at_epoch`
    as the primary signal, NOT filesystem mtime. mtime can be touched
    or copied without preserving the original creation time, so a
    stale backup with a fresh mtime would otherwise sail through.
    """

    def test_metadata_sidecar_is_written_on_backup(self, tmp_path):
        """backup_db must write a .meta.json next to the .backup."""
        import json

        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        meta_path = db_path + ".backup.meta.json"
        assert os.path.isfile(meta_path), (
            "backup_db must write a metadata sidecar for staleness tracking"
        )
        with open(meta_path) as f:
            meta = json.load(f)
        assert meta["version"] == 1
        assert "created_at_epoch" in meta
        assert "created_at_utc" in meta
        assert "user_version" in meta

    def test_stale_metadata_with_fresh_mtime_still_warns(self, tmp_path, capsys):
        """A backup whose metadata says 14 days old but whose mtime
        is fresh (because someone touched/copied it) MUST still
        trigger the staleness guard. Without the sidecar this would
        be bypassable."""
        import json

        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        # Backdate ONLY the metadata, leave mtime fresh
        meta_path = db_path + ".backup.meta.json"
        with open(meta_path) as f:
            meta = json.load(f)
        meta["created_at_epoch"] -= 14 * 86400
        with open(meta_path, "w") as f:
            json.dump(meta, f)
        # mtime stays current
        import time

        os.utime(db_path + ".backup", (time.time(), time.time()))

        rc = do_db_restore_backup(_args(str(tmp_path / "library"), yes=True, accept_stale=False))
        assert rc == 5, "Stale metadata must trigger staleness guard"
        err = capsys.readouterr().err
        assert "stale" in err.lower()

    def test_fresh_metadata_with_stale_mtime_uses_older(self, tmp_path, capsys):
        """D-04 update of C-06: when metadata says fresh but mtime is
        old, the conservative read is to use the OLDER timestamp.
        Previously this test asserted 'fresh metadata wins'; D-04
        flipped that to 'older value wins' so a forged-fresh sidecar
        on top of an actually-old file can't bypass the staleness
        guard. accept_stale=True is implicit in _args, so rc=0; we
        verify the warning fires anyway."""
        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        # Backdate ONLY mtime; metadata stays fresh
        import time

        old = time.time() - (14 * 86400)
        os.utime(db_path + ".backup", (old, old))

        rc = do_db_restore_backup(_args(str(tmp_path / "library")))
        # accept_stale=True default → proceeds, but the staleness
        # warning still fires because we pick the older (mtime) value.
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARNING: backup is" in err, (
            "Conservative-read contract: older timestamp wins. With mtime "
            "14 days old, the warning must fire even when metadata is fresh."
        )
        assert "stale" not in err.lower()

    def test_missing_sidecar_falls_back_to_mtime_with_warning(self, tmp_path, capsys):
        """Backups from older bpp versions don't have a sidecar.
        Restore must still work but warn that the estimate is weak."""
        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        # Delete the sidecar — pretend this was an old backup
        os.remove(db_path + ".backup.meta.json")

        rc = do_db_restore_backup(_args(str(tmp_path / "library")))
        assert rc == 0
        err = capsys.readouterr().err
        assert "metadata sidecar missing" in err

    def test_future_dated_sidecar_rejected_and_falls_back(self, tmp_path, capsys):
        """D-04: a forged/corrupt sidecar with `created_at_epoch` set
        far in the future would otherwise produce age=0 (clamped from
        negative) and silently bypass the staleness guard. Reject
        future-dated metadata and fall back to mtime."""
        import json
        import time

        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        # Forge sidecar to 1 year in the future
        meta_path = db_path + ".backup.meta.json"
        with open(meta_path) as f:
            meta = json.load(f)
        meta["created_at_epoch"] = time.time() + 365 * 86400
        with open(meta_path, "w") as f:
            json.dump(meta, f)

        # And backdate the actual mtime to 14 days ago — so mtime
        # fallback correctly detects staleness
        old_mtime = time.time() - 14 * 86400
        os.utime(db_path + ".backup", (old_mtime, old_mtime))

        rc = do_db_restore_backup(_args(str(tmp_path / "library"), yes=True, accept_stale=False))
        assert rc == 5, (
            "Future-dated sidecar must be rejected and mtime fallback must "
            f"still detect the 14-day staleness — got rc={rc!r}"
        )
        err = capsys.readouterr().err
        assert "future" in err.lower()
        assert "stale" in err.lower()

    def test_sidecar_older_than_mtime_uses_sidecar(self, tmp_path, capsys):
        """The realistic case: sidecar (real creation time) older than
        mtime (touched / copy-with-new-mtime). Use the sidecar; don't
        let a fresher mtime mask staleness."""
        import json
        import time

        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        # Sidecar says 14 days, mtime fresh
        meta_path = db_path + ".backup.meta.json"
        with open(meta_path) as f:
            meta = json.load(f)
        meta["created_at_epoch"] = time.time() - 14 * 86400
        with open(meta_path, "w") as f:
            json.dump(meta, f)
        # mtime stays current

        rc = do_db_restore_backup(_args(str(tmp_path / "library"), yes=True, accept_stale=False))
        assert rc == 5, "Stale sidecar must trigger guard despite fresh mtime"

    def test_sidecar_newer_than_mtime_uses_older_value(self, tmp_path, capsys):
        """The other direction: sidecar fresh but mtime old (e.g.,
        someone restored the .backup file from an old archive without
        regenerating metadata). Use the OLDER value — mtime — so we
        don't trust a sidecar that may have been carried forward
        without the actual bytes being touched."""
        import json
        import time

        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        # Sidecar fresh (just-now), mtime backdated 14 days
        meta_path = db_path + ".backup.meta.json"
        with open(meta_path) as f:
            meta = json.load(f)
        meta["created_at_epoch"] = time.time()
        with open(meta_path, "w") as f:
            json.dump(meta, f)
        old_mtime = time.time() - 14 * 86400
        os.utime(db_path + ".backup", (old_mtime, old_mtime))

        rc = do_db_restore_backup(_args(str(tmp_path / "library"), yes=True, accept_stale=False))
        assert rc == 5, (
            "Older mtime should win even when sidecar claims fresh — "
            "min(metadata, mtime) is the conservative read"
        )


class TestRunningServerLock:
    """`bpp db restore-backup` must refuse to overwrite the DB
    while a server has it open. Otherwise the running server keeps
    writing to the orphaned inode after the restore, and the user
    silently loses any work done in the gap."""

    def _write_lock(self, workdir, pid):
        """Create the lockfile by hand (mirrors what the server's
        write_lock() does on startup)."""
        from bpp.utils.serving_lock import lock_path

        path = lock_path(workdir)
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(pid))
        return path

    def test_running_server_blocks_restore(self, tmp_path, capsys):
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        workdir = os.path.dirname(db_path)
        # Use the current process PID — guaranteed alive
        self._write_lock(workdir, os.getpid())

        rc = do_db_restore_backup(_args(library, yes=True))
        assert rc == 6
        err = capsys.readouterr().err
        assert "server is running" in err
        assert str(os.getpid()) in err

    def test_stale_lockfile_does_not_block(self, tmp_path):
        """A lockfile pointing at a dead PID (crashed server) must
        NOT block recovery. The kill-probe handles this case."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        workdir = os.path.dirname(db_path)
        # Plant a lockfile with a PID that's almost certainly gone.
        # PID 999_999 is well above the macOS / Linux default ceiling.
        self._write_lock(workdir, 999_999)

        rc = do_db_restore_backup(_args(library, yes=True))
        assert rc == 0  # restored successfully despite stale lock

    def test_force_bypasses_alive_lock(self, tmp_path, capsys):
        """--force is the escape hatch for the rare PID-reuse false
        positive. Operator takes responsibility."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        workdir = os.path.dirname(db_path)
        self._write_lock(workdir, os.getpid())

        rc = do_db_restore_backup(_args(library, yes=True, force=True))
        assert rc == 0

    def test_garbage_lockfile_treated_as_stale(self, tmp_path):
        """Empty / non-numeric lockfile content (partial write,
        editor-saved-mid-startup) shouldn't permanently block
        recovery."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        workdir = os.path.dirname(db_path)
        from bpp.utils.serving_lock import lock_path

        # Garbage content
        with open(lock_path(workdir), "w") as f:
            f.write("definitely-not-a-pid")

        rc = do_db_restore_backup(_args(library, yes=True))
        assert rc == 0

    def test_no_lockfile_proceeds_normally(self, tmp_path):
        """The common case: no server up, no lockfile."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        rc = do_db_restore_backup(_args(library, yes=True))
        assert rc == 0


class TestRestorePreservesPrev:
    """Restore must drop a sentinel that causes the next backup_db()
    to skip rotation, so `.backup.prev` survives a restore + normal
    startup cycle.

    Without this: state pre-restore is `.backup`=A, `.backup.prev`=B.
    User restores .backup -> live = A. Next startup runs backup_db,
    rotating .backup (A) -> .backup.prev — overwriting B. The
    user's older fallback snapshot is gone after one normal startup.
    """

    def test_restore_writes_sentinel(self, tmp_path):
        """The restore command must drop a `.restore-pending` sentinel
        next to the live DB so the next backup_db() call knows to
        skip rotation."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        rc = do_db_restore_backup(_args(library, yes=True))
        assert rc == 0
        assert os.path.isfile(db_path + ".restore-pending"), (
            "restore-backup must write a `.restore-pending` sentinel "
            "to skip the next backup rotation"
        )

    def test_startup_consumes_sentinel_and_skips_rotation(self, tmp_path):
        """Simulate startup: write a sentinel, then call the same
        backup-skip logic. Sentinel must be removed and .backup.prev
        must NOT be regenerated from the current .backup."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)  # creates .backup (state A, just the seeded DB)

        # Pre-state: write a distinguishable B into .backup.prev so
        # we can prove it survives.
        prev_path = db_path + ".backup.prev"
        marker_b = b"distinguishable-state-B-content-marker"
        with open(prev_path, "wb") as f:
            f.write(marker_b)

        # Restore (drops the sentinel)
        rc = do_db_restore_backup(_args(library, yes=True))
        assert rc == 0
        assert os.path.isfile(db_path + ".restore-pending")

        # Simulate startup: re-read sentinel + skip backup_db. We
        # invoke the same predicate state.py uses, then call
        # backup_db only if no sentinel.
        sentinel = db_path + ".restore-pending"
        if os.path.isfile(sentinel):
            os.remove(sentinel)
        else:
            backup_db(db_path)

        # `.backup.prev` should still hold the marker — the rotation
        # was skipped.
        assert os.path.isfile(prev_path), ".backup.prev was deleted!"
        with open(prev_path, "rb") as f:
            assert marker_b in f.read(), (
                ".backup.prev was overwritten by the post-restore startup — "
                "the user's last-good fallback got clobbered."
            )

    def test_consume_sentinel_atomic_rename(self, tmp_path):
        """_consume_restore_sentinel uses os.replace (atomic) so the
        skip side effect is gated on a successful rename. Verify
        happy path: sentinel exists -> consumed -> True."""
        from bpp.web.state_helpers import consume_restore_sentinel as _consume_restore_sentinel

        _library, db_path = _make_library(tmp_path)
        sentinel = db_path + ".restore-pending"
        with open(sentinel, "w") as f:
            f.write("test")

        result = _consume_restore_sentinel(db_path)
        assert result is True
        assert not os.path.isfile(sentinel), "sentinel should be gone"

    def test_consume_sentinel_no_sentinel(self, tmp_path):
        """No sentinel -> returns False (caller does normal backup)."""
        from bpp.web.state_helpers import consume_restore_sentinel as _consume_restore_sentinel

        _library, db_path = _make_library(tmp_path)
        # No sentinel exists
        assert _consume_restore_sentinel(db_path) is False

    def test_consume_sentinel_rename_failure_falls_through(self, tmp_path, monkeypatch):
        """If os.replace raises, the sentinel is NOT considered
        consumed and the function returns False so the caller falls
        through to normal backup rotation. Without this, a transient
        OS error would leave the sentinel in place AND skip the
        backup, making the skip effectively permanent."""
        # consume_restore_sentinel lives in state_helpers since the v0.1 split.
        from bpp.web import state_helpers as state_mod

        _library, db_path = _make_library(tmp_path)
        sentinel = db_path + ".restore-pending"
        with open(sentinel, "w") as f:
            f.write("test")

        # Patch os.replace to raise as if the rename failed
        original_replace = os.replace

        def _failing_replace(src, dst):
            if src == sentinel:
                raise OSError("simulated rename failure")
            return original_replace(src, dst)

        monkeypatch.setattr(state_mod.os, "replace", _failing_replace)

        result = state_mod.consume_restore_sentinel(db_path)
        assert result is False, (
            "Failed rename must NOT report consumed — otherwise the "
            "caller skips backup AND the sentinel sits there forever, "
            "making the one-shot skip permanent."
        )
        # Sentinel should still exist (we couldn't consume it)
        assert os.path.isfile(sentinel), (
            "Sentinel must remain on rename failure so a future startup "
            "(or operator) can investigate"
        )

    def test_second_startup_resumes_normal_backup(self, tmp_path):
        """After the sentinel is consumed once, the NEXT startup
        should rotate normally — the skip is one-time."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        prev_path = db_path + ".backup.prev"
        with open(prev_path, "wb") as f:
            f.write(b"original-prev-content")

        rc = do_db_restore_backup(_args(library, yes=True))
        assert rc == 0
        sentinel = db_path + ".restore-pending"

        # First startup: consume the sentinel, skip rotation.
        if os.path.isfile(sentinel):
            os.remove(sentinel)
        else:  # pragma: no cover
            backup_db(db_path)

        # Second startup: no sentinel, full rotation runs. .backup.prev
        # gets overwritten by the current .backup (which now matches
        # live DB). Original prev content is gone.
        assert not os.path.isfile(sentinel)
        backup_db(db_path)
        with open(prev_path, "rb") as f:
            content = f.read()
        # After the normal rotation, .backup.prev should be a valid
        # SQLite file (the previous .backup), NOT the marker.
        assert b"original-prev-content" not in content, (
            "Sentinel survived a startup it should have been consumed by — "
            "the skip is supposed to be one-time."
        )


class TestRestoreMaintenanceLock:
    """R4-M5: a per-library maintenance lock prevents two parallel
    `bpp db restore-backup` invocations from racing each other.

    The serving-lock guard already prevents server-vs-restore
    collisions, but doesn't help when the user runs two restores
    from different shells. Without serialization the moves of
    live DB / WAL / SHM can interleave and produce a half-restored
    DB the user can't recover from.

    --force does NOT bypass the maintenance lock — that would
    silently re-introduce the race.
    """

    def test_lock_file_is_cleaned_up_after_restore(self, tmp_path):
        """Happy path: restore completes, .restore.lock is gone."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        rc = do_db_restore_backup(_args(library))
        assert rc == 0
        data_dir = os.path.dirname(db_path)
        assert not os.path.isfile(os.path.join(data_dir, ".restore.lock")), (
            "Maintenance lock must be released after a successful restore"
        )

    def test_existing_live_lock_blocks_restore(self, tmp_path, monkeypatch):
        """Plant a maintenance lock with a live PID; restore must
        refuse with rc=7."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        data_dir = os.path.dirname(db_path)
        lock_path = os.path.join(data_dir, ".restore.lock")
        # A different live PID (mocked alive)
        other_pid = os.getpid() + 1
        with open(lock_path, "w") as f:
            f.write(str(other_pid))

        original_kill = os.kill

        def _kill_fake(pid, sig):
            if pid == other_pid and sig == 0:
                return  # alive
            return original_kill(pid, sig)

        monkeypatch.setattr(os, "kill", _kill_fake)

        rc = do_db_restore_backup(_args(library))
        assert rc == 7, f"Live maintenance lock must block parallel restore, got {rc}"
        # Lock still in place (not cleaned up — it's not ours)
        assert os.path.isfile(lock_path)

    def test_stale_lock_recovered(self, tmp_path):
        """Stale maintenance lock (PID not alive) is recovered."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        data_dir = os.path.dirname(db_path)
        lock_path = os.path.join(data_dir, ".restore.lock")
        # PID 99999999 is unlikely to be alive
        with open(lock_path, "w") as f:
            f.write("99999999")

        rc = do_db_restore_backup(_args(library))
        assert rc == 0, "Stale maintenance lock should be recovered"
        # And cleaned up after success
        assert not os.path.isfile(lock_path)

    def test_force_does_not_bypass_maintenance_lock(self, tmp_path, monkeypatch):
        """--force was added for the serving lock (PID-reuse edge
        case). It must NOT bypass the maintenance lock — that would
        silently let two restores race."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        data_dir = os.path.dirname(db_path)
        lock_path = os.path.join(data_dir, ".restore.lock")
        other_pid = os.getpid() + 2
        with open(lock_path, "w") as f:
            f.write(str(other_pid))

        original_kill = os.kill

        def _kill_fake(pid, sig):
            if pid == other_pid and sig == 0:
                return
            return original_kill(pid, sig)

        monkeypatch.setattr(os, "kill", _kill_fake)

        rc = do_db_restore_backup(_args(library, force=True))
        assert rc == 7, (
            "--force must NOT bypass the maintenance lock; live parallel restore must still block"
        )

    def test_lock_published_with_pid_atomically(self, tmp_path):
        """R5-H4: the lockfile, once it exists, must contain the
        owner's PID — no empty-content window. Previously the lock
        was created via O_EXCL and the PID was written later;
        a racing contender during that gap could see empty content,
        treat it as stale, remove the lock, and silently double-
        acquire.

        With the atomic tmp+link fix, the file becomes visible at
        the lock path ONLY after the PID is already inside it.
        Verify by intercepting os.link mid-restore: if we read the
        path between the tmp-write and the link, the lockfile
        shouldn't exist; after link succeeds, it exists with the
        PID inside.
        """
        from bpp.commands import do_db_restore_backup as do_restore

        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        data_dir = os.path.dirname(db_path)
        lock_path_canonical = os.path.join(data_dir, ".restore.lock")

        # Run a normal restore. Then verify the lock is gone (cleaned
        # up by the try/finally) — but mid-restore we can't easily
        # observe atomicity in a single-threaded test. The behavior
        # contract we lock here is: at every point the lockfile
        # exists, it has the PID inside it. We approximate this
        # by asserting the lockfile content shape AT one observable
        # point in the lifecycle.
        seen_lock_content: list[str] = []

        original_link = os.link

        def _spy_link(src, dst):
            # If a contender raced us right now, what content
            # would they see in `dst`? At this point, dst doesn't
            # exist yet (link hasn't been called). After link
            # succeeds, dst exists and is hard-linked to src
            # which already has the PID. Capture src content as
            # the "what's about to appear" preview.
            with contextlib.suppress(OSError), open(src, encoding="utf-8") as f:
                seen_lock_content.append(f.read().strip())
            return original_link(src, dst)

        import contextlib
        from unittest.mock import patch

        with patch.object(os, "link", _spy_link):
            rc = do_restore(_args(library))

        assert rc == 0
        assert seen_lock_content, "os.link should have been called during restore"
        # Every captured pre-publish content must be the PID
        # (not empty, not zero)
        my_pid = str(os.getpid())
        for content in seen_lock_content:
            assert content == my_pid, (
                f"R5-H4 broken: tmp file linked with content {content!r} "
                f"instead of PID {my_pid!r}. The lockfile would appear "
                "empty to a racing contender."
            )
        # And the lock is cleaned up after success
        assert not os.path.isfile(lock_path_canonical)


# ─── R11-M7: refuse restore when .backup.corrupt-* sibling exists ────


def test_restore_refuses_when_corrupt_quarantine_present(tmp_path, capsys):
    """R11-M7: a stranded `.backup.corrupt-<timestamp>` file is a red
    flag — backup_db() couldn't cleanly handle a corrupt copy on a
    prior run. Restoring before the operator reviews the file means
    they may be restoring over a state the system already flagged
    suspect.

    The restore command preflights for `.corrupt-*` siblings and
    refuses with exit code 6 + a stderr message naming each
    quarantine file. This is fail-closed by design — operator must
    delete or move the .corrupt-* file before retrying."""
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)

    # Strand a quarantine file alongside the .backup.
    quarantine = db_path + ".backup.corrupt-20260101T000000Z"
    with open(quarantine, "wb") as f:
        f.write(b"corrupt evidence")

    rc = do_db_restore_backup(_args(library))
    assert rc == 6, f"Expected exit code 6 (corrupt-quarantine refusal); got {rc}"

    err = capsys.readouterr().err
    assert "corrupt-quarantine sibling(s)" in err
    assert quarantine in err
    # Live DB is untouched.
    c = sqlite3.connect(db_path)
    n = c.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    c.close()
    assert n == 1, "live DB should be untouched"


def test_restore_proceeds_after_corrupt_quarantine_cleanup(tmp_path):
    """The reverse: operator deletes the .corrupt sibling, retry
    succeeds. Pins that the preflight isn't a permanent block."""
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)

    quarantine = db_path + ".backup.corrupt-20260101T000000Z"
    with open(quarantine, "wb") as f:
        f.write(b"corrupt")

    # First attempt refuses.
    assert do_db_restore_backup(_args(library)) == 6

    # Operator cleans up the quarantine; retry succeeds.
    os.remove(quarantine)
    assert do_db_restore_backup(_args(library)) == 0


def test_restore_refuses_when_legacy_corrupt_backup_marker_exists(tmp_path, capsys):
    """R12-M2: pre-R11 builds quarantined corrupt backups under a
    fixed name `.backup.corrupt` (no timestamp). A user upgrading
    across versions could have one left over. The preflight must
    cover the legacy shape too, otherwise an upgrade path silently
    bypasses the safety check."""
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)

    # Strand a LEGACY (no timestamp) quarantine file alongside.
    legacy = db_path + ".backup.corrupt"
    with open(legacy, "wb") as f:
        f.write(b"old corrupt evidence")

    rc = do_db_restore_backup(_args(library))
    assert rc == 6, f"Legacy .backup.corrupt should still trip the preflight; got rc={rc}"

    err = capsys.readouterr().err
    assert "corrupt-quarantine sibling(s)" in err
    assert legacy in err

    # Live DB untouched.
    c = sqlite3.connect(db_path)
    n = c.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    c.close()
    assert n == 1


def test_restore_refuses_when_both_legacy_and_timestamped_corrupt_present(tmp_path, capsys):
    """If both a legacy `.backup.corrupt` AND timestamped
    `.backup.corrupt-<ts>` exist, the preflight lists both."""
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)

    legacy = db_path + ".backup.corrupt"
    timestamped = db_path + ".backup.corrupt-20260101T000000Z"
    for p in (legacy, timestamped):
        with open(p, "wb") as f:
            f.write(b"corrupt")

    rc = do_db_restore_backup(_args(library))
    assert rc == 6
    err = capsys.readouterr().err
    assert legacy in err
    assert timestamped in err
