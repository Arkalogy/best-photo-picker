"""Stopping pip (and the compilers it started), and noticing an install
the app quit in the middle of."""

from __future__ import annotations

import os
import subprocess

import pytest

from bpp.web import bp_install, pip_process
from tests._install_support import (  # noqa: F401  (autouse fixtures)
    REAL_INSTALLABLE_PACKAGES,
    _drain,
    _event,
    _MockPopen,
    _reset_install_lock,
    claim,
    run_install,
)


def test_nothing_is_sent_to_a_pip_that_already_exited(monkeypatch):
    # Its process (and group) id may already belong to another program.
    signals: list[int] = []
    monkeypatch.setattr(pip_process.os, "killpg", lambda pgid, sig: signals.append(sig))
    proc = _MockPopen(["x\n"])
    proc.pid = 4242
    proc._poll_alive = False  # exited and reaped
    pip_process.stop(proc)
    assert signals == [] and proc._terminated is False and proc._killed is False


def test_a_process_without_its_own_group_is_stopped_directly():
    proc = _MockPopen(["x\n"])  # no pid: e.g. not started in its own session
    pip_process.signal_group(proc, kill=False)
    assert proc._terminated is True


def test_shutting_down_the_app_stops_a_running_pip(tmp_path, monkeypatch):
    from bpp.web import state_lifecycle
    from bpp.web.app import create_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    lib = tmp_path / "lib"
    lib.mkdir()
    app = create_app(workdir=str(lib), library_path=str(lib))
    calls: list[int] = []
    monkeypatch.setattr(pip_process, "stop_running_pip", lambda: calls.append(1))
    state_lifecycle.shutdown(app.extensions["bpp"])
    assert calls == [1]


def _repair(key: str = "test-pkg") -> str | None:
    """The repair the page would show for *key*, or None."""
    return pip_process.interrupted_installs().get(key)


@pytest.mark.parametrize(
    ("returncode", "stop", "interrupted"),
    [(0, False, False), (1, False, False), (-9, False, True), (1, True, True), (0, True, False)],
)
def test_an_install_cut_off_is_remembered_with_its_repair(
    monkeypatch, returncode, stop, interrupted
):
    # (1, stopped) is Windows: a stopped pip exits 1 there, not -15.
    # (0, stopped): it finished fine just as it was stopped.
    run_install(monkeypatch, returncode, stop=stop)
    repair = _repair()
    if interrupted:  # packages may be half replaced
        assert repair is not None and "-m pip install test-pkg-spec" in repair
    else:  # pip ended by itself (and rolls back its own failures)
        assert repair is None


def test_the_repair_command_clears_its_own_note(monkeypatch):
    import shlex

    run_install(monkeypatch, -9)
    repair = _repair()
    assert repair is not None
    rm = shlex.split(repair.split(" && ", 1)[1])
    assert rm[:2] == ["rm", "-f"]
    os.remove(rm[2])  # what the shell does after a successful pip
    assert _repair() is None


def test_another_install_does_not_clear_the_warning(monkeypatch):
    monkeypatch.setattr(bp_install, "_INSTALLABLE_PACKAGES", {"test-pkg": ["a"], "other": ["b"]})
    run_install(monkeypatch, -9, key="test-pkg")
    run_install(monkeypatch, 0, key="other")
    assert _repair() is not None


def test_a_failed_retry_keeps_the_warning_a_successful_one_clears_it(monkeypatch):
    run_install(monkeypatch, -9)
    run_install(monkeypatch, 1)  # e.g. no network: rolls back only itself
    assert _repair() is not None
    run_install(monkeypatch, 0)
    assert _repair() is None


def test_pip_that_never_started_leaves_no_note(monkeypatch):
    def no_pip(*a, **k):
        raise OSError("too many open files")

    monkeypatch.setattr(subprocess, "Popen", no_pip)
    claim()
    _drain(bp_install._api_install_progress("test-pkg").response)
    assert _repair() is None


def test_an_unreadable_note_still_reports_an_interrupted_install():
    (pip_process.MARKER_DIR / "bpp-install-in-progress-x.json").write_text("{not json")
    assert _repair("x") == ""


def test_a_stopped_face_install_is_offered_again(monkeypatch):
    # It may have left face_recognition findable but broken.
    from bpp.scoring import face_embed_sface_runtime as sface
    from bpp.scoring import face_recognition_loader
    from bpp.web.install_status import install_state

    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(sface, "_sface_available", lambda: False)
    monkeypatch.setattr(face_recognition_loader, "looks_installed", lambda: True)
    assert install_state()["face_installable"] is False
    (pip_process.MARKER_DIR / "bpp-install-in-progress-faces.json").write_text('{"command": "x"}')
    assert install_state()["face_installable"] is True


