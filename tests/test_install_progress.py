"""Subprocess lifecycle tests for /api/install/<key>/progress.

The SSE endpoint streams pip output to the client. If the client
disconnects mid-stream OR pip itself stalls, the previous code left
the subprocess holding an open stdout pipe and no timeout — orphan
process risk. These tests pin the post-fix lifecycle:

  - normal completion: returncode propagated, no terminate() call
  - early generator close (client disconnect): terminate() invoked,
    stdout closed, the install claim released
  - hard timeout exceeded: TimeoutError raised, process killed, error
    surfaced to client via SSE

A finished install always asks for a restart: what pip wrote is never
loaded into the running app (bpp.scoring.install_hold).
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from bpp.scoring import install_hold
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


def test_normal_completion_yields_done_and_clears_flag(monkeypatch):
    mock_proc = _MockPopen(stdout_lines=["Collecting test-pkg\n", "Installed.\n"])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: mock_proc)
    claim()

    resp = bp_install._api_install_progress("test-pkg")
    payloads = _drain(resp.response)

    assert any("'type': 'start'" in p or '"type": "start"' in p for p in payloads)
    assert any("'type': 'done'" in p or '"type": "done"' in p for p in payloads)
    assert bp_install._claim is None, "flag must clear on normal completion"
    # Normal completion: stdout closed by finally block.
    assert mock_proc.stdout.closed is True


def test_client_disconnect_terminates_subprocess(monkeypatch):
    mock_proc = _MockPopen(stdout_lines=[f"line {i}\n" for i in range(100)])
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: mock_proc)
    claim()

    resp = bp_install._api_install_progress("test-pkg")
    gen = resp.response
    # Consume two payloads — 'start' (yielded before Popen), then pip's
    # first 'log' line (inside the stdout loop, AFTER Popen is created).
    # Closing here puts proc in a state where the cleanup path is meaningful.
    it = iter(gen)
    next(it)
    next(it)
    gen.close()

    assert mock_proc._terminated is True, (
        "client disconnect must trigger proc.terminate() so pip doesn't orphan"
    )
    assert mock_proc.stdout.closed is True, "stdout must be closed on disconnect"
    assert bp_install._claim is None, (
        "the claim must clear even on disconnect — otherwise the next "
        "install attempt is locked out forever"
    )


def test_a_finished_install_asks_for_a_restart(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: _MockPopen(["Installing collected packages: x\n", "ok\n"]),
    )
    claim()
    payloads = _drain(bp_install._api_install_progress("test-pkg").response)
    assert _event(payloads[-1]) == {"type": "done", "restart_required": True}


def test_a_failed_install_still_waits_for_the_restart(monkeypatch):
    # pip rolls back only the package that failed: what it installed before
    # it (scipy, mediapipe…) stays — not to be loaded into this process.
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: _MockPopen(
            ["Installing collected packages: a, b\n", "ERROR: no wheel\n"], returncode=1
        ),
    )
    claim()
    error = _event(_drain(bp_install._api_install_progress("test-pkg").response)[-1])
    assert error["type"] == "error" and "no wheel" in error["message"]
    assert error["restart_required"] is True
    assert install_hold.held_keys() == {"test-pkg"}


def test_a_stopped_install_keeps_waiting_for_the_restart(monkeypatch):
    proc = _MockPopen(["Installing collected packages: x\n"], returncode=1)
    proc.bpp_stopped = True  # cut off (closed page, deadline): half written
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    claim()
    error = _event(_drain(bp_install._api_install_progress("test-pkg").response)[-1])
    assert error["restart_required"] is True
    assert install_hold.held_keys() == {"test-pkg"}


def test_no_package_loading_work_until_the_restart():
    # The installed feature's models would silently be missing otherwise.
    from bpp.errors import ConflictError
    from bpp.web.base_worker import BackgroundWorker
    from bpp.web.export_worker import ExportWorker

    class _Analysis(BackgroundWorker):
        def _run(self, *args):
            pass

    install_hold.hold_until_restart("test-pkg")
    with pytest.raises(ConflictError, match="Restart Best Photo Picker"):
        _Analysis()._start_thread()
    export = ExportWorker()
    export._start_thread_unguarded = lambda *a: True  # exports load nothing installable
    assert export._start_thread() is True


def test_what_pip_writes_is_held_from_the_moment_it_starts(monkeypatch):
    monkeypatch.setitem(install_hold.FEATURE_MODULES, "test-pkg", frozenset({"pkg_written"}))
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: _MockPopen(["Installing collected packages: x\n"])
    )
    claim()
    held: list[bool] = []
    for payload in bp_install._api_install_progress("test-pkg").response:
        if _event(payload)["type"] == "log":  # pip is running
            held.append(install_hold.held_for_restart("pkg_written"))
    assert held == [True], "a status check mid-pip must not import half-written files"
    assert install_hold.held_for_restart("pkg_written"), "nor after: it loads after a restart"
    assert install_hold.held_keys() == {"test-pkg"}


def test_a_pip_that_hangs_is_stopped_and_reported(monkeypatch):
    proc = _MockPopen(
        ["Installing collected packages: x\n", "Uninstalling numpy\n"], hang_after_stdout=True
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    claim()
    error = _event(_drain(bp_install._api_install_progress("test-pkg").response)[-1])
    assert proc._killed or proc._terminated
    assert (error["type"], error["restart_required"]) == ("error", True)
    assert bp_install._claim is None


def test_the_install_state_names_the_feature_waiting():
    from bpp.web.install_status import install_state

    install_hold.hold_until_restart("inpaint")
    state = install_state()
    assert state["restart_pending"] == ["inpaint"]


def test_one_install_per_restart(monkeypatch, app_context):
    from bpp.errors import ConflictError

    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    install_hold.hold_until_restart("inpaint")
    assert bp_install.install_refusal() is not None
    with pytest.raises(ConflictError, match="Restart Best Photo Picker"):
        bp_install._api_install_package("test-pkg")
    assert bp_install._claim is None


def test_nothing_is_held_if_pip_never_starts(monkeypatch):
    # The tab closed right after "start", or pip couldn't be launched.
    def no_pip(*a, **k):
        raise OSError("too many open files")

    monkeypatch.setattr(subprocess, "Popen", no_pip)
    claim()
    _drain(bp_install._api_install_progress("test-pkg").response)
    assert install_hold.held_keys() == frozenset()


def test_the_hold_reaches_the_analysis_processes(monkeypatch):
    import os
    import subprocess as sp

    install_hold.hold_until_restart("faces")
    from pathlib import Path

    held_file = Path(os.environ["BPP_INSTALL_HELD_FILE"])
    assert "scipy" in held_file.read_text().split(",")
    # A fresh process (as analysis starts) holds the same packages.
    out = sp.run(
        [
            sys.executable,
            "-c",
            "from bpp.scoring import install_hold as h; print(h.held_for_restart('dlib'))",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.strip()
    assert out == "True"


def test_no_install_while_analysis_runs(monkeypatch):
    from bpp.errors import ConflictError

    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: ["analysis"])
    with pytest.raises(ConflictError, match="Wait for analysis to finish"):
        bp_install._api_install_package("test-pkg")
    assert bp_install._claim is None


def test_no_analysis_while_an_install_runs():
    from bpp.errors import ConflictError

    claim()
    with (
        pytest.raises(ConflictError, match="being installed"),
        bp_install._worker_start_guard(None),
    ):
        pass
    bp_install._claim = None
    with bp_install._worker_start_guard(None):
        pass


def test_no_worker_starts_while_an_install_runs():
    # In the worker start itself: every worker (plugins' included) and
    # every way of starting one is covered.
    from bpp.errors import ConflictError
    from bpp.web.base_worker import BackgroundWorker

    class _PluginWorker(BackgroundWorker):
        def _run(self, *args):
            pass

    worker = _PluginWorker()
    claim()
    with pytest.raises(ConflictError, match="being installed"):
        worker._start_thread()
    bp_install._claim = None
    assert worker._start_thread() is True
    worker._thread.join(2)


def test_a_worker_stopped_by_a_held_package_says_so_plainly():
    from bpp.scoring.install_hold import HeldForRestart
    from bpp.web.base_worker import _install_wait_message

    held = HeldForRestart("x", name="scipy.cluster.hierarchy")
    install_hold.hold_during_install("faces")
    assert "being installed — wait" in _install_wait_message("Face grouping", held)
    install_hold.hold_until_restart("faces")
    message = _install_wait_message("Face grouping", held)
    assert "a package was just installed — restart" in message
    assert "scipy" not in message  # paired devices see the stream too
    assert _install_wait_message("Analysis", ValueError("x")) is None


def test_an_installed_face_feature_waits_for_the_restart(tmp_path, monkeypatch):
    from bpp.scoring import face_recognition_loader as loader

    pkg = tmp_path / "face_recognition"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("WORKS = True\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "face_recognition", raising=False)
    loader._reset_for_tests()
    try:
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *a, **k: _MockPopen(["Installing collected packages: x\n", "ok\n"]),
        )
        monkeypatch.setitem(bp_install._INSTALLABLE_PACKAGES, "faces", ["face_recognition"])
        claim("faces")
        _drain(bp_install._api_install_progress("faces").response)
        module, why = loader.load_face_recognition_or_reason()
        assert module is None and why == install_hold.RESTART_REASON
        assert "face_recognition" not in sys.modules, "never loaded into this process"
    finally:
        sys.modules.pop("face_recognition", None)
        loader._reset_for_tests()


def test_the_app_status_says_what_waits_for_the_restart(tmp_path, monkeypatch):
    from bpp.web.app import create_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    install_hold.hold_until_restart("faces")
    lib = tmp_path / "lib"
    lib.mkdir()
    app = create_app(workdir=str(lib), library_path=str(lib))
    app.config["TESTING"] = True
    body = app.test_client().get("/api/v1/status").get_json()
    assert body["install_restart_pending"] == ["faces"]
    assert body["install_interrupted"] == []
    assert body["face_installable"] is False  # installed: restart, don't reinstall


def test_an_ai_removal_install_holds_up_imports_not_exports():
    # Its pip run may upgrade numpy, which an import's processes load fresh
    # from disk; an export loads none of it.
    from bpp.errors import ConflictError
    from bpp.web.export_worker import ExportWorker
    from bpp.web.import_worker import ImportWorker

    install_hold.hold_until_restart("inpaint")
    with pytest.raises(ConflictError, match="Restart"):
        ImportWorker()._start_thread()
    export = ExportWorker()
    export._start_thread_unguarded = lambda *a: True
    assert export._start_thread() is True


def test_a_worker_already_running_says_so_not_restart():
    from bpp.web.base_worker import BackgroundWorker

    class _Busy(BackgroundWorker):
        def _run(self, *args):
            pass

    worker = _Busy()
    type(worker).is_alive = property(lambda self: True)
    install_hold.hold_until_restart("faces")
    assert worker._start_thread() is False  # "already running", no ConflictError


def test_an_install_waits_for_a_running_model_download(monkeypatch):
    # (A download's own package checks go through the hold.)
    from bpp.web import model_download_jobs

    monkeypatch.setattr(model_download_jobs, "_jobs", {})
    release = __import__("threading").Event()
    model_download_jobs.start("k", "CLIP model", lambda: release.wait(5) and {})
    try:
        assert "the CLIP model download" in bp_install._busy_workers()
    finally:
        release.set()


def test_a_model_held_for_a_restart_is_not_cached_as_unavailable(tmp_path, monkeypatch):
    # The hold is temporary (lifted if pip wrote nothing): once it's gone
    # the model loads, without any sweep resetting refused models.
    import sys

    from bpp.scoring.model_base import ModelSingleton

    (tmp_path / "pkg_held_model.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "pkg_held_model", raising=False)
    model = ModelSingleton(
        name="test model",
        model_path=None,
        model_url=None,
        create_fn=lambda _p: "model",
        registry_id="openai_clip_vit_b32_onnx",
        import_check=install_hold.requires("pkg_held_model"),
    )
    monkeypatch.setitem(install_hold.FEATURE_MODULES, "test-pkg", frozenset({"pkg_held_model"}))
    install_hold.hold_until_restart("test-pkg")
    assert model.get() is None
    assert model._available is None, "a hold isn't a verdict to keep"
    install_hold.release("test-pkg")
    assert model.get() == "model"


def test_an_edit_with_a_negative_strength_is_no_blur():
    # Edit values aren't range-checked on save: no crash (scipy's blur, used
    # before, treated it as none).
    import numpy as np

    from bpp.web.photo_edits import _gaussian_filter

    arr = np.random.default_rng(0).random((8, 8, 3)).astype(np.float32)
    assert (_gaussian_filter(arr, -0.6) == arr).all()
    assert (_gaussian_filter(arr, 0.0) == arr).all()


def test_retrying_face_detection_is_refused_before_anything_is_deleted(tmp_path, monkeypatch):
    # The retry wipes faces and person tags: if the extraction can't start
    # (an install waits for a restart), nothing may be wiped.
    from bpp.db import journal
    from bpp.web.app import create_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    lib = tmp_path / "lib"
    lib.mkdir()
    app = create_app(workdir=str(lib))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    monkeypatch.setattr(ctx, "load_analysis_if_needed", lambda: [{"filepath": "x"}])
    monkeypatch.setattr("bpp.web.bp_faces_extract.face_recognition_available", lambda: True)
    wiped: list[str] = []
    monkeypatch.setattr(journal, "journal_start", lambda *a, **k: wiped.append("wipe"))
    install_hold.hold_until_restart("faces")

    resp = app.test_client().post("/api/v1/faces/retry")
    assert resp.status_code == 409
    assert "Restart" in resp.get_json()["error"]
    assert wiped == [], "the wipe ran although the extraction was refused"


def test_pip_that_failed_before_writing_anything_needs_no_restart(monkeypatch, app_context):
    # No pip module, offline, no wheel: it ends before "Installing
    # collected packages" — nothing changed, so nothing waits.
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: _MockPopen(["ERROR: Could not find a version\n"], returncode=1),
    )
    claim()
    error = _event(_drain(bp_install._api_install_progress("test-pkg").response)[-1])
    assert error["restart_required"] is False
    assert install_hold.held_keys() == frozenset()
    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    bp_install._api_install_package("test-pkg")  # can be retried at once


def test_pip_stopped_before_writing_anything_leaves_no_repair(monkeypatch):
    from bpp.web import pip_process

    proc = _MockPopen(["Downloading dlib\n"], returncode=-15)
    proc.bpp_stopped = True
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: proc)
    claim()
    _drain(bp_install._api_install_progress("test-pkg").response)
    assert pip_process.interrupted_installs() == {}
    assert install_hold.held_keys() == frozenset()


def test_an_install_already_there_needs_no_restart(monkeypatch):
    # pip found everything installed: it wrote nothing, nothing waits.
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: _MockPopen(["Requirement already satisfied: x\n"]),
    )
    claim()
    done = _event(_drain(bp_install._api_install_progress("test-pkg").response)[-1])
    assert done == {"type": "done", "restart_required": False}
    assert install_hold.held_keys() == frozenset()


def test_no_restart_is_asked_for_while_pip_is_still_downloading(monkeypatch):
    # The banner (and "restart" refusals) come only once pip has written:
    # a restart now would cut it off.
    from bpp.errors import ConflictError
    from bpp.web.import_worker import ImportWorker

    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *a, **k: _MockPopen(["Collecting x\n", "Installing collected packages: x\n"]),
    )
    claim()
    seen: list[tuple] = []
    for payload in bp_install._api_install_progress("test-pkg").response:
        if _event(payload)["type"] == "log" and not seen:  # still downloading
            seen.append(install_hold.held_keys())
            with pytest.raises(ConflictError, match="being installed"):
                ImportWorker()._start_thread()
    assert seen == [frozenset()]
    assert install_hold.held_keys() == {"test-pkg"}  # written: now it waits
