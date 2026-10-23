"""The registry endpoints and the load gate agree on a model's state:
commercial use needs the rights assertion before a model shows as OK, a
withdrawn / legally blocked model can't be "accepted", and a broken
license check can't take the whole picker down."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bpp.registry import REQUIRED_ACK_CHECKBOXES, canonical_disclaimer_sha256
from bpp.registry.model_registry import (
    LicenseClass,
    ModelEntry,
    ModelStatus,
    register_entry,
)

_ID = "test_load_state_entry"
_BOXES = {cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES}


def _entry(status=ModelStatus.AVAILABLE) -> ModelEntry:
    return ModelEntry(
        id=_ID,
        display_name="Test restricted model",
        kind="face_embedder",
        source_url="https://example.invalid/model.onnx",
        terms_url="https://example.invalid/LICENSE",
        terms_permalink_url="https://example.invalid/abc/LICENSE",
        terms_retrieved_at="2026-06-02",
        license_summary="test entry",
        requires_explicit_ack=True,
        ack_text_version="canonical-disclaimer-v2",
        ack_text_sha256=canonical_disclaimer_sha256(),
        upstream_claimed_license_class=LicenseClass.RESEARCH_NON_COMMERCIAL,
        commercial_use_restriction_known=True,
        bppicker_commercial_default_allowed=False,
        commercial_unlock_requires_rights_assertion=True,
        status=status,
        training_data="synthetic",
        weight_sha256="a" * 64,
        default_for_kind=False,
    )


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


def _confirm(client, *, use_context="commercial", rights=False):
    resp = client.post(
        "/api/v1/model-registry/acceptance/confirm",
        data=json.dumps(
            {
                "model_id": _ID,
                "use_context": use_context,
                "checkbox_responses": _BOXES,
                "accepted_at": "2026-10-14T12:00:00+00:00",
                "separate_rights_asserted": rights,
            }
        ),
        headers={"Content-Type": "application/json"},
    )
    return resp.status_code, resp.get_json()


def _picker_state(client) -> str | None:
    groups = client.get("/api/v1/model-registry/entries").get_json()["groups"]
    for g in groups:
        for e in g["entries"]:
            if e["id"] == _ID:
                return e.get("load_state")
    raise AssertionError("entry missing from the picker")


def test_commercial_use_needs_the_rights_assertion_end_to_end(client, monkeypatch, tmp_path):
    from bpp.registry.use_context_store import UseContext, set_use_context

    monkeypatch.setenv("BPP_USE_CONTEXT_PATH", str(tmp_path / "use-context.json"))
    register_entry(_entry())
    set_use_context(UseContext.COMMERCIAL, set_via="test")
    assert _picker_state(client) == "needs_license"
    status, _ = _confirm(client, rights=False)
    assert status == 200
    assert _picker_state(client) == "needs_commercial_rights"
    status, _ = _confirm(client, rights=True)
    assert status == 200
    assert _picker_state(client) == "ok"


def test_a_withdrawn_model_cannot_be_accepted(client):
    register_entry(_entry(status=ModelStatus.LEGALLY_BLOCKED))
    status, body = _confirm(client, use_context="personal")
    assert status == 400
    assert "can't be enabled" in body["error"]


def test_a_failing_license_check_degrades_one_row_not_the_picker(client):
    register_entry(_entry())
    with patch("bpp.web.model_filter.load_state", side_effect=PermissionError(13, "denied", "/x")):
        resp = client.get("/api/v1/model-registry/entries")
    assert resp.status_code == 200
    rows = [e for g in resp.get_json()["groups"] for e in g["entries"]]
    assert all(r.get("load_state") is None for r in rows)  # the picker falls back


def test_refusing_a_withdrawn_model_reads_no_license_files(client):
    register_entry(_entry(status=ModelStatus.LEGALLY_BLOCKED))
    with patch("bpp.web.model_filter.load_state", side_effect=AssertionError("reads files")):
        status, body = _confirm(client, use_context="personal")
    assert status == 400 and "can't be enabled" in body["error"]


def test_accepting_an_available_model_does_not_depend_on_the_load_state(client):
    register_entry(_entry())
    with patch("bpp.web.model_filter.load_state", side_effect=PermissionError(13, "x", "/x")):
        status, _ = _confirm(client, use_context="personal")
    assert status == 200


def test_the_picker_reads_cached_states(client):
    from bpp.web import model_filter

    register_entry(_entry())
    model_filter._license_blocked_cache.clear()
    with patch("bpp.web.model_filter.load_state", return_value="ok") as state:
        client.get("/api/v1/model-registry/entries")
        first = state.call_count
        client.get("/api/v1/model-registry/entries")
    assert state.call_count == first  # second load served from the cache


def test_content_filter_status_endpoint(client):
    with patch("bpp.scoring.nudity.is_active", return_value=True):
        assert client.get("/api/v1/models/content-filter").get_json() == {"active": True}


def test_a_user_supplied_model_gets_its_own_load_state(client, tmp_path, monkeypatch):
    from bpp.registry.byom import add_byom_entry

    monkeypatch.setenv("BPP_BYOM_PATH", str(tmp_path / "byom-models.json"))
    fp = tmp_path / "mine.onnx"
    fp.write_bytes(b"fake-model")
    byom = add_byom_entry(display_name="My model", kind="face_embedder", file_path=fp)

    def state():
        groups = client.get("/api/v1/model-registry/entries").get_json()["groups"]
        return next(e for g in groups for e in g["entries"] if e["id"] == byom.id)["load_state"]

    assert state() == "needs_license"  # not in the registry map: computed, not "ok"
    resp = client.post(
        "/api/v1/model-registry/acceptance/confirm",
        data=json.dumps(
            {
                "model_id": byom.id,
                "use_context": "personal",
                "checkbox_responses": {"byom_user_takes_responsibility": True},
                "accepted_at": "2026-10-14T12:00:00+00:00",
            }
        ),
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert state() == "ok"


def test_the_cache_is_replaced_in_one_step():
    from bpp.web import model_filter

    model_filter._license_blocked_cache.clear()
    with patch("bpp.web.model_filter.load_state", return_value="needs_license"):
        model_filter.model_load_states()
    # One entry holding (signature, states) — a reader can't see a new
    # signature with the old states.
    assert set(model_filter._license_blocked_cache) == {"entry"}