def test_every_stopped_install_is_listed():
    for key in ("faces", "inpaint"):
        note = pip_process.MARKER_DIR / f"bpp-install-in-progress-{key}.json"
        note.write_text(f'{{"command": "pip install {key}"}}')
    assert pip_process.interrupted_installs() == {
        "faces": "pip install faces",
        "inpaint": "pip install inpaint",
    }


def test_the_repair_command_suits_this_system_s_shell(monkeypatch):
    import shlex

    args = [r"C:\Program Files\Python311\python.exe", "-m", "pip", "install", "x>=1; y"]
    monkeypatch.setattr(pip_process.os, "name", "nt")
    windows = pip_process.command_line(args)
    assert windows == subprocess.list2cmdline(args)  # double quotes for cmd.exe
    assert '"x>=1; y"' in windows and "'" not in windows
    monkeypatch.setattr(pip_process.os, "name", "posix")
    assert shlex.split(pip_process.command_line(args)) == args


def test_every_installable_feature_has_its_safety_map():
    # Without its modules, what the install writes isn't held until a restart.
    from bpp.scoring import install_hold as load_failures

    keys = set(REAL_INSTALLABLE_PACKAGES)
    assert keys == set(load_failures.FEATURE_MODULES)


def test_no_install_starts_while_a_library_opens(monkeypatch):
    from flask import Flask

    from bpp.errors import ConflictError

    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    with (
        Flask(__name__).app_context(),
        bp_install.no_install_during("the library to finish opening"),
    ):
        # Work started while it opens (recovery) can still check: no deadlock.
        with bp_install._worker_start_guard(None):
            pass
        with pytest.raises(ConflictError, match="finish opening"):
            bp_install._api_install_package("test-pkg")
    with Flask(__name__).app_context():
        bp_install._api_install_package("test-pkg")  # fine afterwards


def test_a_request_needing_a_package_held_for_restart_gets_a_409(tmp_path, monkeypatch):
    from bpp.scoring.install_hold import HeldForRestart
    from bpp.web.app import create_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    lib = tmp_path / "lib"
    lib.mkdir()
    app = create_app(workdir=str(lib), library_path=str(lib))
    app.config["TESTING"] = True

    def needs_scipy():
        raise HeldForRestart("scipy.ndimage: restart", name="scipy.ndimage")

    from bpp.scoring import install_hold

    app.add_url_rule("/test-held-scipy", view_func=needs_scipy)
    install_hold.hold_during_install("faces")  # pip still running
    body = app.test_client().get("/test-held-scipy")
    assert body.status_code == 409
    assert "being installed — wait" in body.get_json()["error"]
    install_hold.hold_until_restart("faces")  # pip wrote: installed
    body = app.test_client().get("/test-held-scipy")
    assert "restart Best Photo Picker" in body.get_json()["error"]
    assert "scipy" not in body.get_json()["error"]  # a dependency, not the feature
    assert body.get_json()["context"]["reason"] == "restart_pending"


def test_a_stopped_ai_removal_install_asks_to_be_redone(tmp_path, monkeypatch):
    from bpp.web.app import create_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    lib = tmp_path / "lib"
    lib.mkdir()
    app = create_app(workdir=str(lib), library_path=str(lib))
    app.config["TESTING"] = True
    monkeypatch.setattr("bpp.ai.inpainting.is_available", lambda: True)  # looks fine
    (pip_process.MARKER_DIR / "bpp-install-in-progress-inpaint.json").write_text('{"command": "x"}')
    body = app.test_client().get("/api/v1/inpaint/status").get_json()
    assert (body["available"], body["repair_needed"]) == (False, True)


def test_the_page_learns_which_installs_were_stopped():
    from bpp.web.install_status import install_state

    (pip_process.MARKER_DIR / "bpp-install-in-progress-inpaint.json").write_text('{"command": "x"}')
    assert install_state()["interrupted_keys"] == ["inpaint"]


def test_ai_removal_goes_through_the_same_gate_as_every_model(monkeypatch):
    import sys

    from bpp.ai import inpainting
    from bpp.scoring import install_hold

    monkeypatch.setattr(install_hold, "_held", frozenset())
    monkeypatch.delitem(sys.modules, "simple_lama_inpainting", raising=False)  # not loaded yet
    install_hold.hold_until_restart("inpaint")
    singletons = [v for v in vars(inpainting).values() if hasattr(v, "import_check")]
    assert singletons
    for singleton in singletons:
        with pytest.raises(install_hold.HeldForRestart):
            singleton.import_check()


