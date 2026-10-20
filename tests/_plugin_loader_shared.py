"""Shared fixtures and helpers of the test_plugin_loader.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import importlib.metadata

import pytest


@pytest.fixture(autouse=True)
def _reset_loaded():
    """Each test starts with a clean loaded-set so order doesn't
    matter."""
    from bpp.plugins import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture
def plugins_enabled(monkeypatch):
    """Plugins are off by default. Tests that exercise the
    happy path opt in via env var so they're representative of a
    user who said `BPP_ENABLE_PLUGINS=1`."""
    monkeypatch.setenv("BPP_ENABLE_PLUGINS", "1")


class _FakeEP:
    """Minimal stand-in for an importlib.metadata EntryPoint."""

    def __init__(self, name: str, value: str, target):
        self.name = name
        self.value = value
        self._target = target

    def load(self):
        return self._target


def _patch_entry_points(monkeypatch, eps_list):
    """Stub importlib.metadata.entry_points() to return a selectable
    object exposing only `bpp.plugins`. Mirrors the Python 3.10+
    EntryPoints.select(group=...) API the loader uses."""

    class _Selectable:
        def select(self, group):
            return eps_list if group == "bpp.plugins" else []

    monkeypatch.setattr(importlib.metadata, "entry_points", lambda: _Selectable())
