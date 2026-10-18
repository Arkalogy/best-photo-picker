"""The installer's claim: one install at a time, never alongside the
background work that loads packages, never stuck when a tab closes or
pip goes quiet."""

from __future__ import annotations

import subprocess

import pytest

from bpp.web import bp_install

# Fixtures shared with the other installer tests; pytest injects them by
# parameter name, which ruff reads as redefinitions.
# ruff: noqa: F811
from tests._install_support import (  # noqa: F401
    _drain,
    _event,
    _MockPopen,
    _reset_install_lock,
    app_context,
    claim,
)


def test_a_claim_whose_stream_never_connects_is_released(monkeypatch):
    # The tab closed between POST /install and the progress stream.
    claim()
    bp_install._claim.claimed_at = 0.0  # long ago
    with bp_install._worker_start_guard(None):  # no longer blocks analysis
        pass
    assert bp_install._claim is None


def test_a_streaming_install_is_never_released_early():
    claim()
    bp_install._claim.claimed_at = None  # its stream started
    with pytest.raises(Exception, match="being installed"), bp_install._worker_start_guard(None):
        pass


def test_an_install_and_new_work_never_start_together(monkeypatch, app_context):
    import threading

    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    results: list[str] = []

    def install():
        from flask import Flask

        try:
            with Flask(__name__).app_context():  # contexts are per thread
                bp_install._api_install_package("test-pkg")
            results.append("installed")
        except Exception as exc:
            results.append(type(exc).__name__)

    with bp_install._worker_start_guard(None):  # work is starting
        other = threading.Thread(target=install)
        other.start()
        other.join(0.2)
        assert other.is_alive(), "the install must wait for the work to start"
    other.join(2)
    assert results == ["installed"]


def test_a_second_stream_for_the_same_install_is_refused(monkeypatch, app_context):
    from bpp.errors import ConflictError

    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _MockPopen(["ok\n"]))
    bp_install._api_install_package("test-pkg")
    first = iter(bp_install._api_install_progress("test-pkg").response)
    next(first)  # its stream has started: it runs this install's pip
    with pytest.raises(ConflictError, match="already running"):
        bp_install._api_install_progress("test-pkg")  # another tab
    _drain(first)
    assert bp_install._claim is None


def test_of_two_streams_answered_at_once_only_the_first_runs_pip(monkeypatch, app_context):
    # Both GETs answered before either stream started (a reconnect): the
    # stream is taken when it starts, so the second says so, runs nothing.
    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    popens: list[int] = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: popens.append(1) or _MockPopen(["ok\n"])
    )
    bp_install._api_install_package("test-pkg")
    first = iter(bp_install._api_install_progress("test-pkg").response)
    second = bp_install._api_install_progress("test-pkg").response
    next(first)
    assert "already running" in _event(_drain(second)[-1])["message"]
    _drain(first)
    assert popens == [1]


def test_a_stream_never_read_does_not_keep_the_install_taken(monkeypatch, app_context):
    # The client left before reading it: a reconnect can still run it.
    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _MockPopen(["ok\n"]))
    bp_install._api_install_package("test-pkg")
    bp_install._api_install_progress("test-pkg")  # answered, never read
    assert (
        _event(_drain(bp_install._api_install_progress("test-pkg").response)[-1])["type"] == "done"
    )


def test_a_stream_for_another_key_is_refused(monkeypatch, app_context):
    from bpp.errors import ConflictError

    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    monkeypatch.setattr(bp_install, "_INSTALLABLE_PACKAGES", {"test-pkg": ["a"], "other": ["b"]})
    bp_install._api_install_package("test-pkg")
    with pytest.raises(ConflictError, match="POST /install"):
        bp_install._api_install_progress("other")


class _ImmediateTimer:
    """A watchdog that fires as soon as it's started, on this thread."""

    def __init__(self, _interval, fn):
        self._fn = fn
        self.daemon = True

    def start(self):
        self._fn()

    def cancel(self):
        pass


