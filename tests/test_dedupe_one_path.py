"""Every selection path dedupes through the strategy registry.

``bpp select`` and the weight optimizer called the built-in hash dedupe
directly, so a plugin strategy the user set in config (``dedupe_strategy``)
applied in the web recompute only; ``bpp select`` also never loaded plugin
entry points, so the strategy could not even be registered there.
"""

from __future__ import annotations

import argparse
import json

import pytest

from bpp.dedupe.strategy import DedupeStrategy, DedupeStrategyRegistry, register_dedupe_strategy


@pytest.fixture(autouse=True)
def _plugin_strategy():
    DedupeStrategyRegistry._reset_for_tests()
    calls: list[int] = []

    def keep_first_only(items, _config, **_kw):
        calls.append(len(items))
        return items[:1]

    register_dedupe_strategy(DedupeStrategy(name="myplugin_first", dedupe_fn=keep_first_only))
    yield calls
    DedupeStrategyRegistry._reset_for_tests()


def _item(path, score, h, day):
    return {
        "filepath": path,
        "date": f"2024-01-{day:02d}T12:00:00",
        "aggregate_score": score,
        "blur_raw": 100.0,
        "blur_score": 0.5,
        "exposure_score": 0.5,
        "face_score": 0.5,
        "composition_score": 0.5,
        "phash": h,
        "ahash": h,
    }


# Days apart and hashes far apart: three photos the hash dedupe keeps.
_ITEMS = [
    _item("/a.jpg", 0.9, 0, 1),
    _item("/b.jpg", 0.8, 0xFFFFFFFFFFFFFFFF, 10),
    _item("/c.jpg", 0.7, 0x00FF00FF00FF00FF, 20),
]


def test_the_shared_path_honours_a_plugin_strategy(_plugin_strategy):
    from bpp.dedupe.select import dedupe_candidates

    out, mode = dedupe_candidates([dict(i) for i in _ITEMS], {"dedupe_strategy": "myplugin_first"})
    assert mode == "myplugin_first"
    assert [o["filepath"] for o in out] == ["/a.jpg"]
    assert _plugin_strategy == [3]


def test_the_shared_path_falls_back_to_hash_without_a_strategy():
    from bpp.dedupe.select import dedupe_candidates

    out, mode = dedupe_candidates([dict(i) for i in _ITEMS], {})
    assert mode == "hash"
    assert len(out) == 3


def test_bpp_select_loads_plugins_and_uses_the_strategy(tmp_path, monkeypatch, _plugin_strategy):
    from bpp import plugins
    from bpp.commands import analyze as commands

    loaded = []
    monkeypatch.setattr(plugins, "load_plugin_entry_points", lambda: loaded.append(1) or 0)
    monkeypatch.setattr(
        "bpp.commands.analyze.load_config", lambda _p: {"dedupe_strategy": "myplugin_first"}
    )
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "analysis.json").write_text(json.dumps(_ITEMS))
    ns = argparse.Namespace(
        workdir=str(workdir),
        out=str(tmp_path / "out"),
        config=None,
        k=3,
        export_mode="copy",
        gallery=False,
        force=False,
        dry_run=True,
        seed=1,
        extensions=".jpg",
    )
    assert commands.do_select(ns) == 0
    assert loaded == [1], "bpp select must load plugin entry points before deduping"
    assert _plugin_strategy == [3], "the plugin strategy must dedupe the CLI selection"


def test_the_optimizer_uses_the_strategy(_plugin_strategy):
    from bpp.web.recompute import RecomputeOptions, optimize

    optimize(
        RecomputeOptions(
            analysis=[dict(i) for i in _ITEMS],
            config={"dedupe_strategy": "myplugin_first"},
            k=2,
        )
    )
    assert _plugin_strategy == [3], "the optimizer's one dedupe pass must go through the registry"
