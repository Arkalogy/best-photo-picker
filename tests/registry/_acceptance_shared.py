"""Shared fixtures and helpers of the test_acceptance.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bpp.registry import (
    CANONICAL_DISCLAIMER_VERSION,
    LicenseClass,
    ModelEntry,
    ModelStatus,
    canonical_disclaimer_sha256,
)


@pytest.fixture
def isolated_log_path(tmp_path: Path, monkeypatch) -> Path:
    """Force the acceptance log into a tempdir for every test using
    this fixture."""
    target = tmp_path / "model-acceptance.jsonl"
    monkeypatch.setenv("BPP_ACCEPTANCE_LOG_PATH", str(target))
    return target


def _make_restricted_entry(entry_id: str = "test_restricted") -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        display_name="Restricted Test Model v1",
        kind="face_embedder",
        source_url="https://example.invalid/model.onnx",
        terms_url="https://example.invalid/LICENSE",
        # Restricted entries must have a permalink — see the
        # TestPermalinkValidation suite. Using a valid placeholder
        # so the rest of this fixture matches a real builtin.
        terms_permalink_url="https://example.invalid/abc123/LICENSE",
        terms_retrieved_at="2026-06-02",
        license_summary="Test restricted entry — non-commercial only",
        requires_explicit_ack=True,
        ack_text_version=CANONICAL_DISCLAIMER_VERSION,
        ack_text_sha256=canonical_disclaimer_sha256(),
        upstream_claimed_license_class=LicenseClass.RESEARCH_NON_COMMERCIAL,
        commercial_use_restriction_known=True,
        bppicker_commercial_default_allowed=False,
        commercial_unlock_requires_rights_assertion=True,
        status=ModelStatus.AVAILABLE,
        training_data="synthetic test corpus",
        weight_sha256="a" * 64,
        default_for_kind=False,
    )


def _make_permissive_entry(entry_id: str = "test_permissive") -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        display_name="Permissive Test Model",
        kind="face_embedder",
        source_url="https://example.invalid/permissive.onnx",
        terms_url="https://example.invalid/PERMISSIVE",
        terms_permalink_url=None,
        terms_retrieved_at="2026-06-02",
        license_summary="Test permissive entry",
        requires_explicit_ack=False,
        ack_text_version=CANONICAL_DISCLAIMER_VERSION,
        ack_text_sha256=canonical_disclaimer_sha256(),
        upstream_claimed_license_class=LicenseClass.APACHE_2_0,
        commercial_use_restriction_known=False,
        bppicker_commercial_default_allowed=True,
        commercial_unlock_requires_rights_assertion=False,
        status=ModelStatus.AVAILABLE,
        training_data="synthetic test corpus",
        weight_sha256="b" * 64,
        default_for_kind=False,
    )