class _SilentPip:
    """A pip that prints nothing until it's stopped (dlib building)."""

    def __init__(self):
        import threading

        self._stopped = threading.Event()
        self.returncode = None
        self.stdout = self

    def __iter__(self):
        self._stopped.wait(5)  # blocks like a pipe with no output
        return iter(())

    closed = False

    def close(self):
        self.closed = True

    def terminate(self):
        self.returncode = -15
        self._stopped.set()

    kill = terminate

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def test_a_pip_that_goes_quiet_is_stopped_at_the_deadline(monkeypatch):
    import time

    proc = _SilentPip()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(bp_install.threading, "Timer", _ImmediateTimer)
    claim()
    started = time.monotonic()
    payloads = _drain(bp_install._api_install_progress("test-pkg").response)
    assert time.monotonic() - started < 3, "the deadline can't wait for pip's next line"
    error = _event(payloads[-1])
    assert error["type"] == "error"
    assert "didn't finish within" in error["message"]  # the reason
    assert "pip install test-pkg-spec" in error["message"]  # and a way forward
    assert bp_install._claim is None, "analysis must not stay blocked"


def test_the_claim_is_released_before_the_page_hears_done(monkeypatch):
    # The page starts analysis the moment it hears "done".
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _MockPopen(["ok\n"]))
    claim()
    for payload in bp_install._api_install_progress("test-pkg").response:
        if _event(payload)["type"] == "done":
            assert bp_install._claim is None
            break
    else:
        pytest.fail("no done event")


def test_a_stream_whose_claim_expired_meanwhile_runs_no_pip(monkeypatch, app_context):
    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    popens: list[int] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popens.append(1))
    bp_install._api_install_package("test-pkg")
    stream = bp_install._api_install_progress("test-pkg")  # GET arrived at ~29.9 s
    bp_install._claim.claimed_at = 0.0
    with bp_install._worker_start_guard(None):  # another request releases it as stale
        pass
    payloads = _drain(stream.response)
    assert popens == [], "pip must not run without the claim"
    assert "took too long to start" in _event(payloads[-1])["message"]


def test_a_pip_that_finished_as_the_deadline_hit_succeeded(monkeypatch):
    proc = _MockPopen(["ok\n"])
    proc._poll_alive = False  # already exited 0 when the deadline fires
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(bp_install.threading, "Timer", _ImmediateTimer)
    claim()
    payloads = _drain(bp_install._api_install_progress("test-pkg").response)
    assert proc._terminated is False, "nothing to stop"
    assert _event(payloads[-1])["type"] == "done"


def test_a_pip_stopped_at_the_deadline_is_reported_as_timed_out(monkeypatch):
    # Still running; stopped by SIGTERM, as a real pip reports it.
    proc = _MockPopen(["Building wheel for dlib\n"], returncode=-15)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(bp_install.threading, "Timer", _ImmediateTimer)
    claim()
    payloads = _drain(bp_install._api_install_progress("test-pkg").response)
    assert proc._terminated is True
    assert "didn't finish within" in _event(payloads[-1])["message"]


def test_the_timeout_hint_is_a_command_a_shell_can_run(monkeypatch):
    import shlex
    import sys

    monkeypatch.setattr(
        bp_install,
        "_INSTALLABLE_PACKAGES",
        {"test-pkg": ["setuptools<81", "x>=1; sys_platform != 'darwin'"]},
    )
    proc = _MockPopen(["Building\n"], returncode=-15)  # stopped by SIGTERM
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(bp_install.threading, "Timer", _ImmediateTimer)
    claim()
    message = _event(_drain(bp_install._api_install_progress("test-pkg").response)[-1])["message"]
    command = message.split("with: ", 1)[1]
    assert shlex.split(command) == [
        sys.executable,  # this app's Python, not whatever "pip" is first on PATH
        "-m",
        "pip",
        "install",
        "setuptools<81",
        "x>=1; sys_platform != 'darwin'",
    ]


