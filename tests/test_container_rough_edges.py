"""Rough edges a Linux, headless or container install hits: fork-started
workers, no native folder picker, HOME=/ for a uid without a passwd
entry, and a license prompt with no terminal to answer it."""

from __future__ import annotations

import os

import pytest

from bpp.cli import main
from bpp.utils.path_validation import build_library_allowlist
from bpp.utils.subprocess_runner import SENTINEL, BoundedSubprocessRunner
from tests._web_shared import web_client  # noqa: F401


def test_pick_without_osascript_says_unsupported(web_client, monkeypatch):  # noqa: F811
    monkeypatch.setattr("bpp.web.bp_core.shutil.which", lambda _name: None)
    resp = web_client.post("/api/v1/pick", json={"mode": "folder"})
    assert resp.status_code == 200
    assert resp.get_json() == {"cancelled": True, "unsupported": True}


def test_home_at_filesystem_root_is_not_allowlisted(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    monkeypatch.setenv("HOME", os.sep)
    result = build_library_allowlist(library_path=str(lib), include_home=True)
    assert result == [os.path.realpath(lib)]


@pytest.mark.no_preaccept_permissive
def test_accept_attribution_without_a_terminal_explains(monkeypatch, capsys):
    def _eof(_prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert main(["model", "accept-attribution"]) == 1
    assert "docker run -it" in capsys.readouterr().err


#: Set at runtime by the test below; a forked child would inherit the
#: new value, a spawned child re-imports this module and sees the default.
_PARENT_STATE = "as imported"


def _child_reports_parent_state(_payload, result_queue, _cancel):
    result_queue.put(_PARENT_STATE)
    result_queue.put(SENTINEL)


class _ReportPhase:
    name = "report-parent-state"

    def target(self):
        return _child_reports_parent_state

    def build_args(self, payload, result_queue, cancel_event):
        return (payload, result_queue, cancel_event)

    def initial_state(self):
        return []

    def reduce(self, state, msg):
        return [*state, msg], None


def test_worker_subprocesses_start_fresh_not_forked(monkeypatch):
    """Linux's default fork copies the server's running ML thread pools,
    and the face-extraction child could hang on a lock one of them held."""
    monkeypatch.setattr(f"{__name__}._PARENT_STATE", "changed in the parent")
    state, _pid = BoundedSubprocessRunner(_ReportPhase()).run(payload=None)
    assert state == ["as imported"]


@pytest.mark.parametrize(
    ("system", "machine", "probe_rc", "supported"),
    [
        ("Linux", "x86_64", -4, False),  # SIGILL: no AVX
        ("Linux", "x86_64", 0, True),
        ("Linux", "x86_64", 1, True),  # not installed: an ordinary ImportError later
        ("Linux", "x86_64", -9, True),  # killed (out of memory): says nothing about the CPU
        ("Linux", "aarch64", -4, True),  # never probed: Arm wheels carry no AVX code
        ("Darwin", "x86_64", -4, True),
    ],
)
def test_mediapipe_is_probed_before_loading_on_linux_x86(
    monkeypatch, system, machine, probe_rc, supported
):
    import subprocess

    from bpp.scoring import cpu_support
    from bpp.scoring.install_hold import import_gated

    monkeypatch.setattr(cpu_support.platform, "system", lambda: system)
    monkeypatch.setattr(cpu_support.platform, "machine", lambda: machine)
    monkeypatch.setattr(
        cpu_support.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, probe_rc),
    )
    monkeypatch.delenv("BPP_MEDIAPIPE_CPU_OK", raising=False)
    cpu_support._reset_for_tests()
    try:
        assert cpu_support.mediapipe_cpu_supported() is supported
        if not supported:
            with pytest.raises(ImportError, match="no AVX"):
                import_gated("mediapipe.tasks")
    finally:
        cpu_support._reset_for_tests()
        monkeypatch.delenv("BPP_MEDIAPIPE_CPU_OK", raising=False)


def test_readme_finds_the_keyed_link_the_server_prints():
    """The README's `docker logs | grep` must match the banner, not the
    access log (which carries the key redacted)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    banner = "Open Best Photo Picker at"
    assert banner in (root / "bpp" / "commands" / "serve.py").read_text()
    assert f"grep '{banner}' | tail -1" in (root / "README.md").read_text()


def _child_reports_cancel(_payload, result_queue, cancel):
    result_queue.put(cancel.is_set())
    result_queue.put(SENTINEL)


class _CancelPhase(_ReportPhase):
    def target(self):
        return _child_reports_cancel


def test_a_fork_context_cancel_event_still_reaches_the_child():
    """Linux's default mp.Event is fork-context; a spawn child can't take
    it directly, so the runner bridges it."""
    import multiprocessing

    event = multiprocessing.get_context("fork").Event()
    event.set()
    state, _pid = BoundedSubprocessRunner(_CancelPhase()).run(payload=None, cancel_event=event)
    assert state == [True]


def _report_parent_state(_item):
    return _PARENT_STATE


def test_parallel_map_workers_start_fresh_not_forked(monkeypatch):
    """The analysis pool (`bpp run`, `bpp analyze`) spawns too: a forked
    worker would inherit the parent's native thread pools mid-state."""
    from bpp.utils.concurrency import parallel_map

    monkeypatch.setattr(f"{__name__}._PARENT_STATE", "changed in the parent")
    assert parallel_map(_report_parent_state, [1, 2, 3], workers=2) == ["as imported"] * 3


def test_no_download_for_a_model_this_cpu_cannot_run(monkeypatch, tmp_path):
    import bpp.utils.download
    from bpp.scoring import cpu_support
    from bpp.scoring.install_hold import requires
    from bpp.scoring.model_base import ModelSingleton

    monkeypatch.setattr(cpu_support, "mediapipe_cpu_supported", lambda: False)
    fetched = []
    monkeypatch.setattr(bpp.utils.download, "download_file", lambda *a, **kw: fetched.append(a))

    def model(module):
        return ModelSingleton(
            "test model",
            tmp_path / "m.task",
            "https://example.invalid/m.task",
            lambda _path: object(),
            registry_id="test_model",
            import_check=requires(module),
            model_sha256="0" * 64,
        )

    assert model("mediapipe.tasks").cpu_unsupported()
    assert model("mediapipe.tasks").ensure_model() is None
    assert fetched == []
    assert not model("onnxruntime").cpu_unsupported()


def test_no_install_offer_where_pip_cannot_write(monkeypatch, tmp_path):
    """The Docker image: site-packages read-only, user site turned off."""
    from bpp.web import bp_install

    monkeypatch.setattr(
        bp_install.sysconfig, "get_paths", lambda: {"purelib": str(tmp_path / "ro")}
    )
    monkeypatch.setattr(bp_install.site, "ENABLE_USER_SITE", False)
    assert bp_install._pip_available() is False
    monkeypatch.setattr(bp_install.site, "ENABLE_USER_SITE", True)
    assert bp_install._pip_available() is True
