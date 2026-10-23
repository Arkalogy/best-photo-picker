"""Shared fixtures and helpers of the test_integration.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import os
import queue

import pytest
from PIL import Image


def _real(p):
    """Resolve macOS /var -> /private/var symlink."""
    return os.path.realpath(str(p))


def _drain_queue(q: queue.Queue) -> list[dict]:
    msgs = []
    while not q.empty():
        try:
            msgs.append(q.get_nowait())
        except queue.Empty:
            break
    return msgs


def _create_test_images(directory: str, count: int = 5, size: tuple = (200, 200)):
    """Create minimal JPEG test images. Returns list of paths."""
    paths = []
    for i in range(count):
        p = os.path.join(directory, f"img_{i:03d}.jpg")
        color = (50 + i * 40, 100, 150)
        Image.new("RGB", size, color).save(p, "JPEG")
        paths.append(p)
    return paths


@pytest.fixture()
def _suppress_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture()
def integration_app(tmp_path, _suppress_config, offline_downloads):
    """Flask app with a real library directory structure, ready for import
    (offline: a model that isn't on disk is off for the run)."""
    from bpp.web.app import create_app

    lib = _real(tmp_path / "library")
    os.makedirs(lib, exist_ok=True)

    app = create_app(library_path=lib)
    app.config["TESTING"] = True
    yield app, lib
    app.extensions["bpp"].shutdown()  # no worker outlives its test


@pytest.fixture()
def source_photos(tmp_path):
    """Source directory with test images ready for import."""
    src = str(tmp_path / "source_photos")
    os.makedirs(src)
    paths = _create_test_images(src, count=5)
    return src, paths


# ===================================================================
# 2. Analysis Workflow (import → analyze → scores populated)
# ===================================================================

_SKIP_SLOW_ANALYZE = pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason="AnalyzeWorker subprocess startup exceeds 3 min on CI free runners",
)
