"""What an install waits for and what waits for an install: work that
loads packages, a pending restart, the reasons the page is told (split
from test_install_claims.py)."""

from __future__ import annotations

import pytest

from bpp.web import bp_install
from tests._install_support import (  # noqa: F401
    _reset_install_lock,
    claim,
)


def test_no_install_can_start_while_the_face_retry_wipes(tmp_path, monkeypatch):
    # Between the wipe and the extraction's start an install would refuse
    # the extraction, leaving no faces: installs wait for the retry.
    from bpp.db import journal
    from bpp.errors import ConflictError
    from bpp.web.app import create_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    lib = tmp_path / "lib"
    lib.mkdir()
    app = create_app(workdir=str(lib))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    monkeypatch.setattr(ctx, "load_analysis_if_needed", lambda: [{"filepath": "x"}])
    monkeypatch.setattr("bpp.web.bp_faces_extract.face_recognition_available", lambda: True)
    monkeypatch.setattr(ctx.face_worker, "start", lambda *a: True)
    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: [])
    refused: list[str] = []

    def install_meanwhile(*a, **k):
        try:
            bp_install._api_install_package("test-pkg")
        except ConflictError as e:
            refused.append(str(e))

    monkeypatch.setattr(journal, "journal_start", install_meanwhile)
    assert app.test_client().post("/api/v1/faces/retry").status_code == 202
    assert refused and "face detection" in refused[0], refused
    assert bp_install._claim is None


def test_any_install_holds_up_work_that_loads_packages(monkeypatch):
    # pip may upgrade shared libraries (numpy…) for any feature.
    from bpp.errors import ConflictError
    from bpp.web.clip_worker import ClipWorker

    claim("inpaint")  # not one smart search uses
    with pytest.raises(ConflictError, match="being installed"):
        ClipWorker()._start_thread([], "unused.db")


def test_any_install_waiting_for_a_restart_holds_up_analysis(monkeypatch):
    # Even AI removal: pip may have upgraded numpy, which analysis
    # processes would load fresh from disk.
    from bpp.errors import ConflictError
    from bpp.scoring import install_hold
    from bpp.web.analyze_worker import AnalyzeWorker

    install_hold.hold_until_restart("inpaint")
    with pytest.raises(ConflictError, match="Restart"):
        AnalyzeWorker()._start_thread([], "unused.db", {})


def test_no_install_is_offered_that_the_server_would_refuse(monkeypatch):
    # Busy (analysis, a download, an install left running): the page says
    # why up front rather than after the consent dialog.
    from bpp.web.install_status import install_state

    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: ["analysis"])
    monkeypatch.setattr("bpp.scoring.face_recognition_loader.looks_installed", lambda: False)
    monkeypatch.setattr("bpp.scoring.face_embed_sface_runtime._sface_available", lambda: False)
    state = install_state()
    assert state["face_installable"] is False
    assert "Wait for analysis to finish" in state["install_blocked"]
    assert bp_install.install_refusal() is not None


def test_the_install_state_follows_work_starting_and_ending(monkeypatch):
    # Not a stale "wait for analysis" right after it ended (or the reverse).
    from bpp.web.install_status import install_state

    busy: list[str] = ["analysis"]
    monkeypatch.setattr(bp_install, "_pip_available", lambda: True)
    monkeypatch.setattr(bp_install, "_busy_workers", lambda: list(busy))
    assert "Wait for analysis" in install_state()["install_blocked"]
    busy.clear()
    assert install_state()["install_blocked"] == ""
    claim()
    assert "already in progress" in install_state()["install_blocked"]


def test_a_plugin_worker_without_is_alive_does_not_break_the_status(monkeypatch):
    # Plugin workers needn't be BackgroundWorkers; the status asks on every
    # page load.
    from bpp.web import model_download_jobs, state

    class _Ctx:
        class workers:
            @staticmethod
            def items():
                return [("bare_plugin", object())]

    monkeypatch.setattr(state, "get_ctx_or_none", lambda: _Ctx())
    monkeypatch.setattr(model_download_jobs, "running_labels", lambda: [])
    assert bp_install._busy_workers() == []


def test_smart_search_setup_is_refused_before_its_download(tmp_path, monkeypatch):
    # Waiting for a restart (another install): no point downloading first.
    from bpp.scoring import clip_embed, install_hold
    from bpp.web.app import create_app

    lib = tmp_path / "lib"
    lib.mkdir()
    app = create_app(workdir=str(lib))
    app.config["TESTING"] = True
    monkeypatch.setattr(app.extensions["bpp"], "load_analysis_if_needed", lambda: [{"x": 1}])
    downloads: list[int] = []
    monkeypatch.setattr(clip_embed, "ensure_model", lambda: downloads.append(1))
    monkeypatch.setattr("bpp.web.bp_clip.clip_is_available", lambda: False)
    install_hold.hold_until_restart("faces")
    resp = app.test_client().post("/api/v1/clip/extract")
    assert resp.status_code == 409
    assert downloads == []


def _inpaint_app(tmp_path):
    from bpp.web.app import create_app

    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    app = create_app(workdir=str(lib))
    app.config["TESTING"] = True
    return app


def test_ai_removal_just_installed_says_restart_not_pip_install(tmp_path, monkeypatch):
    import sys

    from bpp.scoring import install_hold

    monkeypatch.delitem(sys.modules, "simple_lama_inpainting", raising=False)
    install_hold.hold_until_restart("inpaint")
    resp = _inpaint_app(tmp_path).test_client().post("/api/v1/photos/1/inpaint", json={"mask": "x"})
    assert resp.status_code == 409
    assert "restart Best Photo Picker" in resp.get_json()["error"]


def test_no_ai_removal_download_offered_without_pip(tmp_path, monkeypatch):
    # The desktop app has no pip: a Download button could only fail.
    monkeypatch.setattr(bp_install, "_pip_available", lambda: False)
    body = _inpaint_app(tmp_path).test_client().get("/api/v1/inpaint/status").get_json()
    assert body["installable"] is False


def test_a_refused_analysis_leaves_the_session_s_folder_alone(tmp_path, monkeypatch):
    from bpp.scoring import install_hold

    app = _inpaint_app(tmp_path)
    ctx = app.extensions["bpp"]
    before = ctx.input_dir
    other = tmp_path / "other"
    other.mkdir()
    install_hold.hold_until_restart("faces")
    resp = app.test_client().post("/api/v1/analyze", json={"input_dir": str(other)})
    assert resp.status_code == 409
    assert ctx.input_dir == before
