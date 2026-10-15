"""The picker and /api/v1/health report each model's real load state —
the same decision the load gate makes (including commercial use without
the separate-rights assertion, and withdrawn / legally blocked entries) —
instead of a green state while the model is quietly skipped."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bpp.registry.policy import ModelLoadDecision, PolicyResult


def _result(decision):
    return PolicyResult(decision=decision, reason="test", entry_id="x")


@pytest.mark.parametrize(
    ("decision", "accepted_before", "expected"),
    [
        (ModelLoadDecision.ALLOW, False, "ok"),
        (ModelLoadDecision.BLOCKED_NEEDS_ACK, False, "needs_license"),
        (ModelLoadDecision.BLOCKED_NEEDS_ACK, True, "needs_reaccept"),
        (ModelLoadDecision.BLOCKED_COMMERCIAL_NO_RIGHTS, True, "needs_commercial_rights"),
        (ModelLoadDecision.BLOCKED_LEGAL, False, "blocked"),
        (ModelLoadDecision.BLOCKED_WITHDRAWN, True, "blocked"),
    ],
)
def test_load_state_follows_the_load_gate(decision, accepted_before, expected):
    from bpp.registry.model_registry import get_entry
    from bpp.web.model_filter import load_state

    with (
        patch("bpp.registry.policy.check_model_load_allowed", return_value=_result(decision)),
        patch("bpp.registry.acceptance_log.has_accepted", return_value=accepted_before),
    ):
        assert load_state(get_entry("nudenet_320n")) == expected


def test_picker_entries_carry_the_load_state():
    from bpp.registry.model_registry import get_entry
    from bpp.web import bp_model_registry

    states = {"nudenet_320n": "needs_commercial_rights"}
    nudenet = bp_model_registry._entry_to_picker_dict(get_entry("nudenet_320n"), states=states)
    clip = bp_model_registry._entry_to_picker_dict(
        get_entry("openai_clip_vit_b32_onnx"), states=states
    )
    assert nudenet["load_state"] == "needs_commercial_rights"
    assert clip["load_state"] == "ok"


def _health(tmp_path, monkeypatch):
    from bpp.web.app import create_app

    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(tmp_path / "acceptance.jsonl"))
    workdir = tmp_path / "w"
    workdir.mkdir()
    app = create_app(workdir=str(workdir))
    app.config["TESTING"] = True
    return app.test_client().get("/api/v1/health").get_json()


def test_health_lists_models_the_gate_blocks_by_reason(tmp_path, monkeypatch):
    states = {
        "nudenet_320n": "needs_commercial_rights",
        "insightface_scrfd_25g": "needs_license",
        "ultralytics_yolov11n_pets": "needs_reaccept",
        "lama_inpaint_research": "blocked",
    }
    with patch(
        "bpp.web.model_filter.load_state", side_effect=lambda legal: states.get(legal.id, "ok")
    ):
        body = _health(tmp_path, monkeypatch)
    blocked = body["checks"]["models"]["license_blocked"]
    assert "nudenet_320n" in blocked["needs_commercial_rights"]
    assert "insightface_scrfd_25g" in blocked["needs_license"]
    assert "ultralytics_yolov11n_pets" in blocked["needs_reaccept"]
    assert "lama_inpaint_research" in blocked["blocked"]


def test_a_failing_license_check_degrades_health_instead_of_failing(tmp_path, monkeypatch):
    with patch("bpp.web.model_filter.load_state", side_effect=PermissionError(13, "denied", "/x")):
        body = _health(tmp_path, monkeypatch)
    assert body["status"] == "degraded"
    assert body["checks"]["models"] == {"error": "License check failed"}


def test_the_blocked_list_is_cached_until_licenses_or_entries_change(monkeypatch):
    import dataclasses

    from bpp.registry.model_registry import get_entry, register_entry
    from bpp.scoring import model_load_gate
    from bpp.web import model_filter

    model_filter._license_blocked_cache.clear()
    sig = {"v": 1}
    monkeypatch.setattr(model_load_gate, "_inputs_signature", lambda: (sig["v"],))
    with patch("bpp.web.model_filter.load_state", return_value="ok") as state:
        model_filter.license_blocked_models()
        calls = state.call_count
        model_filter.license_blocked_models()
        assert state.call_count == calls  # served from the cache
        sig["v"] = 2  # an accept / revoke rewrote the log
        model_filter.license_blocked_models()
        assert state.call_count > calls
        calls = state.call_count
        entry = get_entry("nudenet_320n")
        # A remote-registry overlay replaced the entry (e.g. new ack text).
        # Any field can change the decision (e.g. the commercial restriction).
        register_entry(
            dataclasses.replace(
                entry,
                commercial_use_restriction_known=not entry.commercial_use_restriction_known,
            )
        )
        try:
            model_filter.license_blocked_models()
            assert state.call_count > calls
        finally:
            register_entry(entry)
