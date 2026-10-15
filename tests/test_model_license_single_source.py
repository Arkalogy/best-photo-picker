"""The legal registry is the only place a model's license is written.

Every model BPP runs links to a registry entry by id, and every
license label shown anywhere is derived from that entry.
"""

from __future__ import annotations

import gc
import importlib
import pathlib
import pkgutil
import re

import pytest

import bpp.ai
import bpp.scoring
from bpp.registry import get_entry
from bpp.registry.labels import plain_english_license_label
from bpp.scoring.face_detector_registry import list_detectors
from bpp.scoring.face_embedder_registry import list_embedders
from bpp.scoring.model_base import ModelSingleton

_REPO = pathlib.Path(__file__).resolve().parent.parent


def _import_all_model_modules() -> None:
    for pkg in (bpp.scoring, bpp.ai):
        for mod in pkgutil.iter_modules(pkg.__path__, pkg.__name__ + "."):
            try:
                importlib.import_module(mod.name)
            except ImportError:
                continue


def test_every_model_singleton_links_to_a_registry_entry():
    _import_all_model_modules()
    singletons = [o for o in gc.get_objects() if isinstance(o, ModelSingleton)]
    assert singletons
    unlinked = [s.name for s in singletons if not s.registry_id or get_entry(s.registry_id) is None]
    assert unlinked == [], f"models with no registry entry: {unlinked}"


def test_model_singleton_refuses_a_missing_registry_id():
    with pytest.raises(ValueError, match="registry_id"):
        ModelSingleton("x", None, None, lambda _p: None, registry_id=None)


_EXPECTED_DETECTOR_IDS = {
    "yunet": "opencv_yunet",
    "mediapipe_sr": "mediapipe_blazeface_short_range",
    "blazeface_fr": "mediapipe_blazeface_full_range",
    "scrfd": "insightface_scrfd_25g",
    "dlib": "dlib_face_recognition_resnet_v1",
    "haar": "opencv_haar_frontalface",
}

_EXPECTED_EMBEDDER_IDS = {
    "sface": "sface_yunet",
    "dlib": "dlib_face_recognition_resnet_v1",
    "buffalo_s": "insightface_buffalo_s",
}


@pytest.mark.parametrize(("name", "registry_id"), sorted(_EXPECTED_DETECTOR_IDS.items()))
def test_builtin_detectors_link_to_the_right_entry(name, registry_id):
    det = {d.name: d for d in list_detectors()}[name]
    assert det.registry_id == registry_id
    entry = get_entry(registry_id)
    assert det.license_label == plain_english_license_label(entry.upstream_claimed_license_class)


@pytest.mark.parametrize(("name", "registry_id"), sorted(_EXPECTED_EMBEDDER_IDS.items()))
def test_builtin_embedders_link_to_the_right_entry(name, registry_id):
    importlib.import_module("bpp.scoring.face_embed")
    importlib.import_module(
        "bpp.scoring.face_embed_buffalo_s"
    ).register_with_face_embedder_registry()
    emb = {e.name: e for e in list_embedders()}[name]
    assert emb.registry_id == registry_id
    entry = get_entry(registry_id)
    assert emb.license_label == plain_english_license_label(entry.upstream_claimed_license_class)


def test_scrfd_reads_as_research_only():
    det = {d.name: d for d in list_detectors()}["scrfd"]
    assert det.license_label == "Research / non-commercial"


def test_no_license_strings_outside_the_registry():
    """No ``license_id=`` field, and no SPDX names in runtime model
    descriptions — a license written anywhere but the registry drifts."""
    offenders = []
    pattern = re.compile(r"license_id\s*=")
    for path in (_REPO / "bpp").rglob("*.py"):
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(_REPO)))
    assert offenders == [], f"license_id= still used in: {offenders}"


def test_models_status_descriptions_carry_no_license_names():
    src = (_REPO / "bpp/web/models_status.py").read_text(encoding="utf-8")
    descriptions = re.findall(r'"description":\s*\(?\s*"([^"]*)"', src)
    assert descriptions
    bad = [d for d in descriptions if re.search(r"\b(BSD|MIT|Apache|AGPL|GPL)\b", d)]
    assert bad == []


def test_download_file_refuses_a_missing_registry_id(tmp_path):
    """No ungated "ancillary" downloads: every model has an entry."""
    from unittest.mock import patch

    from bpp.utils.download import download_file

    with patch("urllib.request.urlopen") as net, pytest.raises(ValueError, match="registry_id"):
        download_file("https://example.com/x", str(tmp_path / "x"), registry_id=None)
    net.assert_not_called()
