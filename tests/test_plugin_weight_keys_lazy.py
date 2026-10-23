"""A plugin scorer's weight can be overridden per recompute and per album.

The recompute override allow-list and the per-album config allow-list
were built at import time from the scorer registry, so a weight a plugin
registered later was dropped from both.
"""

from __future__ import annotations

import pytest

from bpp.scoring.registry import ScorerDef, _reset_plugin_scorers_for_tests, register_scorer


@pytest.fixture
def plugin_weight():
    register_scorer(
        ScorerDef(
            key="myplugin_sat",
            weight_key="myplugin_sat_weight",
            default_weight=0.1,
            optional=True,
            score_fn=lambda img, fp, cfg: {"myplugin_sat_score": 0.5},
            api_fields={"myplugin_sat_score": 0},
        )
    )
    yield "myplugin_sat_weight"
    _reset_plugin_scorers_for_tests()


def test_the_recompute_override_accepts_the_plugin_weight(plugin_weight):
    from bpp.web.bp_recompute import _config_with_overrides
    from bpp.web.recompute import recompute_weight_keys

    assert plugin_weight in recompute_weight_keys()
    cfg = _config_with_overrides({}, {plugin_weight: 0.7, "blur_weight": 0.2})
    assert cfg[plugin_weight] == 0.7
    assert cfg["blur_weight"] == 0.2


def test_an_album_stores_the_plugin_weight(tmp_path, plugin_weight):
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "lib"))
    app.config["TESTING"] = True
    with app.test_client() as c:
        album_id = c.post("/api/v1/albums", json={"name": "Trip"}).get_json()["id"]
        r = c.put(f"/api/v1/albums/{album_id}", json={"config": {plugin_weight: 0.8}})
        assert r.status_code == 200, r.get_json()
        albums = c.get("/api/v1/albums").get_json()["albums"]
    album = next(a for a in albums if a["id"] == album_id)
    assert album["config"][plugin_weight] == 0.8
