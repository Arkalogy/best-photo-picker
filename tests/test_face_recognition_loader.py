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
    assert "SystemExit" in loader.load_face_recognition_or_reason()[1]


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
        assert loader.load_face_recognition_or_reason()[1].startswith("not installed")
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


@pytest.fixture
def import_counting_broken_package(tmp_path, monkeypatch):
    """Installed but broken: importing it fails on a missing dependency."""
    pkg = tmp_path / "face_recognition"
    pkg.mkdir()
    counter = tmp_path / "imports"
    (pkg / "__init__.py").write_text(
        f"open({str(counter)!r}, 'a').write('x')\nimport dlib_missing_for_this_test\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "face_recognition", raising=False)
    loader._reset_for_tests()
    yield counter
    sys.modules.pop("face_recognition", None)
    loader._reset_for_tests()


def test_an_installed_but_broken_package_is_imported_once(import_counting_broken_package):
    for call in range(5):
        assert loader.load_face_recognition() is None, f"call {call} loaded it"
    assert import_counting_broken_package.read_text() == "x"
    assert "failed to load" in loader.load_face_recognition_or_reason()[1]


def test_a_missing_package_is_looked_up_once_not_per_photo(monkeypatch):
    import importlib.abc

    lookups = []

    class Counting(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name == "face_recognition":
                lookups.append(name)
            return None

    monkeypatch.delitem(sys.modules, "face_recognition", raising=False)
    monkeypatch.setattr(sys, "meta_path", [Counting(), *sys.meta_path])
    monkeypatch.setattr(sys, "path", [p for p in sys.path if "site-packages" not in p])
    loader._reset_for_tests()
    try:
        for call in range(5):
            assert loader.load_face_recognition() is None, f"call {call} loaded it"
        assert lookups == ["face_recognition"]
    finally:
        loader._reset_for_tests()
