"""Shared fixtures and helpers of the test_model_registry.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import pytest

from bpp.registry import (
    CANONICAL_DISCLAIMER_VERSION,
    LicenseClass,
    ModelEntry,
    ModelStatus,
    canonical_disclaimer_sha256,
)
from bpp.registry.model_registry import _reset_registry_for_tests


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    """Empty the global registry between tests so test order does not
    leak entries into each other's assertions."""
    _reset_registry_for_tests()


# ── Version-locked hash pin (audit trail) ──

#: Hash-version map for the canonical and BYOM disclaimers. The whole
#: audit trail ("we can prove what wording user X agreed to on
#: date Z") rests on each ``*_VERSION`` constant pinning ONE specific
#: text. A typo fix that changes the bytes without bumping the
#: version silently desyncs every historical acceptance row whose
#: hash was snapshotted under the old text.
#:
#: When you intentionally update the wording:
#:   1. Bump the ``_VERSION`` constant in disclaimers.py.
#:   2. Run this file with -p no:cacheprovider once and copy the new
#:      hash from the assertion failure into the table below.
#:   3. Commit the constant + the table edit in the same change.
_PINNED_DISCLAIMER_HASHES = {
    "canonical-disclaimer-v2": {
        "full": "a7346a755f33a9a4b1a6e4a08bcddce0dfca976b63baa15c2139254c1d00c5e5",
        "compressed": "45886a7a76c11a2815480b2aa5ce708d245bbadc5934271c510a1de177afa0a6",
    },
    "canonical-disclaimer-v3": {
        "full": "be9ef0766bad0861244817765c1b1b4627175eacf07541c69b11e2a4ec1a2687",
        "compressed": "45886a7a76c11a2815480b2aa5ce708d245bbadc5934271c510a1de177afa0a6",
    },
    "byom-disclaimer-v2": {
        "full": "61834c690bb43802b788a1ffd00937b8d5632b43ba01808f8a6995a85ec70793",
        "compressed": "08a299518ebb9c34320c0e31477e7e8a5a2d635097de7b1e740a2c9135abd270",
    },
}


# ── ModelEntry composition + label derivation ──


def _make_entry(
    *,
    entry_id: str = "test_entry",
    display_name: str = "Test Entry",
    license_class: LicenseClass = LicenseClass.MIT,
    commercial_use_restriction_known: bool = False,
    bppicker_commercial_default_allowed: bool = True,
    commercial_unlock_requires_rights_assertion: bool = False,
    status: ModelStatus = ModelStatus.AVAILABLE,
    requires_explicit_ack: bool = False,
    default_for_kind: bool = False,
) -> ModelEntry:
    return ModelEntry(
        id=entry_id,
        display_name=display_name,
        kind="face_embedder",
        source_url="https://example.invalid/model.onnx",
        terms_url="https://example.invalid/LICENSE",
        terms_permalink_url=None,
        terms_retrieved_at="2026-06-02",
        license_summary="License summary used in tests",
        requires_explicit_ack=requires_explicit_ack,
        ack_text_version=CANONICAL_DISCLAIMER_VERSION,
        ack_text_sha256=canonical_disclaimer_sha256(),
        upstream_claimed_license_class=license_class,
        commercial_use_restriction_known=commercial_use_restriction_known,
        bppicker_commercial_default_allowed=bppicker_commercial_default_allowed,
        commercial_unlock_requires_rights_assertion=commercial_unlock_requires_rights_assertion,
        status=status,
        training_data="LFW-derived",
        weight_sha256="0" * 64,
        default_for_kind=default_for_kind,
    )
