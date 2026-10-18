"""Stale acceptances (accepted under an older acknowledgment text) are
NOT accepted anywhere in the UI, and the resulting skips are visible.

Regression: the canonical disclaimer bump (v3) made the load gate refuse
earlier acceptances, but the pre-analysis dialog (``has_accepted``,
version-blind) and the picker (any latest ``accept`` row) still showed
them as accepted — while pets / nudity scoring silently stopped.

Also covers: content-filter "active" vs "available", and that a revoke
(or acceptance) takes effect without a restart (SCRFD's memoized gate).
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path

import pytest

from bpp.registry import (
    UseContext,
    confirm_acceptance,
    get_entry,
    prepare_acceptance,
    set_use_context,
    utc_now_iso,
)
from bpp.registry.acceptance_log import append_row, find_latest_for_model

pytestmark = pytest.mark.no_preaccept_permissive


def _accept(model_id: str) -> None:
    set_use_context(UseContext.PERSONAL, set_via="test")
    entry = get_entry(model_id)
    assert entry is not None
    draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
    confirm_acceptance(
        draft,
        checkbox_responses={cb: True for cb, _ in draft.required_checkboxes},
        accepted_at=utc_now_iso(),
        separate_rights_asserted=False,
        source_of_rights_note="",
    )


def _make_stale(model_id: str) -> None:
    latest = find_latest_for_model(model_id)
    assert latest is not None
    append_row(dataclasses.replace(latest, ack_text_sha256="0" * 64, ack_text_version="old"))


@pytest.fixture
def client(tmp_path: Path):
    from bpp.web.app import create_app

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    (workdir / "analysis.json").write_text("[]", encoding="utf-8")
    app = create_app(workdir=str(workdir))
    app.config["TESTING"] = True
    return app.test_client()


# ── Pre-analysis dialog (model_filter) ──────────────────────────────


class TestLicenseState:
    def test_never_accepted(self) -> None:
        from bpp.web.model_filter import license_state

        assert license_state(get_entry("nudenet_320n")) == "needs_license"

    def test_current_acceptance(self) -> None:
        from bpp.web.model_filter import license_state

        _accept("nudenet_320n")
        assert license_state(get_entry("nudenet_320n")) == "ok"

    def test_stale_acceptance_needs_reaccept(self) -> None:
        from bpp.web.model_filter import license_state

        _accept("nudenet_320n")
        _make_stale("nudenet_320n")
        assert license_state(get_entry("nudenet_320n")) == "needs_reaccept"

    def test_permissive_is_ok(self) -> None:
        from bpp.web.model_filter import license_state

        assert license_state(get_entry("openai_clip_vit_b32_onnx")) == "ok"

    def test_stale_nudenet_is_blocked_with_reaccept_reason(self) -> None:
        from bpp.web.model_filter import compute_pending_and_blocked

        _accept("nudenet_320n")
        _make_stale("nudenet_320n")
        items, blocked = compute_pending_and_blocked()
        assert not any(i.get("legal_entry_id") == "nudenet_320n" for i in items)
        by_id = {b["legal_entry_id"]: b for b in blocked}
        assert by_id["nudenet_320n"]["reason"] == "needs_reaccept", by_id

    def test_scrfd_blocked_entry_marked_optional_research_only(self) -> None:
        from bpp.web.model_filter import compute_pending_and_blocked

        _, blocked = compute_pending_and_blocked()
        scrfd = [b for b in blocked if b["legal_entry_id"] == "insightface_scrfd_25g"]
        # Present when the weights aren't cached locally; either way,
        # when listed it must carry the optional/research-only markers.
        for b in scrfd:
            assert b["research_only"] is True
            assert b["default_for_kind"] is False


# ── Picker payload ──────────────────────────────────────────────────


class TestAcceptanceListFlags:
    def test_latest_row_flags_stale(self, client) -> None:
        _accept("nudenet_320n")
        _make_stale("nudenet_320n")
        rows = client.get("/api/v1/model-registry/acceptance/list").get_json()["acceptances"]
        nude = [r for r in rows if r["model_id"] == "nudenet_320n"]
        assert nude[0]["current"] is True and nude[0]["needs_reaccept"] is False
        assert nude[-1]["current"] is False and nude[-1]["needs_reaccept"] is True

    def test_revoke_row_is_neither_current_nor_reaccept(self, client) -> None:
        _accept("nudenet_320n")
        resp = client.post(
            "/api/v1/model-registry/acceptance/revoke", json={"model_id": "nudenet_320n"}
        )
        assert resp.status_code == 200
        rows = client.get("/api/v1/model-registry/acceptance/list").get_json()["acceptances"]
        last = [r for r in rows if r["model_id"] == "nudenet_320n"][-1]
        assert last["event"] == "revoke"
        assert last["current"] is False and last["needs_reaccept"] is False


# ── Visible skip (once per process) ─────────────────────────────────


class TestBlockedScorerLogsOnce:
    @pytest.mark.parametrize(
        ("scorer", "feature"),
        [("nudity", "Content filter (NudeNet)"), ("pets", "Pet detection (YOLO)")],
    )
    def test_info_once_not_per_photo(self, caplog, scorer: str, feature: str) -> None:
        import numpy as np

        from bpp.scoring import registry as scorer_registry
        from bpp.scoring.model_load_gate import _reset_blocked_log_for_tests

        _reset_blocked_log_for_tests()
        scorer_registry._wire_optional_scorers()
        fn = scorer_registry._SCORER_REGISTRY[scorer].score_fn
        img = np.zeros((32, 32, 3), dtype=np.uint8)
        with caplog.at_level(logging.DEBUG, logger="bpp.scoring.model_load_gate"):
            for _ in range(5):
                assert fn(img, "/nonexistent.jpg", {}) == {}
        infos = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO and r.name == "bpp.scoring.model_load_gate"
        ]
        assert len(infos) == 1, [r.getMessage() for r in infos]
        msg = infos[0].getMessage()
        assert feature in msg
        assert "Settings → Models" in msg


# ── Content filter active vs available ──────────────────────────────


class TestNudenetActive:
    def _point_at(self, monkeypatch, path: Path) -> None:
        from bpp.scoring import nudity

        monkeypatch.setattr(nudity._nudenet, "model_path", path)

    def test_inactive_without_acceptance_even_with_weights(self, tmp_path, monkeypatch) -> None:
        from bpp.scoring import nudity

        w = tmp_path / "320n.onnx"
        w.write_bytes(b"x")
        self._point_at(monkeypatch, w)
        assert nudity.is_available() is True
        assert nudity.is_active() is False

    def test_inactive_when_accepted_but_not_downloaded(self, tmp_path, monkeypatch) -> None:
        from bpp.scoring import nudity

        self._point_at(monkeypatch, tmp_path / "missing.onnx")
        _accept("nudenet_320n")
        assert nudity.is_active() is False

    def test_inactive_with_stale_acceptance(self, tmp_path, monkeypatch) -> None:
        from bpp.scoring import nudity

        w = tmp_path / "320n.onnx"
        w.write_bytes(b"x")
        self._point_at(monkeypatch, w)
        _accept("nudenet_320n")
        _make_stale("nudenet_320n")
        assert nudity.is_active() is False

    def test_active_when_accepted_and_on_disk(self, tmp_path, monkeypatch) -> None:
        from bpp.scoring import nudity

        w = tmp_path / "320n.onnx"
        w.write_bytes(b"x")
        self._point_at(monkeypatch, w)
        _accept("nudenet_320n")
        assert nudity.is_active() is True

    def test_status_endpoint_reports_both_flags(self, client) -> None:
        data = client.get("/api/v1/status").get_json()
        assert isinstance(data["nudenet_available"], bool)
        assert data["nudenet_active"] is False


# ── SCRFD gate reset + revoke without restart ───────────────────────


class TestScrfdGateReset:
    def test_registered_reset_rearms_gate(self) -> None:
        from bpp.scoring import face_scrfd
        from bpp.scoring.model_base import ModelRegistry

        _accept("insightface_scrfd_25g")
        assert face_scrfd.scrfd_accepted() is True
        assert face_scrfd._scrfd_gate.passed is True
        ModelRegistry.get("SCRFD 2.5g").reset()
        assert face_scrfd._scrfd_gate.passed is False

    def test_revoke_takes_effect_without_restart(self, client) -> None:
        from bpp.scoring import face_scrfd

        face_scrfd.reset_scrfd()
        _accept("insightface_scrfd_25g")
        assert face_scrfd.scrfd_accepted() is True  # gate memoizes the pass
        resp = client.post(
            "/api/v1/model-registry/acceptance/revoke",
            json={"model_id": "insightface_scrfd_25g"},
        )
        assert resp.status_code == 200
        assert face_scrfd._scrfd_gate.passed is False
        assert face_scrfd.scrfd_accepted() is False

    def test_revoke_rearms_every_gate_for_the_id(self) -> None:
        """Generic: dlib (no ModelRegistry entry) is re-armed too."""
        from bpp.scoring.face_embed_extractors import _dlib_gate
        from bpp.scoring.model_lifecycle import reset_runtime_for_legal_id

        _accept("dlib_face_recognition_resnet_v1")
        _dlib_gate.enforce()
        assert _dlib_gate.passed is True
        reset_runtime_for_legal_id("dlib_face_recognition_resnet_v1")
        assert _dlib_gate.passed is False