def test_nothing_is_held_until_pip_actually_starts(monkeypatch, app_context):
    # The tab may close before the progress stream opens: then nothing runs,
    # so nothing should be kept from loading.
    from bpp.scoring import install_hold as load_failures

    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    monkeypatch.setitem(load_failures.FEATURE_MODULES, "test-pkg", frozenset({"pkg_not_yet"}))
    bp_install._api_install_package("test-pkg")
    assert load_failures.held_for_restart("pkg_not_yet") is False


def test_a_finished_install_leaves_the_next_one_alone(monkeypatch, app_context):
    from bpp.scoring import install_hold as load_failures

    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    monkeypatch.setitem(load_failures.FEATURE_MODULES, "test-pkg", frozenset({"pkg_b"}))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _MockPopen(["ok\n"]))
    bp_install._api_install_package("test-pkg")
    first = iter(bp_install._api_install_progress("test-pkg").response)
    for payload in first:
        if _event(payload)["type"] == "done":
            break  # A told the page it's done; A's teardown hasn't run yet
    # (One install per restart: let B through as if the app had restarted.)
    monkeypatch.setattr(load_failures, "_installed", frozenset())
    bp_install._api_install_package("test-pkg")  # B claims
    second = iter(bp_install._api_install_progress("test-pkg").response)
    next(second)  # B's "start"
    next(second)  # B's pip is running (its first line)
    from bpp.web import pip_process

    live_b = pip_process._live
    assert live_b is not None
    _drain(first)  # A's teardown
    assert bp_install._claim is not None, "A must not release B's claim"
    assert pip_process._live is live_b, "nor forget B's running pip"
    _drain(second)


def test_busy_means_running_work_that_loads_installable_packages(monkeypatch):
    # Any install may upgrade shared libraries (numpy…) such work imports;
    # work that loads none of them (an export) doesn't count.
    from bpp.web import model_download_jobs, state

    def worker(alive, loads):
        return type("W", (), {"is_alive": alive, "loads_installable_packages": loads})()

    class _Ctx:
        class workers:
            @staticmethod
            def items():
                return [
                    ("analyze", worker(False, True)),
                    ("clip", worker(True, True)),
                    ("export", worker(True, False)),
                    ("my_plugin", worker(True, None)),
                ]

    monkeypatch.setattr(state, "get_ctx_or_none", lambda: _Ctx())
    monkeypatch.setattr(model_download_jobs, "running_labels", lambda: ["the CLIP download"])
    assert bp_install._busy_workers() == [
        "the CLIP download",
        "smart search indexing",
        "my_plugin",
    ]


def test_no_install_offered_while_one_runs(monkeypatch):
    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    assert bp_install.install_refusal() is None
    claim()
    assert bp_install.install_refusal() is not None


def test_stopping_pip_stops_the_compilers_it_started():
    import os
    import time

    if os.name != "posix":
        pytest.skip("process groups are POSIX")
    # A pip stand-in that started a long child (a compiler), in its own group.
    proc = subprocess.Popen(["/bin/sh", "-c", "sleep 30 & wait"], start_new_session=True, text=True)
    time.sleep(0.2)
    from bpp.web import pip_process

    pip_process.signal_group(proc, kill=False)
    proc.wait(5)
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.killpg(proc.pid, 0)  # signal 0 only probes: nothing left in the group


def test_no_library_switch_while_an_install_runs(tmp_path, monkeypatch):
    # Opening a library starts recovery work that loads packages.
    from bpp.web.app import create_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    lib, other = tmp_path / "lib", tmp_path / "other"
    lib.mkdir()
    other.mkdir()
    app = create_app(workdir=str(lib), library_path=str(lib))
    app.config["TESTING"] = True
    claim()
    resp = app.test_client().post("/api/v1/libraries/switch", json={"path": str(other)})
    assert resp.status_code == 409
    assert "being installed" in resp.get_json()["error"]


def test_shutting_down_stops_a_running_pip(monkeypatch):
    from bpp.web import pip_process

    proc = _MockPopen(["x\n"])
    pip_process.set_live(proc)
    try:
        pip_process.stop_running_pip()
        assert proc._terminated is True
    finally:
        pip_process.set_live(None)
