"""Unit tests for the serving-lock helper.

The helper is small but load-bearing for `bpp db restore-backup`'s
running-server guard. Cover the three states explicitly: no
lockfile, alive PID, stale PID, and the garbage / empty /
non-existent edge cases.
"""

from __future__ import annotations

import os
import time

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
        """Inversion of the previous test: missing workdir was
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
        """A non-FileExistsError OSError on os.open (read-only
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

    def test_a_process_paused_before_writing_its_pid_still_holds_the_lock(self, tmp_path):
        """The window the PID text alone can't cover: one process has the
        file but has not written its PID yet. Another must not take it."""
        import subprocess
        import sys

        from bpp.utils.serving_lock import acquire_lock

        wd = str(tmp_path)
        paused, go_on = tmp_path / "paused", tmp_path / "go-on"
        script = (
            "import os, sys, time\n"
            "from bpp.utils import serving_lock\n"
            "real = serving_lock._write_pid\n"
            "def slow(fd, path, pid, created):\n"
            "    open(sys.argv[2], 'w').close()\n"
            "    while not os.path.exists(sys.argv[3]): time.sleep(0.01)\n"
            "    real(fd, path, pid, created)\n"
            "serving_lock._write_pid = slow\n"
            "print(serving_lock.acquire_lock(sys.argv[1]), flush=True)\n"
            "sys.stdin.readline()\n"
        )
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        first = subprocess.Popen(
            [sys.executable, "-c", script, wd, str(paused), str(go_on)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            cwd=repo,
        )
        try:
            for _ in range(3000):
                if paused.exists():
                    break
                time.sleep(0.01)
            assert paused.exists(), "the first process never reached its write"

            second = acquire_lock(wd)

            go_on.write_text("")
            assert first.stdout.readline().strip() == "None", "the first holds it"
        finally:
            first.communicate("\n", timeout=30)
        assert second is not None, "two processes held the lock at once"

    def test_a_holder_that_dies_lets_go(self, tmp_path):
        import signal
        import subprocess
        import sys

        from bpp.utils.serving_lock import acquire_lock

        wd = str(tmp_path)
        script = (
            "import sys\n"
            "from bpp.utils.serving_lock import acquire_lock\n"
            "print(acquire_lock(sys.argv[1]), flush=True)\n"
            "sys.stdin.readline()\n"
        )
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        holder = subprocess.Popen(
            [sys.executable, "-c", script, wd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            cwd=repo,
        )
        assert holder.stdout.readline().strip() == "None"
        assert acquire_lock(wd) == holder.pid, "held while it runs"
        holder.send_signal(signal.SIGKILL)
        holder.wait(30)
        assert acquire_lock(wd) is None, "a crash lets go without anyone removing the file"
        clear_lock(wd)

    def test_where_files_cant_be_locked_a_left_lock_is_not_taken_over(self, tmp_path, monkeypatch):
        from bpp.utils import serving_lock

        monkeypatch.setattr(serving_lock, "try_lock", lambda fd: None)
        wd = str(tmp_path)
        with open(lock_path(wd), "w") as f:
            f.write("99999999")  # a run that ended, or one still starting

        assert serving_lock.acquire_lock(wd) == 0
        assert "remove " + lock_path(wd) in serving_lock.lock_advice(wd, 0)
        os.remove(lock_path(wd))
        assert serving_lock.acquire_lock(wd) is None, "made by this process: held"
        clear_lock(wd)
        assert not os.path.exists(lock_path(wd))

    def test_a_live_holder_is_never_told_to_remove_its_lock(self, tmp_path):
        import subprocess
        import sys

        from bpp.utils.serving_lock import acquire_lock, lock_advice

        wd = str(tmp_path)
        script = (
            "import sys\n"
            "from bpp.utils.serving_lock import acquire_lock\n"
            "print(acquire_lock(sys.argv[1]), flush=True)\n"
            "sys.stdin.readline()\n"
        )
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        holder = subprocess.Popen(
            [sys.executable, "-c", script, wd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            cwd=repo,
        )
        try:
            assert holder.stdout.readline().strip() == "None"
            advice = lock_advice(wd, acquire_lock(wd))
        finally:
            holder.communicate("\n", timeout=30)
        assert "wait for it to finish" in advice and "remove" not in advice

    def test_a_holder_with_this_processs_pid_is_still_another_holder(self, tmp_path):
        """In containers each app can be PID 1: the system's lock, not the
        PID written in the file, says who holds it."""
        from bpp.utils.serving_lock import acquire_lock

        wd = str(tmp_path)
        holder = _hold_in_another_process(wd)
        try:
            with open(lock_path(wd), "w") as f:
                f.write(str(os.getpid()))  # the same PID, in its own namespace
            assert acquire_lock(wd) is not None
        finally:
            holder.communicate("\n", timeout=30)

    def test_clearing_leaves_a_lock_another_process_is_still_starting_with(self, tmp_path):
        import subprocess
        import sys

        wd = str(tmp_path)
        script = (
            "import fcntl, os, sys\n"
            "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"  # locked, its PID not written yet
            "print('LOCKED', flush=True)\n"
            "sys.stdin.readline()\n"
        )
        starting = subprocess.Popen(
            [sys.executable, "-c", script, lock_path(wd)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert starting.stdout.readline().strip() == "LOCKED"
            clear_lock(wd)
            assert os.path.exists(lock_path(wd))
        finally:
            starting.communicate("\n", timeout=30)

    def test_a_lock_whose_file_was_removed_meanwhile_is_taken_again(self, tmp_path):
        from bpp.utils.serving_lock import acquire_lock

        wd = str(tmp_path)
        assert acquire_lock(wd) is None
        os.remove(lock_path(wd))  # removed by hand while held

        assert acquire_lock(wd) is None
        assert os.path.exists(lock_path(wd)), "held for real, not only on record"
        clear_lock(wd)

    def test_a_lock_that_could_not_be_written_is_not_left_behind(self, tmp_path, monkeypatch):
        import errno

        from bpp.utils import serving_lock

        wd = str(tmp_path)
        monkeypatch.setattr(serving_lock, "try_lock", lambda fd: None)  # a drive without locks

        def full(fd):
            raise OSError(errno.ENOSPC, "No space left on device")

        with monkeypatch.context() as m:
            m.setattr(os, "fsync", full)
            with pytest.raises(serving_lock.ServingLockError):
                serving_lock.acquire_lock(wd)

        assert not os.path.exists(lock_path(wd)), "it would keep every later start out"
        assert serving_lock.acquire_lock(wd) is None
        clear_lock(wd)


def _hold_in_another_process(wd):
    import subprocess
    import sys

    script = (
        "import sys\n"
        "from bpp.utils.serving_lock import acquire_lock\n"
        "print(acquire_lock(sys.argv[1]), flush=True)\n"
        "sys.stdin.readline()\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", script, wd],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert holder.stdout.readline().strip() == "None"
    return holder
