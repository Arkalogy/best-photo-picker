"""The analysis preflight reports not-yet-accepted models as one clear
notice — never as "download failed" / "unavailable" per model — and
never fetches their weights."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bpp.web import analyze_model_preflight as pf

pytestmark = pytest.mark.no_preaccept_permissive


@pytest.fixture
def empty_model_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("BPP_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setenv("BPP_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))


def _run() -> list[dict]:
    events: list[dict] = []
    with patch("bpp.utils.download.download_file", side_effect=AssertionError("network")) as dl:
        pf.preflight_models(events.append)
    dl.assert_not_called()
    return events


def test_unaccepted_models_get_one_notice_and_no_failure_warnings(empty_model_dirs):
    events = _run()
    warnings = [e["message"] for e in events if e.get("type") == "warning"]
    notices = [w for w in warnings if "accept" in w.lower()]
    assert len(notices) == 1, warnings
    assert "Settings → Models" in notices[0]
    assert not [w for w in warnings if "unavailable" in w or "failed" in w], warnings


def test_no_notice_once_everything_is_accepted(empty_model_dirs):
    from bpp.registry.attribution_bundle import (
        confirm_attribution_bundle,
        pending_attribution_entries,
    )

    confirm_attribution_bundle(
        [e.id for e in pending_attribution_entries()],
        checkbox_responses={"permissive_attribution_acknowledged": True},
        accepted_at="2026-10-14T00:00:00+00:00",
    )
    with patch.object(pf, "_download_accepted_models") as dl:
        events: list[dict] = []
        pf.preflight_models(events.append)
    dl.assert_called_once()
    assert not [e for e in events if "accept" in str(e.get("message", "")).lower()]
