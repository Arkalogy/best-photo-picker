"""The command line fires the plugin lifecycle it documents.

``docs/plugins.md`` says ``on_register`` receives ``None`` in CLI
contexts, but only the web server fired it; ``bpp select`` / ``pick`` /
``analyze`` loaded the plugins and never told them. The hooks now fire
around every non-server command.
"""

from __future__ import annotations

import json

import pytest

from bpp import plugin_protocol


class _Recorder:
    def __init__(self):
        self.calls = []

    def on_register(self, app):
        self.calls.append(("register", app))

    def on_shutdown(self):
        self.calls.append(("shutdown", None))


@pytest.fixture
def plugin():
    from bpp import plugins

    plugins._reset_cli_lifecycle_for_tests()  # another test's command may have registered
    rec = _Recorder()
    plugin_protocol.register_plugin(rec)
    yield rec
    plugin_protocol._reset_plugins_for_tests()
    plugins._reset_cli_lifecycle_for_tests()


def test_select_fires_register_and_shutdown(tmp_path, monkeypatch, plugin):
    from bpp import plugins
    from bpp.cli import main

    monkeypatch.setattr(plugins, "load_plugin_entry_points", lambda: 0)
    workdir = tmp_path / "work"
    workdir.mkdir()
    (workdir / "analysis.json").write_text(
        json.dumps(
            [
                {
                    "filepath": "/a.jpg",
                    "date": "2024-01-01T00:00:00",
                    "aggregate_score": 0.5,
                    "blur_raw": 1.0,
                    "blur_score": 0.5,
                    "exposure_score": 0.5,
                    "face_score": 0.5,
                    "composition_score": 0.5,
                }
            ]
        )
    )
    rc = main(
        [
            "select",
            "--workdir",
            str(workdir),
            "--out",
            str(tmp_path / "out"),
            "--dry-run",
            "--k",
            "1",
        ]
    )
    assert rc == 0
    assert plugin.calls == [("register", None), ("shutdown", None)]


def test_register_fires_once_per_process_from_the_cli_helper(plugin, monkeypatch):
    from bpp import plugins

    monkeypatch.setattr(plugins, "load_plugin_entry_points", lambda: 0)
    plugins._reset_cli_lifecycle_for_tests()
    plugins.load_plugins_for_cli()
    plugins.load_plugins_for_cli()
    assert plugin.calls == [("register", None)]
