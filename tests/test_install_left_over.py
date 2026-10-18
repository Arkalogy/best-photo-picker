"""A pip an earlier server left running, the install notes behind the
repair banner, and smart search when its runtime waits for a restart
(split from test_pip_process.py)."""

from __future__ import annotations

import os
import subprocess

from bpp.web import bp_install, pip_process
from tests._install_support import (  # noqa: F401  (autouse fixtures)
    _MockPopen,
    _reset_install_lock,
    run_install,
)


def test_our_own_running_install_is_not_taken_for_a_left_over_one(monkeypatch):
    # Its note is written while it runs: it must not block itself.
    pip_process.mark_started("faces", "pip install x", os.getpid(), "argv")
    monkeypatch.setattr(pip_process, "_pip_still_running", lambda pid, argv: True)
    monkeypatch.setattr(pip_process, "_live", object())
    assert pip_process.pip_left_running() is False


def test_the_left_over_pip_check_is_not_rerun_on_every_poll(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        pip_process, "_pip_still_running_now", lambda pid, argv: calls.append(pid) or True
    )
    pid = os.getpid()  # a process that exists
    assert pip_process._pip_still_running(pid, "a") is True
    assert pip_process._pip_still_running(pid, "a") is True
    assert calls == [pid]


def test_a_left_over_pip_that_exited_is_not_reported_from_the_cache(monkeypatch):
    # Installs aren't refused for half a minute after it ends.
    monkeypatch.setattr(pip_process, "_pip_still_running_now", lambda pid, argv: True)
    proc = subprocess.Popen(["sleep", "30"])
    try:
        assert pip_process._pip_still_running(proc.pid, "a") is True
    finally:
        proc.kill()
        proc.wait()
    assert pip_process._pip_still_running(proc.pid, "a") is False


def _clip_run(monkeypatch, problem: str = "it's broken") -> list[dict]:
    """Run smart-search extraction whose model won't load; its events."""
    from bpp.web import clip_worker

    monkeypatch.setattr(clip_worker, "ensure_model", lambda: "model.onnx")
    monkeypatch.setattr(clip_worker, "visual_model_problem", lambda: problem)
    worker = clip_worker.ClipWorker()
    events: list[dict] = []
    monkeypatch.setattr(worker, "_emit", events.append)
    monkeypatch.setattr("bpp.db.connection.close_all_connections", lambda: None)
    worker._safe_run([{"filepath": "x"}], "unused.db")
    return events


def test_smart_search_extraction_says_restart_not_failed(monkeypatch):
    import sys

    from bpp.scoring import install_hold

    monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)
    install_hold.hold_until_restart("onnxruntime")
    events = _clip_run(monkeypatch)
    assert "restart Best Photo Picker" in events[-1]["message"], events


def test_smart_search_extraction_reports_a_model_that_wont_load(monkeypatch):
    # Not "done, 0 computed" with nothing said: the reason, and what to do.
    events = _clip_run(monkeypatch, "its license isn't accepted — accept it")
    assert events[-1]["type"] == "error"
    assert "stopped: its license isn't accepted — accept it" in events[-1]["message"]


def test_a_refused_license_is_named_as_the_reason(monkeypatch):
    from bpp.registry.policy import ModelLoadBlockedError
    from bpp.scoring import clip_embed

    def blocked():
        raise ModelLoadBlockedError.__new__(ModelLoadBlockedError)

    monkeypatch.setattr(clip_embed._clip_visual, "get_or_raise", blocked)
    assert "license isn't accepted" in clip_embed.visual_model_problem()
    monkeypatch.setattr(clip_embed._clip_visual, "get_or_raise", lambda: None)
    monkeypatch.setattr(clip_embed, "can_install", lambda: False)
    assert "ONNX runtime it runs on is missing" in clip_embed.visual_model_problem()
    monkeypatch.setattr(clip_embed, "can_install", lambda: True)
    assert "couldn't be downloaded or loaded" in clip_embed.visual_model_problem()
    monkeypatch.setattr(clip_embed._clip_visual, "get_or_raise", lambda: object())
    assert clip_embed.visual_model_problem() is None


def test_a_left_over_pip_is_found_under_a_framework_python(monkeypatch):
    # macOS framework Pythons re-run themselves from Python.app: ps shows
    # another interpreter path than the one pip was started with.
    ps = (
        "/Library/Frameworks/Python.framework/Versions/3.11/Resources/Python.app"
        "/Contents/MacOS/Python -m pip install x"
    )

    def fake_ps(*a, **k):
        return subprocess.CompletedProcess(a, 0, stdout=ps + "\n")

    monkeypatch.setattr(pip_process.subprocess, "run", fake_ps)
    # (Its arguments only: the venv's path — here with a space — isn't matched.)
    assert pip_process._pip_still_running_now(os.getpid(), "-m pip install x") is True
    assert pip_process._pip_still_running_now(os.getpid(), "-m pip install y") is False


def test_the_note_records_pip_s_arguments_without_the_interpreter(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        pip_process, "mark_started", lambda key, cmd, pid, argv: seen.update(argv=argv) or False
    )
    run_install(monkeypatch)
    assert seen["argv"] == "-m pip install test-pkg-spec"


def test_quitting_before_pip_writes_leaves_no_repair(monkeypatch):
    # Still downloading when the app quit: nothing to repair on next start.
    proc = _MockPopen(["Collecting x\n"])
    proc._poll_alive = True
    pip_process.set_live(proc, "inpaint")
    pip_process.mark_started("inpaint", "pip install x", None, "")
    pip_process.stop_running_pip()
    assert pip_process.interrupted_installs() == {}


