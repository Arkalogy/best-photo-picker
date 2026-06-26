"""Unit tests for the serving-lock helper.

The helper is small but load-bearing for `bpp db restore-backup`'s
running-server guard. Cover the three states explicitly: no
lockfile, alive PID, stale PID, and the garbage / empty /
non-existent edge cases.
"""

from __future__ import annotations

import os

import pytest

from bpp.utils.serving_lock import (
    clear_lock,
    lock_path,
    read_lock_pid_if_alive,
    write_lock,
)


def test_write_then_read_returns_self_pid(tmp_path):
    write_lock(str(tmp_path))
    assert read_lock_pid_if_alive(str(tmp_path)) == os.getpid()


def test_write_creates_file_with_pid_text(tmp_path):
    path = write_lock(str(tmp_path))
    assert path is not None
    with open(path) as f:
        assert f.read().strip() == str(os.getpid())


def test_clear_removes_lockfile(tmp_path):
    write_lock(str(tmp_path))
    p = lock_path(str(tmp_path))
    assert os.path.isfile(p)
    clear_lock(str(tmp_path))
    assert not os.path.isfile(p)


def test_clear_is_idempotent(tmp_path):
    """Removing a non-existent lock must not raise (atexit hook
    runs even when write_lock failed)."""
    clear_lock(str(tmp_path))  # no lock to remove
    # And again
    clear_lock(str(tmp_path))


def test_no_lockfile_returns_none(tmp_path):
    assert read_lock_pid_if_alive(str(tmp_path)) is None


def test_stale_pid_returns_none(tmp_path):
    """A PID that's gone is treated as no lock (so restore can
    recover from a crashed server)."""
    p = lock_path(str(tmp_path))
    with open(p, "w") as f:
        # PID 999_999 well above macOS / Linux defaults
        f.write("999999")
    assert read_lock_pid_if_alive(str(tmp_path)) is None


def test_garbage_content_returns_none(tmp_path):
    p = lock_path(str(tmp_path))
    with open(p, "w") as f:
        f.write("not-a-number")
    assert read_lock_pid_if_alive(str(tmp_path)) is None


def test_empty_file_returns_none(tmp_path):
    p = lock_path(str(tmp_path))
    open(p, "w").close()
    assert read_lock_pid_if_alive(str(tmp_path)) is None


def test_zero_or_negative_pid_returns_none(tmp_path):
    """Don't kill(0, 0) or kill(-1, 0) — both have special meanings
    in POSIX (process group / all processes) that would be unsafe
    if we were sending real signals. Reject defensively."""
    p = lock_path(str(tmp_path))
    for value in ("0", "-1"):
        with open(p, "w") as f:
            f.write(value)
        assert read_lock_pid_if_alive(str(tmp_path)) is None


def test_atomic_write_via_tmp_rename(tmp_path):
    """write_lock writes through a `.tmp` + rename so a partial
    write can't leave an empty/garbage lockfile that confuses the
    probe. Confirm the .tmp doesn't linger after success."""
    write_lock(str(tmp_path))
    tmp = lock_path(str(tmp_path)) + ".tmp"
    assert not os.path.exists(tmp)


def test_workdir_must_exist(tmp_path):
    """write_lock returns None if the workdir doesn't exist —
    don't auto-create directories from a lock helper."""
    missing = str(tmp_path / "does-not-exist")
    assert write_lock(missing) is None


def test_empty_workdir_string_returns_none(tmp_path):
    """All three helpers must tolerate empty-string workdir
    (callers sometimes pass `state.get('workdir')` which may
    return ''). No-op rather than raise."""
    assert write_lock("") is None
    assert read_lock_pid_if_alive("") is None
    clear_lock("")  # must not raise


# ─── Exclusive serving lock ───────────────────────────────────────


