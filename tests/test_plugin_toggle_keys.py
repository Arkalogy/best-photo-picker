"""A plugin model's on/off switch works like a built-in's.

A detector or scorer registered with a ``toggle_key`` could not be
switched: the toggle endpoint accepted only the built-in keys, and the
analyze run merged only those keys from the library's settings, so the
plugin's switch was never read. The picker also had no way to know a
plugin embedder's method value or toggle key: both came from maps in
the JavaScript that list the built-ins.
"""

from __future__ import annotations

import pytest

from bpp.constants import MODEL_TOGGLE_KEYS
from bpp.scoring.face_detector_registry import FaceDetector, register_detector
from bpp.scoring.registry import ScorerDef, _reset_plugin_scorers_for_tests, register_scorer


@pytest.fixture
def plugin_models():
    """A plugin detector and a plugin scorer, each with a toggle key. (The
    detector stays registered: it finds nothing and the registry has no
    unregister; the scorer registry has a test reset.)"""
    register_detector(
        FaceDetector(
            name="myplugin_det",
            detect=lambda img, conf: [],
            registry_id="myplugin_detector_model",
            toggle_key="model_myplugin_det",
            description="plugin detector",
        )
    )
    register_scorer(
        ScorerDef(
            key="myplugin_sat",
            optional=True,
            toggle_key="model_myplugin_sat",
            score_fn=lambda img, fp, cfg: {"myplugin_sat_score": 0.5},
            api_fields={"myplugin_sat_score": 0},
        )
    )
    yield
    _reset_plugin_scorers_for_tests()


def test_all_toggle_keys_include_the_registered_plugin_keys(plugin_models):
    from bpp.scoring.toggles import all_toggle_keys

    keys = all_toggle_keys()
    assert set(MODEL_TOGGLE_KEYS) <= set(keys)
    assert {"model_myplugin_det", "model_myplugin_sat"} <= set(keys)


def test_the_toggle_endpoint_accepts_a_plugin_key(tmp_path, plugin_models):
    from bpp.db.settings import get_setting
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "lib"))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    with app.test_client() as c:
        r = c.post("/api/v1/models/toggle", json={"key": "model_myplugin_det", "enabled": False})
        assert r.status_code == 200, r.get_json()
        assert (
            c.post(
                "/api/v1/models/toggle", json={"key": "model_nope", "enabled": False}
            ).status_code
            == 400
        )
    with app.app_context():
        assert get_setting(ctx.get_conn(), "model_myplugin_det") == "false"


def test_the_analyze_run_reads_a_plugin_switch_from_settings(tmp_path, plugin_models):
    from bpp.db.settings import set_setting
    from bpp.web.app import create_app
    from bpp.web.bp_analysis import run_config_for_analyze

    app = create_app(library_path=str(tmp_path / "lib"))
    ctx = app.extensions["bpp"]
    with app.app_context():
        conn = ctx.get_conn()
        set_setting(conn, "model_myplugin_sat", "false")
        set_setting(conn, "model_scrfd", "false")
        cfg = run_config_for_analyze(ctx, conn)
    assert cfg["model_myplugin_sat"] is False
    assert cfg["model_scrfd"] is False


def test_a_switched_off_plugin_scorer_does_not_run(plugin_models):
    import numpy as np

    from bpp.scoring.registry import run_optional_scorers

    img = np.zeros((4, 4, 3), dtype=np.uint8)
    out = run_optional_scorers(img, "/p.jpg", {"model_myplugin_sat": False}, {})
    assert "myplugin_sat_score" not in out
    out = run_optional_scorers(img, "/p.jpg", {"model_myplugin_sat": True}, {})
    assert out.get("myplugin_sat_score") == 0.5
