"""``--debug`` is verbose logging, never Werkzeug's interactive debugger.

The debugger returns full tracebacks (source lines, absolute paths) to
whoever triggers an error, and with sharing on the server binds the LAN.
"""

from __future__ import annotations

import argparse
import os


def test_web_command_never_runs_flask_in_debug_mode(tmp_path, monkeypatch):
    from flask import Flask

    captured: dict = {}

    def fake_run(self, *args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(Flask, "run", fake_run)
    from bpp.commands.serve import do_web

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    args = argparse.Namespace(
        input=None, workdir=workdir, config=None, port=5999, no_browser=True, host=None, debug=True
    )
    assert do_web(args) == 0
    assert captured.get("debug") is False
