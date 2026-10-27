# ruff: noqa: F811
"""A plugin model's on/off switch reaches the picker: its registry entry
carries the label of its plugin-model row (which holds toggle_key and
enabled), so the row menu and the Off status work for plugins too. And
the status endpoint's content-filter flag follows its switch."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from tests._web_shared import web_client  # noqa: F401


def test_a_plugin_entry_carries_the_label_of_its_plugin_model_row():
    from bpp.web import picker_fields

    entry = SimpleNamespace(id="acme_detector_v1", kind="face_detector")
    det = SimpleNamespace(registry_id="acme_detector_v1", toggle_key="model_acme")
    model = SimpleNamespace(name="acme.onnx")
    with (
        patch("bpp.scoring.face_detector_registry.list_detectors", return_value=[det]),
        patch("bpp.scoring.model_lifecycle.plugin_model_entries", return_value=[model]),
        patch("bpp.scoring.model_lifecycle.fetchable_legal_id", return_value="acme_detector_v1"),
    ):
        fields = picker_fields.picker_fields(entry)
    assert fields["toggle_key"] == "model_acme"
    assert fields["legacy_label"] == "Plugin model: acme.onnx"


def test_status_reports_the_content_filter_off_when_its_switch_is_off(web_client):
    with patch("bpp.scoring.nudity.is_available", return_value=True):
        from bpp.web.state import get_ctx

        with web_client.application.app_context():
            get_ctx().config["model_nudity"] = False
        body = web_client.get("/api/v1/status").get_json()
    assert body["nudenet_active"] is False
