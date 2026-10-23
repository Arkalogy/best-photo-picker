"""The analysis preflight follows the model on/off switches, and a model
it couldn't fetch is off for that run, so scoring doesn't silently retry
the download."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_skipped_downloads(monkeypatch):
    """Code under test may mark downloads as skipped for "this process";
    keep that from leaking into other tests."""
    monkeypatch.setenv("BPP_SKIP_DOWNLOADS", "")


class _FakeModel:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls = 0

    def ensure_model(self):
        self.calls += 1
        return Path("/m") if self.ok else None

    def license_blocked(self) -> bool:
        return False


def _builtin_preflight(monkeypatch, toggles, *, fr_ok=True, core_failures=None):
    from bpp.scoring import face
    from bpp.web import analyze_model_preflight as pf

    core: list = []
    optional = {
        "model_blazeface_fr": _FakeModel(fr_ok),
        "model_face_landmarker": _FakeModel(),
        "model_hand_landmarker": _FakeModel(),
    }
    calls: list = []
    monkeypatch.setattr(
        face, "OPTIONAL_FACE_MODELS", tuple((k, m, f"{k} failed") for k, m in optional.items())
    )
    monkeypatch.setattr(
        face, "core_face_model_failures", lambda: core.append(1) or dict(core_failures or {})
    )
    monkeypatch.setattr("bpp.scoring.face_mediapipe.ensure_mediapipe_models", lambda: [])
    monkeypatch.setattr("bpp.scoring.face_embed.ensure_sface_model", lambda: core.append(2) or [])
    monkeypatch.setattr(
        "bpp.scoring.segmentation.ensure_segmenter_model", lambda: calls.append("seg") or []
    )
    monkeypatch.setattr("bpp.scoring.pose.ensure_pose_model", lambda: calls.append("pose") or [])
    monkeypatch.setattr(pf, "_download_scrfd", lambda emit: calls.append("scrfd") or True)
    monkeypatch.setattr(pf, "_download_pets", lambda emit: calls.append("pets") or True)
    events: list = []
    failed = pf._download_builtin_models(events.append, toggles)
    for key, model in optional.items():
        if model.calls:
            calls.append(key)
    return failed, calls, core, events


def test_switched_off_models_are_not_downloaded(monkeypatch):
    from bpp.constants import MODEL_TOGGLE_KEYS

    failed, calls, core, _ = _builtin_preflight(
        monkeypatch, dict.fromkeys(MODEL_TOGGLE_KEYS, False)
    )
    assert calls == [] and failed == set()
    assert core == [1, 2]  # the always-on detectors are still fetched


def test_switched_on_models_are_downloaded(monkeypatch):
    _, calls, _, _ = _builtin_preflight(monkeypatch, {})
    assert sorted(calls) == sorted(
        [
            "scrfd",
            "model_blazeface_fr",
            "model_face_landmarker",
            "model_hand_landmarker",
            "seg",
            "pose",
            "pets",
        ]
    )


def test_a_failed_optional_model_is_reported_with_a_warning(monkeypatch):
    failed, _, _, events = _builtin_preflight(monkeypatch, {}, fr_ok=False)
    assert failed == {"model_blazeface_fr"}
    assert {"type": "warning", "message": "model_blazeface_fr failed"} in events


def test_the_run_scores_with_failed_models_switched_off(monkeypatch):
    from bpp.web import analyze_model_preflight as pf

    seen: dict = {}

    def fake(emit, toggles):
        seen.update(toggles)
        return {"model_nudity"}

    monkeypatch.setattr(pf, "preflight_models", fake)
    config = {"model_pose": False, "max_long_side": 1024}
    run = pf.preflight_run_config(lambda e: None, config)
    assert run["model_nudity"] is False
    assert run["model_pose"] is False and run["max_long_side"] == 1024
    assert seen["model_pose"] is False and seen["model_pets"] is True
    assert "model_nudity" not in config  # the caller's config is untouched


def test_a_failed_core_detector_is_off_for_the_run(monkeypatch):
    failed, _, _, events = _builtin_preflight(
        monkeypatch, {}, core_failures={"model_yunet": "YuNet failed"}
    )
    assert "model_yunet" in failed
    assert {"type": "warning", "message": "YuNet failed"} in events


def test_the_scoring_child_honors_a_run_only_yunet_switch(monkeypatch):
    import queue

    from bpp.scoring import aggregate
    from bpp.web.analyze_scoring import _scoring_worker

    seen: list = []
    monkeypatch.setattr(aggregate, "init_analysis_db", lambda db: None)
    monkeypatch.setattr(aggregate, "process_one", lambda args: seen.append(args[4]) or {})
    _scoring_worker(["a.jpg"], {"model_yunet": False}, "db", queue.Queue(), None)
    assert seen and seen[0]["model_yunet"] is False


def test_switched_off_yunet_is_not_run(monkeypatch):
    import numpy as np

    from bpp.scoring import face_pipeline
    from tests._detector_stubs import stub_detectors

    ran: list = []
    monkeypatch.setattr(face_pipeline, "_yunet_detect", lambda img, conf: ran.append(2) or [])
    with stub_detectors(
        {
            "yunet": lambda img, conf: ran.append(1) or [],
            "mediapipe_sr": lambda img, conf: [],
            "blazeface_fr": lambda img, conf: [],
            "dlib": lambda img, conf: [],
        }
    ):
        face_pipeline._collect_builtin_detections(
            np.zeros((1200, 1200, 3), np.uint8), 0.5, {"model_yunet": False, "model_scrfd": False}
        )
    assert ran == []


def test_face_worker_respects_the_toggles(monkeypatch):
    from bpp.scoring import face

    model = _FakeModel()
    monkeypatch.setattr(face, "OPTIONAL_FACE_MODELS", (("model_blazeface_fr", model, "w"),))
    monkeypatch.setattr(face, "ensure_core_face_models", lambda: [])
    face.ensure_face_models({"model_blazeface_fr": False})
    assert model.calls == 0
    face.ensure_face_models()
    assert model.calls == 1


def test_the_analyze_worker_scores_with_the_preflight_config(tmp_path, monkeypatch):
    """End to end through AnalyzeWorker._run: a model whose download
    failed is switched off in the config handed to the scoring child."""
    from PIL import Image

    from bpp.web import analyze_model_preflight as pf
    from bpp.web import analyze_worker as aw

    photos = tmp_path / "in"
    photos.mkdir()
    Image.new("RGB", (64, 64)).save(photos / "a.jpg")
    workdir = tmp_path / "work"
    workdir.mkdir()

    monkeypatch.setattr(pf, "preflight_models", lambda emit, toggles: {"model_nudity"})
    seen: dict = {}

    class _Stop(Exception):
        pass

    def fake_scoring(*, config, **kw):
        seen.update(config)
        raise _Stop

    monkeypatch.setattr(aw, "run_scoring_subprocess", fake_scoring)
    worker = aw.AnalyzeWorker()
    with pytest.raises(_Stop):
        worker._run(str(photos), str(workdir), {"model_nudity": True}, [".jpg"])
    assert seen["model_nudity"] is False


def test_clip_phase_announces_its_download(monkeypatch):
    from bpp.scoring import clip_embed
    from bpp.utils import download as dl
    from bpp.web import analyze_phases

    events: list = []
    monkeypatch.setattr(clip_embed, "is_available", lambda: True)
    monkeypatch.setattr(clip_embed, "ensure_model", lambda: dl.announce("Downloading CLIP…"))
    monkeypatch.setattr("bpp.db.photos.get_photo_id_map_by_paths", lambda conn, paths: {})
    analyze_phases.run_clip_phase(None, [], emit=events.append, cancel_event=None)
    assert {"type": "status", "message": "Downloading CLIP…"} in events


def test_scoring_and_face_children_skip_downloads_that_failed_in_preflight(monkeypatch):
    import queue

    from bpp.scoring import aggregate
    from bpp.utils import download as dl
    from bpp.web.analyze_scoring import _scoring_worker

    monkeypatch.setattr(aggregate, "init_analysis_db", lambda db: None)
    monkeypatch.setattr(aggregate, "process_one", lambda args: {})
    _scoring_worker([], {"model_yunet": False}, "db", queue.Queue(), None)
    assert "opencv_yunet" in dl._skipped_downloads()


def test_face_worker_passes_toggles_and_skips_a_failed_yunet(tmp_path, monkeypatch):
    from bpp.scoring import face, face_yunet
    from bpp.web import face_worker as fw

    seen: dict = {}
    monkeypatch.setattr(
        face, "ensure_face_models", lambda toggles=None: seen.update(toggles=toggles) or []
    )
    monkeypatch.setattr("bpp.scoring.face_embed.ensure_sface_model", lambda: [])
    monkeypatch.setattr(face_yunet, "_YUNET_MODEL_PATH", str(tmp_path / "missing.onnx"))
    config = fw.prepare_face_models(lambda e: None, {"model_blazeface_fr": False})
    assert seen["toggles"]["model_blazeface_fr"] is False
    assert config["model_yunet"] is False
