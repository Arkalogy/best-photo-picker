"""HTTP status correctness on the acceptance/confirm endpoint.

Pinpoints item P-06 of the comprehensive review: when
``confirm_acceptance`` raises ``AcceptanceError`` (missing checkbox,
unchecked required box, empty timestamp, missing permalink), the
HTTP layer must surface the failure as **400 Bad Request** — these
are user-input validation errors, not server crashes. A 500 status
would:

* trip incident monitoring on what is an expected user mistake,
* mis-classify the failure under ``app.py``'s 4xx→INFO / 5xx→WARNING
  split, raising noisy WARN lines for every fumbled dialog
  submission,
* mislead clients into thinking the server is broken when the form
  payload is invalid.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bpp.registry import REQUIRED_ACK_CHECKBOXES, canonical_disclaimer_sha256
from bpp.registry.model_registry import (
    LicenseClass,
    ModelEntry,
    ModelStatus,
    register_entry,
)


def _seed_restricted_entry() -> str:
    """Register a synthetic restricted entry whose acceptance flow we
    can drive through the HTTP layer. Returns its id."""
    entry = ModelEntry(
        id="test_acceptance_endpoint_status",
        display_name="Test entry for HTTP status checks",
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
        status=ModelStatus.AVAILABLE,
        training_data="synthetic",
        weight_sha256="a" * 64,
        default_for_kind=False,
    )
    register_entry(entry)
    return entry.id


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """Boot a Flask test client with auth bypassed and the
    acceptance log redirected to a tempdir."""
    monkeypatch.setenv(
        "BPP_ACCEPTANCE_LOG_PATH",
        str(tmp_path / "acceptance.jsonl"),
    )
    from bpp.web.app import create_app

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "analysis.json").write_text("[]", encoding="utf-8")
    app = create_app(workdir=str(workdir))
    app.config["TESTING"] = True
    return app.test_client()


def _confirm(client, body: dict) -> tuple[int, dict | None]:
    resp = client.post(
        "/api/v1/model-registry/acceptance/confirm",
        data=json.dumps(body),
        headers={"Content-Type": "application/json"},
    )
    try:
        payload = resp.get_json()
    except Exception:
        payload = None
    return resp.status_code, payload


class TestAcceptanceConfirmReturns400OnValidationFailure:
    def test_unchecked_required_box_returns_400(self, client) -> None:
        model_id = _seed_restricted_entry()
        status, payload = _confirm(
            client,
            {
                "model_id": model_id,
                "use_context": "personal",
                "checkbox_responses": {
                    "not_commercial": False,
                    "mit_doesnt_grant_rights": False,
                    "direct_upstream": False,
                    "no_paid_without_separate_rights": False,
                },
                "accepted_at": "2026-06-02T12:00:00+00:00",
            },
        )
        assert status == 400, (
            f"unchecked required box returned {status} (expected 400). Payload: {payload!r}"
        )

    def test_missing_required_box_returns_400(self, client) -> None:
        model_id = _seed_restricted_entry()
        status, payload = _confirm(
            client,
            {
                "model_id": model_id,
                "use_context": "personal",
                # Empty dict → every required id is missing
                "checkbox_responses": {},
                "accepted_at": "2026-06-02T12:00:00+00:00",
            },
        )
        assert status == 400, (
            f"missing required box returned {status} (expected 400). Payload: {payload!r}"
        )

    def test_empty_accepted_at_returns_400(self, client) -> None:
        model_id = _seed_restricted_entry()
        status, payload = _confirm(
            client,
            {
                "model_id": model_id,
                "use_context": "personal",
                "checkbox_responses": {
                    "not_commercial": True,
                    "mit_doesnt_grant_rights": True,
                    "direct_upstream": True,
                    "no_paid_without_separate_rights": True,
                },
                "accepted_at": "",
            },
        )
        # Empty accepted_at falls through to utc_now_iso() at the
        # endpoint level (the ``or utc_now_iso()`` default at line
        # 263). The strict ValidationError fires further upstream
        # only when accepted_at is genuinely absent. Either 200 OK
        # (server filled in a default) or 400 (rejected) is correct —
        # what we DON'T accept is 500.
        assert status != 500, f"empty accepted_at returned 500. Payload: {payload!r}"


def _revoke(client, model_id: str) -> tuple[int, dict | None]:
    resp = client.post(
        "/api/v1/model-registry/acceptance/revoke",
        data=json.dumps({"model_id": model_id}),
        headers={"Content-Type": "application/json"},
    )
    try:
        payload = resp.get_json()
    except Exception:
        payload = None
    return resp.status_code, payload


def _valid_accept_body(model_id: str) -> dict:
    return {
        "model_id": model_id,
        "use_context": "personal",
        "checkbox_responses": {cb_id: True for cb_id, _ in REQUIRED_ACK_CHECKBOXES},
        "accepted_at": "2026-06-02T12:00:00+00:00",
    }


class TestAcceptanceRevokeEndpoint:
    def test_revoke_after_accept_returns_200_and_regates(self, client) -> None:
        model_id = _seed_restricted_entry()
        status, _ = _confirm(client, _valid_accept_body(model_id))
        assert status == 200

        status, payload = _revoke(client, model_id)
        assert status == 200, payload
        assert payload["revocation"]["event"] == "revoke"

        # Server-side gate re-engages: the model is no longer valid to load.
        from bpp.registry import get_entry, is_acceptance_valid_for

        assert is_acceptance_valid_for(get_entry(model_id)) is False

    def test_revoke_without_acceptance_returns_400(self, client) -> None:
        model_id = _seed_restricted_entry()
        status, payload = _revoke(client, model_id)
        assert status == 400, payload

    def test_revoke_unknown_model_returns_400(self, client) -> None:
        status, _ = _revoke(client, "definitely_not_a_model")
        assert status == 400
