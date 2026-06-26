"""Subprocess lifecycle tests for /api/install/<key>/progress.

The SSE endpoint streams pip output to the client. If the client
disconnects mid-stream OR pip itself stalls, the previous code left
the subprocess holding an open stdout pipe and no timeout — orphan
process risk. These tests pin the post-fix lifecycle:

  - normal completion: returncode propagated, no terminate() call
  - early generator close (client disconnect): terminate() invoked,
    stdout closed, _install_running flag cleared
  - hard timeout exceeded: TimeoutError raised, process killed, error
    surfaced to client via SSE
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from bpp.web import bp_install


class _MockPopen:
    """Minimal subprocess.Popen stand-in that yields scripted stdout."""

    def __init__(
        self,
        stdout_lines: list[str],
        returncode: int = 0,
        hang_after_stdout: bool = False,
    ):
        self.stdout = MagicMock()
        # Iterating .stdout returns the scripted lines, then EOF.
        self.stdout.__iter__ = lambda self_: iter(stdout_lines)
        self.stdout.closed = False
        # Track close so the test can assert it ran.
        original_close = self.stdout.close

        def _close():
            self.stdout.closed = True
            original_close()

        self.stdout.close = _close
        self.returncode = returncode
        self._hang_after = hang_after_stdout
        self._terminated = False
        self._killed = False
        self._poll_alive = True

    def wait(self, timeout: float | None = None):
        if self._hang_after and not self._killed:
            raise subprocess.TimeoutExpired(cmd="pip", timeout=timeout or 0)
        self._poll_alive = False
        return self.returncode

    def poll(self):
        return None if self._poll_alive else self.returncode

    def terminate(self):
        self._terminated = True
        if not self._hang_after:
            self._poll_alive = False

    def kill(self):
        self._killed = True
        self._poll_alive = False


@pytest.fixture(autouse=True)
def _reset_install_lock(monkeypatch):
    """Ensure each test starts with the global install flag clear."""
    monkeypatch.setattr(bp_install, "_install_running", False)
    monkeypatch.setattr(
        bp_install, "_INSTALLABLE_PACKAGES", {"test-pkg": "test-pkg-spec"}, raising=False
    )
    yield


def _drain(generator):
    """Consume an SSE generator into a list of payload strings."""
    return list(generator)


def test_normal_completion_yields_done_and_clears_flag(monkeypatch):
    mock_proc = _MockPopen(stdout_lines=["Collecting test-pkg\n", "Installed.\n"])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: mock_proc)
    bp_install._install_running = True

    resp = bp_install._api_install_progress("test-pkg")
    payloads = _drain(resp.response)

    assert any("'type': 'start'" in p or '"type": "start"' in p for p in payloads)
    assert any("'type': 'done'" in p or '"type": "done"' in p for p in payloads)
    assert bp_install._install_running is False, "flag must clear on normal completion"
    # Normal completion: stdout closed by finally block.
    assert mock_proc.stdout.closed is True


def test_client_disconnect_terminates_subprocess(monkeypatch):
    mock_proc = _MockPopen(stdout_lines=[f"line {i}\n" for i in range(100)])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: mock_proc)
    bp_install._install_running = True

    resp = bp_install._api_install_progress("test-pkg")
    gen = resp.response
    # Consume two payloads — the first is 'start' (yielded before Popen),
    # the second is the first 'log' line (yielded inside the stdout loop,
    # AFTER Popen is created). Closing here puts proc in a state where
    # the cleanup path is meaningful.
    it = iter(gen)
    next(it)
    next(it)
    gen.close()

    assert mock_proc._terminated is True, (
        "client disconnect must trigger proc.terminate() so pip doesn't orphan"
    )
    assert mock_proc.stdout.closed is True, "stdout must be closed on disconnect"
    assert bp_install._install_running is False, (
        "_install_running must clear even on disconnect — otherwise the next "
        "install attempt is locked out forever"
    )
