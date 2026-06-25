"""Tests for the ONNX Runtime provider helper.

The helper is the single source of truth for which execution providers
:func:`onnxruntime.InferenceSession` is constructed with. All three
ONNX session creation sites (SCRFD, CLIP, pets YOLO) route through it.
These tests pin two contracts:

  * **Default behaviour is unchanged from pre-helper code** — without
    the env var, providers list is exactly ``["CPUExecutionProvider"]``.
    A regression here would silently change every user's inference
    backend.
  * **CPU is always present as the final fallback.** Even if the user
    requests a hardware-accelerated provider that isn't compiled into
    their onnxruntime wheel, the session can still load on CPU.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("BPP_ONNX_PROVIDERS", raising=False)


def test_default_is_cpu_only():
    from bpp.scoring.onnx_providers import get_providers

    assert get_providers() == ["CPUExecutionProvider"]


def test_blank_env_is_cpu_only(monkeypatch):
    from bpp.scoring.onnx_providers import get_providers

    monkeypatch.setenv("BPP_ONNX_PROVIDERS", "  ")
    assert get_providers() == ["CPUExecutionProvider"]


def test_explicit_cpu_is_idempotent(monkeypatch):
    from bpp.scoring.onnx_providers import get_providers

    monkeypatch.setenv("BPP_ONNX_PROVIDERS", "CPUExecutionProvider")
    assert get_providers() == ["CPUExecutionProvider"]


def test_unknown_provider_filtered_with_cpu_fallback(monkeypatch, caplog):
    from bpp.scoring.onnx_providers import get_providers

    monkeypatch.setenv("BPP_ONNX_PROVIDERS", "FakeProvider,AlsoFake")
    with caplog.at_level("WARNING", logger="bpp.scoring.onnx_providers"):
        result = get_providers()
    # CPU is always appended as a fallback so the session still loads.
    assert result == ["CPUExecutionProvider"]
    # Both unknowns logged so the user can debug.
    msgs = " ".join(r.message for r in caplog.records)
    assert "FakeProvider" in msgs
    assert "AlsoFake" in msgs


def test_known_providers_filtered_against_wheel(monkeypatch, caplog):
    """When user requests CoreML on a Linux box, the wheel's
    available_providers won't include it; the helper drops it with
    a warning rather than passing it to ONNX Runtime (which would
    crash session construction on some versions)."""
    from bpp.scoring import onnx_providers

    # Pretend we're on a wheel that only ships CPU (e.g. ubuntu CI).
    monkeypatch.setattr(onnx_providers, "_available_providers", lambda: {"CPUExecutionProvider"})
    monkeypatch.setenv("BPP_ONNX_PROVIDERS", "CoreMLExecutionProvider")
    with caplog.at_level("WARNING", logger="bpp.scoring.onnx_providers"):
        result = onnx_providers.get_providers()
    assert result == ["CPUExecutionProvider"]
    assert any("CoreMLExecutionProvider" in r.message for r in caplog.records)


def test_priority_order_preserved(monkeypatch):
    """Provider priority is significant — ONNX Runtime tries them in
    order. CoreML before CPU means GPU-eligible nodes prefer the GPU,
    everything else falls back to CPU."""
    from bpp.scoring import onnx_providers

    # Pretend the wheel has both providers available.
    monkeypatch.setattr(
        onnx_providers,
        "_available_providers",
        lambda: {"CoreMLExecutionProvider", "CPUExecutionProvider"},
    )
    monkeypatch.setenv("BPP_ONNX_PROVIDERS", "CoreMLExecutionProvider,CPUExecutionProvider")
    assert onnx_providers.get_providers() == [
        "CoreMLExecutionProvider",
        "CPUExecutionProvider",
    ]


def test_all_three_session_sites_call_helper():
    """Source-scan: SCRFD, CLIP, pets all route through get_providers
    instead of hardcoding `providers=["CPUExecutionProvider"]`. A
    refactor that drops the helper at any site would silently lose
    the opt-in path for that model."""
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    for rel in (
        "bpp/scoring/face_scrfd.py",
        "bpp/scoring/clip_embed.py",
        "bpp/scoring/pets.py",
    ):
        src = (repo / rel).read_text()
        assert "get_providers" in src, f"{rel} no longer routes through get_providers"
        # Inverse guard: make sure the hardcoded list is gone.
        assert 'providers=["CPUExecutionProvider"]' not in src, (
            f"{rel} still hardcodes providers — should call get_providers() instead"
        )