class TestAcquireLock:
    """`acquire_lock` uses O_EXCL so exactly one process wins the
    creation race. Two concurrent `bpp serve` attempts can no
    longer both write the same DB silently."""

    def test_first_acquire_succeeds(self, tmp_path):
        from bpp.utils.serving_lock import acquire_lock

        wd = str(tmp_path)
        result = acquire_lock(wd)
        assert result is None, "First acquire must succeed (return None)"
        with open(lock_path(wd)) as f:
            assert f.read().strip() == str(os.getpid())

    def test_second_acquire_with_live_pid_fails(self, tmp_path, monkeypatch):
        """The actual safety property: a second acquire while another
        live PID owns the lock returns that PID (caller refuses to
        start). We simulate "another live PID" by writing a different
        PID and patching os.kill to treat it as alive."""
        from bpp.utils.serving_lock import acquire_lock

        wd = str(tmp_path)
        other_pid = os.getpid() + 1  # Pretend a different process owns it
        with open(lock_path(wd), "w") as f:
            f.write(str(other_pid))

        # Patch kill so other_pid registers as alive
        original_kill = os.kill

        def _kill_fake(pid, sig):
            if pid == other_pid and sig == 0:
                return  # alive
            return original_kill(pid, sig)

        monkeypatch.setattr(os, "kill", _kill_fake)

        result = acquire_lock(wd)
        assert result == other_pid, (
            f"Second acquire with live foreign PID must return that PID, "
            f"got {result}. Without this the two-server race goes silent."
        )

    def test_stale_lockfile_recovered(self, tmp_path):
        """A lockfile from a crashed server (PID no longer alive)
        must not permanently block startup. acquire_lock should
        atomically replace it with our PID."""
        from bpp.utils.serving_lock import acquire_lock

        wd = str(tmp_path)
        # Simulate a stale lockfile with an unlikely PID
        with open(lock_path(wd), "w") as f:
            f.write("99999999")

        result = acquire_lock(wd)
        assert result is None, "Stale lock must be recovered"
        with open(lock_path(wd)) as f:
            assert f.read().strip() == str(os.getpid())

    def test_corrupt_lockfile_treated_as_stale(self, tmp_path):
        """A lockfile with non-numeric content was probably written
        partially or by a buggy version. acquire_lock recovers."""
        from bpp.utils.serving_lock import acquire_lock

        wd = str(tmp_path)
        with open(lock_path(wd), "w") as f:
            f.write("not-a-pid")

        result = acquire_lock(wd)
        assert result is None
        with open(lock_path(wd)) as f:
            assert f.read().strip() == str(os.getpid())

    def test_missing_workdir_raises_servinglock_error(self, tmp_path):
        """R4-M4 inversion of the previous test: missing workdir was
        a silent return None (caller proceeded WITHOUT a lock). Now
        it's a ServingLockError so startup refuses."""
        from bpp.utils.serving_lock import ServingLockError, acquire_lock

        with pytest.raises(ServingLockError, match="does not exist"):
            acquire_lock(str(tmp_path / "nonexistent"))

    def test_acquire_then_clear_then_reacquire(self, tmp_path):
        """clean shutdown -> next startup reacquires fine."""
        from bpp.utils.serving_lock import acquire_lock

        wd = str(tmp_path)
        assert acquire_lock(wd) is None
        clear_lock(wd)
        assert acquire_lock(wd) is None

    def test_empty_workdir_raises_lock_error(self):
        from bpp.utils.serving_lock import ServingLockError, acquire_lock

        with pytest.raises(ServingLockError, match="empty workdir"):
            acquire_lock("")

    def test_os_error_on_open_raises(self, tmp_path, monkeypatch):
        """R4-M4: a non-FileExistsError OSError on os.open (read-only
        FS, EACCES, etc.) used to silently return None and let the
        caller proceed without a lock. Now must raise ServingLockError
        so startup refuses."""
        from bpp.utils.serving_lock import ServingLockError, acquire_lock

        wd = str(tmp_path)

        def _fail_open(*a, **kw):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr("bpp.utils.serving_lock.os.open", _fail_open)

        with pytest.raises(ServingLockError, match="OS error"):
            acquire_lock(wd)

    def test_stale_recovery_loser_does_not_double_acquire(self, tmp_path, monkeypatch):
        """D-03: the actual race Codex caught. Two contenders both
        see the same stale lockfile; both write their tmp; both call
        os.replace (which can't fail in this scenario — it just
        overwrites). With the old code, both returned success.

        Fix: after os.replace, verify the lockfile contains OUR PID.
        If a different PID is there, the other process won the race
        and we report them as the holder (caller refuses to start).

        Simulate the race by making os.replace a no-op for the
        loser (their content gets immediately overwritten by the
        winner). The loser must detect this and return the winner's PID.
        """
        from bpp.utils.serving_lock import acquire_lock

        wd = str(tmp_path)
        winner_pid = os.getpid() + 999  # Pretend a sibling process

        # Plant a stale lock so both contenders go down the recovery path
        with open(lock_path(wd), "w") as f:
            f.write("99999999")  # unlikely-to-be-alive PID

        # After we call os.replace(tmp, path), the file's content gets
        # overwritten by the "winner" — simulate by patching os.replace
        # to write the winner's PID instead of our temp content.
        def _winning_replace(src, dst):
            # Discard our tmp, plant winner's PID
            import contextlib

            with contextlib.suppress(OSError):
                os.remove(src)
            with open(dst, "w") as f:
                f.write(str(winner_pid))

        monkeypatch.setattr(os, "replace", _winning_replace)

        # And make kill think the winner is alive (so the post-replace
        # verification sees a live PID, not a stale one)
        original_kill = os.kill

        def _kill_fake(pid, sig):
            if pid == winner_pid and sig == 0:
                return
            return original_kill(pid, sig)

        monkeypatch.setattr(os, "kill", _kill_fake)

        result = acquire_lock(wd)
        assert result == winner_pid, (
            f"Loser must report the winner's PID, got {result}. "
            "Without this verification, both contenders would return "
            "None (success) and two servers would silently corrupt the DB."
        )
