"""HTTP surface of the combined attribution acceptance step."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bpp.registry.attribution_bundle import pending_attribution_entries

pytestmark = pytest.mark.no_preaccept_permissive

_URL = "/api/v1/model-registry/acceptance/attribution-bundle"
_ACK = {"permissive_attribution_acknowledged": True}


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(tmp_path / "acceptance.jsonl"))
    from bpp.web.app import create_app

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "analysis.json").write_text("[]", encoding="utf-8")
    app = create_app(workdir=str(workdir))
    app.config["TESTING"] = True
    return app.test_client()


def _post(client, body: dict):
    return client.post(
        _URL + "/confirm",
        data=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )


def test_get_lists_every_pending_model_with_its_license(client):
    data = client.get(_URL).get_json()
    ids = [m["id"] for m in data["models"]]
    assert ids == [e.id for e in pending_attribution_entries()]
    seg = next(m for m in data["models"] if m["id"] == "mediapipe_selfie_segmenter")
    assert seg["license"] == "Apache 2.0"
    assert seg["terms_url"].startswith("https://")
    haar = next(m for m in data["models"] if m["id"] == "opencv_haar_frontalface")
    assert haar["license"] == "BSD 3-Clause"
    assert data["required_checkboxes"][0]["id"] == "permissive_attribution_acknowledged"
    assert data["compressed_disclaimer"]
    assert data["biometric_responsibility_text"]


def test_confirm_accepts_all_and_get_then_lists_none(client):
    ids = [m["id"] for m in client.get(_URL).get_json()["models"]]
    resp = _post(client, {"model_ids": ids, "checkbox_responses": _ACK})
    assert resp.status_code == 200
    assert sorted(resp.get_json()["accepted"]) == sorted(ids)
    assert client.get(_URL).get_json()["models"] == []


def test_confirm_re_arms_loaded_gates(client):
    """An analysis running in the server process must see the acceptance
    without a restart."""
    from bpp.scoring.segmentation import _segmenter

    _segmenter._gate.reset()
    with pytest.raises(Exception):  # noqa: B017 — blocked before acceptance
        _segmenter._gate.enforce()
    assert (
        _post(
            client, {"model_ids": ["mediapipe_selfie_segmenter"], "checkbox_responses": _ACK}
        ).status_code
        == 200
    )
    _segmenter._gate.enforce()


def test_restricted_model_is_refused_with_400(client):
    resp = _post(
        client,
        {"model_ids": ["opencv_yunet", "insightface_scrfd_25g"], "checkbox_responses": _ACK},
    )
    assert resp.status_code == 400
    assert "not an attribution" in resp.get_json()["error"]
    assert "opencv_yunet" in [m["id"] for m in client.get(_URL).get_json()["models"]]


def test_unticked_box_is_refused_with_400(client):
    resp = _post(
        client,
        {
            "model_ids": ["opencv_yunet"],
            "checkbox_responses": {"permissive_attribution_acknowledged": False},
        },
    )
    assert resp.status_code == 400


def test_model_ids_must_be_a_list(client):
    resp = _post(client, {"model_ids": "opencv_yunet", "checkbox_responses": _ACK})
    assert resp.status_code == 400