def test_the_claim_is_released_even_if_stopping_pip_fails(monkeypatch):
    def broken_stop(proc, what="pip install"):
        raise PermissionError("not allowed")

    monkeypatch.setattr(pip_process, "stop", broken_stop)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _MockPopen(["ok\n"]))
    claim()
    _drain(bp_install._api_install_progress("test-pkg").response)
    assert bp_install._claim is None, "work must not stay blocked"
    assert pip_process._live is None


def test_install_notes_live_per_user_and_per_environment():
    import sys

    notes = pip_process._notes_dir()
    assert "install-notes" in notes.parts
    assert sys.prefix not in str(notes)  # not inside the (maybe read-only) environment


def test_an_export_and_an_install_do_not_wait_for_each_other(monkeypatch):
    from bpp.web import state
    from bpp.web.export_worker import ExportWorker

    export = ExportWorker()
    assert export.loads_installable_packages is False

    class _Ctx:
        class workers:
            @staticmethod
            def items():
                return [("export", export)]

    monkeypatch.setattr(state, "get_ctx_or_none", lambda: _Ctx())
    monkeypatch.setattr(type(export), "is_alive", property(lambda self: True))
    assert bp_install._busy_workers() == []
    monkeypatch.setattr(type(export), "is_alive", property(lambda self: False))
    claim()
    started: list[int] = []
    monkeypatch.setattr(export, "_start_thread_unguarded", lambda *a: started.append(1) or True)
    assert export._start_thread() is True and started == [1]


def test_a_pip_left_running_by_a_crashed_server_is_not_a_repair(monkeypatch):
    # The server died; its pip (own session) still runs and still writes.
    import json
    import os

    note = pip_process.MARKER_DIR / "bpp-install-in-progress-faces.json"
    note.write_text(json.dumps({"command": "pip install x", "pid": os.getpid()}))
    monkeypatch.setattr(pip_process, "_pip_still_running", lambda pid, argv: pid == os.getpid())
    assert pip_process.interrupted_installs() == {}  # not "stopped": running
    assert pip_process.pip_left_running() is True
    monkeypatch.setattr(pip_process, "_pip_still_running", lambda pid, argv: False)
    assert pip_process.interrupted_installs() == {"faces": "pip install x"}


def test_a_live_unrelated_process_is_not_taken_for_pip():
    import os

    # Another program with the same process id (here: this test run), even
    # one running some other `pip install`: only our exact command counts.
    ours = "-m pip install face_recognition>=1.3"
    assert pip_process._pip_still_running(os.getpid(), ours) is False
    assert pip_process._pip_still_running(os.getpid(), "") is False  # old note
    assert pip_process._pip_still_running(None, ours) is False


def test_our_pip_still_running_is_recognised():
    import os
    import sys
    import time

    marker = "bpp_test_still_running_marker"
    proc = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(30)  # {marker}"])
    try:
        time.sleep(0.3)
        argv = f"-c import time; time.sleep(30)  # {marker}"  # after the interpreter
        if os.name == "posix":
            assert pip_process._pip_still_running(proc.pid, argv) is True
    finally:
        proc.kill()
        proc.wait()


def test_no_install_while_an_old_pip_still_runs(monkeypatch):
    from bpp.errors import ConflictError

    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    monkeypatch.setattr(pip_process, "pip_left_running", lambda: True)
    with pytest.raises(ConflictError, match="still running"):
        bp_install._api_install_package("test-pkg")


def test_smart_search_says_restart_when_its_runtime_was_just_installed(tmp_path, monkeypatch):
    import sys

    from bpp.scoring import install_hold
    from bpp.web.app import create_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    lib = tmp_path / "lib"
    lib.mkdir()
    app = create_app(workdir=str(lib), library_path=str(lib))
    app.config["TESTING"] = True
    monkeypatch.setattr(app.extensions["bpp"], "load_analysis_if_needed", lambda: {"x": 1})
    monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)
    install_hold.hold_until_restart("onnxruntime")
    resp = app.test_client().post("/api/v1/clip/extract")
    assert resp.status_code == 409
    assert "restart Best Photo Picker" in resp.get_json()["error"]


def test_a_refused_install_names_what_it_waits_for(monkeypatch):
    from flask import Flask

    from bpp.errors import ConflictError

    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    with (
        Flask(__name__).app_context(),
        bp_install.no_install_during("the smart search setup"),
        pytest.raises(ConflictError, match="Wait for the smart search setup"),
    ):
        bp_install._api_install_package("test-pkg")


def test_on_windows_pip_s_whole_process_tree_is_stopped(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(pip_process.os, "name", "nt")
    monkeypatch.setattr(pip_process.subprocess, "run", lambda args, **k: calls.append(args))
    proc = _MockPopen(["x\n"])
    proc.pid = 4321
    pip_process.signal_group(proc, kill=False)
    assert calls == [["taskkill", "/T", "/F", "/PID", "4321"]]