def test_quitting_while_pip_writes_leaves_the_repair(monkeypatch):
    proc = _MockPopen(["Installing collected packages: x\n"])
    proc._poll_alive = True
    pip_process.set_live(proc, "inpaint")
    pip_process.mark_writing(proc)
    pip_process.mark_started("inpaint", "pip install x", None, "")
    pip_process.stop_running_pip()
    pip_process.clear_live(proc)
    assert "inpaint" in pip_process.interrupted_installs()


def test_the_notes_are_not_reread_on_every_poll(tmp_path, monkeypatch):
    (tmp_path / "bpp-install-in-progress-faces.json").write_text('{"command": "x"}')
    assert set(pip_process.interrupted_installs()) == {"faces"}
    reads: list[int] = []
    real = type(tmp_path).read_text
    monkeypatch.setattr(
        type(tmp_path), "read_text", lambda self, **k: reads.append(1) or real(self, **k)
    )
    assert set(pip_process.interrupted_installs()) == {"faces"}
    assert reads == []
    (tmp_path / "bpp-install-in-progress-faces.json").unlink()  # the repair ran
    assert pip_process.interrupted_installs() == {}


def test_an_install_s_note_is_settled_before_a_retry_can_start(monkeypatch):
    # A retry writes its own note once the claim is free: the finished run
    # must not remove it afterwards.
    claims_at_settle: list[object] = []
    real = pip_process.mark_ended
    monkeypatch.setattr(
        pip_process,
        "mark_ended",
        lambda *a, **k: claims_at_settle.append(bp_install._claim) or real(*a, **k),
    )
    run_install(monkeypatch, 1)
    assert claims_at_settle and claims_at_settle[0] is not None
    assert bp_install._claim is None


def test_a_note_removed_by_the_app_is_forgotten_at_once(tmp_path):
    # (Not left to the folder's change time: some filesystems keep it only
    # to the second.)
    pip_process.mark_started("faces", "pip install x")
    assert "faces" in pip_process.interrupted_installs()
    pip_process.mark_ended("faces", stopped=False, succeeded=True, prior=False)
    assert pip_process.interrupted_installs() == {}


def test_quitting_a_retry_before_it_writes_keeps_the_earlier_repair():
    # Run 1 was cut off writing; run 2 (the retry) is still downloading when
    # the app quits: run 1's damage still needs the repair.
    proc = _MockPopen(["Collecting x\n"])
    pip_process.mark_started("faces", "pip install x")  # run 1's note
    pip_process.note_prior(proc, pip_process.mark_started("faces", "pip install x"))
    pip_process.set_live(proc, "faces")
    pip_process.stop_running_pip()
    pip_process.clear_live(proc)
    assert "faces" in pip_process.interrupted_installs()


def test_a_worker_waiting_for_a_restart_logs_no_crash(monkeypatch, caplog):
    import logging

    caplog.set_level(logging.INFO)
    events = _clip_run_held(monkeypatch)
    assert "restart Best Photo Picker" in events[-1]["message"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR and "CLIP" in r.message]


def _clip_run_held(monkeypatch) -> list[dict]:
    import sys

    from bpp.scoring import install_hold

    monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)
    install_hold.hold_until_restart("onnxruntime")
    return _clip_run(monkeypatch)


def test_just_installed_onnxruntime_is_not_shown_as_missing(monkeypatch):
    # The models list mustn't offer an install that would only be refused.
    import sys

    from bpp.scoring import install_hold
    from bpp.web import models_status

    monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)
    install_hold.hold_until_restart("onnxruntime")
    clip = models_status._build_clip("/nonexistent", True, ort_ok=True)
    assert clip["lib_available"] is True


def test_face_recovery_waits_for_the_restart_without_spending_retries(monkeypatch):
    import sqlite3

    from bpp.scoring import install_hold
    from bpp.web import face_extraction_journal as journal
    from bpp.web import face_worker
    from tests.test_face_extraction_journal import _build_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _build_schema(conn)
    run_id = journal.start_run(conn)
    monkeypatch.setattr(
        face_worker,
        "extract_and_cluster_faces",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError),
    )
    install_hold.hold_until_restart("faces")
    assert face_worker.recover_pending_face_extractions(conn) == 0
    assert journal.get_retry_count(conn, run_id) == 0
    assert [r["run_id"] for r in journal.pending_runs(conn)] == [run_id]  # still pending


def test_on_windows_a_left_over_pip_is_found_by_its_process_id(monkeypatch):
    # Its command line can't be read there: a live process with its id
    # counts (installs wait) — not "interrupted, repair it".
    seen: list[list[str]] = []

    def fake_run(cmd, **k):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='"python.exe","4242","Console"\n')

    monkeypatch.setattr(pip_process.os, "name", "nt")
    monkeypatch.setattr(pip_process.subprocess, "run", fake_run)
    assert pip_process._pip_still_running_now(4242, "-m pip install x") is True
    assert pip_process._pip_still_running_now(4243, "-m pip install x") is False
    assert seen[0][0] == "tasklist"


def test_no_package_loading_work_starts_while_an_old_pip_still_writes(monkeypatch):
    # The server was killed mid-install; its pip still runs after the restart.
    import pytest

    from bpp.errors import ConflictError
    from bpp.web.export_worker import ExportWorker
    from bpp.web.import_worker import ImportWorker

    monkeypatch.setattr(pip_process, "pip_left_running", lambda: True)
    with pytest.raises(ConflictError, match="still running"):
        ImportWorker()._start_thread()
    with pytest.raises(ConflictError, match="still running"), bp_install.no_install_during("x"):
        pass
    export = ExportWorker()
    export._start_thread_unguarded = lambda *a: True
    assert export._start_thread() is True  # loads nothing pip writes
