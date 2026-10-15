"""face_recognition must never be able to end the bpp process."""

from __future__ import annotations

import sys

import numpy as np
import pytest

from bpp.scoring import face_recognition_loader as loader


@pytest.fixture
def broken_face_recognition(tmp_path, monkeypatch):
    """A face_recognition that behaves like the real one when its model
    package can't import pkg_resources: prints a hint and calls quit()."""
    pkg = tmp_path / "face_recognition"
    pkg.mkdir()
    (pkg / "__init__.py").write_text('print("Please install face_recognition_models")\nquit()\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "face_recognition", raising=False)
    loader._reset_for_tests()
    yield
    sys.modules.pop("face_recognition", None)
    loader._reset_for_tests()


def test_quit_on_import_is_contained(broken_face_recognition):
    assert loader.load_face_recognition() is None
    assert "SystemExit" in loader.face_recognition_unavailable_reason()


def test_callers_degrade_instead_of_exiting(broken_face_recognition, monkeypatch):
    from bpp.scoring import face_embed, face_fallback

    monkeypatch.setattr(face_embed, "_sface_available", lambda: False)
    assert face_fallback._has_face_recognition() is False
    assert face_fallback._dlib_detect(np.zeros((64, 64, 3), dtype=np.uint8)) == []
    assert face_embed.is_available() is False


def test_missing_package_is_quietly_unavailable(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "face_recognition", None)
    loader._reset_for_tests()
    try:
        assert loader.load_face_recognition() is None
        assert loader.face_recognition_unavailable_reason().startswith("not installed")
    finally:
        loader._reset_for_tests()


def test_faces_extra_pins_setuptools_for_pkg_resources():
    import pathlib
    import tomllib

    root = pathlib.Path(__file__).resolve().parents[1]
    extras = tomllib.loads((root / "pyproject.toml").read_text())["project"][
        "optional-dependencies"
    ]
    assert "setuptools<81" in extras["faces"]
