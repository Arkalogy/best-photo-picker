"""Shared pieces for the in-app installer tests: a scripted pip, and
fixtures that reset the installer's module state between tests."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest

from bpp.web import bp_install

#: The real install list (the fixtures below swap in a test one).
REAL_INSTALLABLE_PACKAGES = dict(bp_install._INSTALLABLE_PACKAGES)


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
def _reset_install_lock(monkeypatch, tmp_path):
    """Ensure each test starts with the global install flag clear."""
    monkeypatch.setattr(bp_install, "_claim", None)
    monkeypatch.setattr(bp_install, "_last_token", 0)
    monkeypatch.setattr(bp_install, "_long_work", [])
    # The "install in progress" note goes next to the real environment.
    from bpp.web import pip_process

    monkeypatch.setattr(pip_process, "MARKER_DIR", tmp_path)
    monkeypatch.setattr(pip_process, "_live", None)
    monkeypatch.setattr(pip_process, "_running_cache", {})
    monkeypatch.setattr(pip_process, "_notes_cache", None)
    monkeypatch.setattr(
        bp_install, "_INSTALLABLE_PACKAGES", {"test-pkg": ["test-pkg-spec"]}, raising=False
    )
    # Nothing held for a restart outside the test.
    from bpp.scoring import install_hold

    monkeypatch.setattr(install_hold, "_installed", frozenset())
    monkeypatch.setattr(install_hold, "_held", frozenset())
    yield


def claim(key: str = "test-pkg") -> bp_install._Claim:
    """Claim an install as POST /install/<key> does (its checks skipped)."""
    import time

    bp_install._last_token += 1
    bp_install._claim = bp_install._Claim(
        key=key, token=bp_install._last_token, claimed_at=time.monotonic()
    )
    return bp_install._claim


def _drain(generator):
    """Consume an SSE generator into a list of payload strings."""
    return list(generator)


def _event(payload: str) -> dict:
    return json.loads(payload.removeprefix("data: "))


@pytest.fixture
def app_context():
    """jsonify() needs one; these tests call the endpoint bodies directly."""
    from flask import Flask

    with Flask(__name__).app_context():
        yield


def run_install(monkeypatch, returncode=0, *, stop=False, key="test-pkg"):
    """Run an install through the real progress stream with a fake pip."""
    proc = _MockPopen(["Installing collected packages: x\n", "ok\n"], returncode=returncode)
    if stop:  # the app cut it off (quit, deadline, closed page)
        proc.bpp_stopped = True
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    claim(key)
    _drain(bp_install._api_install_progress(key).response)
