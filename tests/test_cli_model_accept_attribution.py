"""`bpp model accept-attribution` — the CLI twin of the pre-analysis
dialog's combined license step."""

from __future__ import annotations

import pytest

from bpp.cli import main
from bpp.registry.attribution_bundle import pending_attribution_entries

pytestmark = pytest.mark.no_preaccept_permissive


def test_lists_every_model_and_records_on_yes(monkeypatch, capsys):
    expected = [e.id for e in pending_attribution_entries()]
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
    assert main(["model", "accept-attribution"]) == 0
    out = capsys.readouterr().out
    assert "MediaPipe Selfie Segmenter" in out
    assert "BSD 3-Clause" in out
    assert pending_attribution_entries() == []
    assert str(len(expected)) in out


def test_no_records_nothing(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    before = [e.id for e in pending_attribution_entries()]
    assert main(["model", "accept-attribution"]) == 1
    assert [e.id for e in pending_attribution_entries()] == before


def test_yes_flag_skips_the_prompt(monkeypatch):
    def _no_input(_prompt=""):
        raise AssertionError("prompted despite --yes")

    monkeypatch.setattr("builtins.input", _no_input)
    assert main(["model", "accept-attribution", "--yes"]) == 0
    assert pending_attribution_entries() == []


def test_nothing_pending_is_a_no_op(monkeypatch, capsys):
    assert main(["model", "accept-attribution", "--yes"]) == 0
    capsys.readouterr()
    assert main(["model", "accept-attribution"]) == 0
    assert "already accepted" in capsys.readouterr().out
