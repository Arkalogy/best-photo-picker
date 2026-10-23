"""An import scores the new photos with the settings an Analyze would:
the stored model switches and detection thresholds, not the scorer's
defaults."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


def _jpeg(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), "red").save(path, "JPEG")
    return path


def test_an_import_scores_with_the_settings_an_analyze_would(tmp_path, monkeypatch):
    from bpp.db.connection import init_db
    from bpp.web import import_worker as iw

    seen: list[tuple] = []
    monkeypatch.setattr(iw, "process_one", lambda args: seen.append(args))
    source = tmp_path / "source"
    _jpeg(source / "a.jpg")
    workdir = tmp_path / "work"
    workdir.mkdir()
    conn = init_db(str(workdir / "photopicker.db"))
    config = {
        "max_long_side": 321,
        "face_detection_confidence": 0.99,
        "min_face_area_pct": 15,
        "model_pets": False,
        "model_yunet": False,
    }
    worker = iw.ImportWorker()
    worker._emit = lambda _e: None  # type: ignore[method-assign]
    worker._do_import(
        conn, str(source), str(tmp_path / "lib"), str(workdir), config, [".jpg"], None
    )

    assert len(seen) == 1
    _path, max_long_side, _db, face_conf, toggles, scorer_config = seen[0]
    assert max_long_side == 321 and face_conf == 0.99
    assert toggles["model_pets"] is False and toggles["model_yunet"] is False
    assert toggles["model_nudity"] is True
    assert scorer_config["min_face_area_pct"] == 15


def test_the_scoring_subprocess_and_the_import_hand_the_scorer_the_same_settings():
    from bpp.scoring.aggregate import process_one_settings

    config = {"min_face_area_pct": 2.5, "pet_detection_confidence": 0.4, "model_clip": False}
    max_long_side, face_conf, toggles, thresholds = process_one_settings(config)
    assert isinstance(max_long_side, int) and 0 < face_conf < 1
    assert toggles["model_clip"] is False and toggles["model_pets"] is True
    assert thresholds == {
        "pet_detection_confidence": 0.4,
        "pet_input_size": thresholds["pet_input_size"],
        "min_face_area_pct": 2.5,
    }


def test_the_import_endpoint_merges_the_stored_settings_like_analyze(tmp_path, monkeypatch):
    from bpp.db.settings import set_setting
    from bpp.web.app import create_app

    lib = tmp_path / "lib"
    (lib / "photos").mkdir(parents=True)
    source = tmp_path / "source"
    source.mkdir()
    app = create_app(workdir=str(lib / "data"), library_path=str(lib))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    with app.app_context():
        set_setting(ctx.get_conn(), "model_pets", "false")
        set_setting(ctx.get_conn(), "face_detection_confidence", "0.77")
    started: list[dict] = []
    monkeypatch.setattr(
        ctx.import_worker, "start", lambda **kw: started.append(kw) or True, raising=True
    )

    resp = app.test_client().post("/api/v1/import", json={"source_dir": str(source)})

    assert resp.status_code == 202
    config = started[0]["config"]
    assert config["model_pets"] is False
    assert config["face_detection_confidence"] == 0.77
    assert "face_recognition_available" in config
