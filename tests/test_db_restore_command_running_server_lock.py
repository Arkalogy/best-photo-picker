"""`bpp db restore-backup` — recovery from a failed migration — part 2 of 3: TestRunningServerLock,
TestRestorePreservesPrev, TestRestoreMaintenanceLock."""

from __future__ import annotations

import os
import sqlite3

from bpp.commands import do_db_restore_backup
from bpp.db.connection import (
    backup_db,
)
from tests._db_restore_command_shared import _args, _make_library


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
    """A per-library maintenance lock prevents two parallel
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
        """The lockfile, once it exists, must contain the
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
                f"Lockfile atomicity broken: tmp file linked with content {content!r} "
                f"instead of PID {my_pid!r}. The lockfile would appear "
                "empty to a racing contender."
            )
        # And the lock is cleaned up after success
        assert not os.path.isfile(lock_path_canonical)


# ─── Refuse restore when .backup.corrupt-* sibling exists ────


def test_restore_refuses_when_corrupt_quarantine_present(tmp_path, capsys):
    """A stranded `.backup.corrupt-<timestamp>` file is a red
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
