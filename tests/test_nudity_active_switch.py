"""The content-filter hint ("nudity detection is on") honours the
``model_nudity`` run switch, like the analysis does."""

from __future__ import annotations

from bpp.scoring import nudity


def test_a_switched_off_content_filter_is_not_active(monkeypatch):
    monkeypatch.setattr(nudity, "is_available", lambda: True)
    monkeypatch.setattr(nudity, "is_on_disk", lambda: True)
    monkeypatch.setattr(nudity._nudenet, "enforce_license", lambda: None)
    assert nudity.is_active({"model_nudity": True}) is True
    assert nudity.is_active({"model_nudity": "false"}) is False
    assert nudity.is_active({"model_nudity": False}) is False
