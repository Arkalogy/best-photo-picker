"""Pin every load-bearing policy decision on the buffalo_s entry.

buffalo_s is the first restricted-license face embedder in the
bundled baseline. Every flag on the entry maps to a downstream
runtime gate (click-through dialog, commercial-use hard-block,
dual-signature requirement on relaxation, derived-data purge on
removal). A careless edit of the registry entry that flipped one
of these flags would silently change BPP's legal posture without
any other code visibly breaking — so we pin them here.

A future edit that legitimately changes one of these flags MUST
go through MODEL_POLICY.md's dual-sig review and update the test
in the same PR.
"""

from __future__ import annotations

from bpp.registry import (
    LicenseClass,
    ModelStatus,
    get_default_for_kind,
    get_entry,
)


class TestBuffaloSPolicyFlags:
    """Every restriction-bearing flag on the entry is pinned. The
    plain-English explanation alongside each assertion is what a
    future maintainer reading the test will need to decide whether
    a proposed change is safe."""

    def test_entry_is_registered_with_expected_id(self) -> None:
        entry = get_entry("insightface_buffalo_s")
        assert entry is not None, (
            "buffalo_s is no longer in the bundled baseline. If you "
            "removed it intentionally, also remove this test file."
        )

    def test_buffalo_s_is_not_the_default(self) -> None:
        """SFace is the permanent default per item 1 of the legal-
        posture plan; the restricted entries never become the
        default-for-kind."""
        default = get_default_for_kind("face_embedder")
        assert default is not None
        assert default.id == "sface_yunet", (
            "Default face embedder is no longer SFace — that's a "
            "Batch 2 / item 1 invariant. A restricted entry must "
            "NEVER be the default."
        )

    def test_requires_explicit_acknowledgment(self) -> None:
        """The click-through dialog fires only when this flag is set.
        Turning it off would silently use buffalo_s with no user
        acknowledgment of the non-commercial license."""
        entry = get_entry("insightface_buffalo_s")
        assert entry.requires_explicit_ack is True

    def test_commercial_use_restriction_known(self) -> None:
        """When this is True, the policy layer can hard-block the
        entry in commercial-use mode. Flipping to False would let
        commercial users select buffalo_s with no warning."""
        entry = get_entry("insightface_buffalo_s")
        assert entry.commercial_use_restriction_known is True

    def test_bppicker_commercial_default_disallowed(self) -> None:
        """Without separately-asserted rights, the entry refuses to
        load in commercial mode (the hard-block path). Flipping to
        True would allow buffalo_s commercial-by-default."""
        entry = get_entry("insightface_buffalo_s")
        assert entry.bppicker_commercial_default_allowed is False

    def test_commercial_unlock_requires_rights_assertion(self) -> None:
        """The 'I have obtained separate commercial rights' escape
        hatch requires this flag. Flipping to False would let
        commercial-mode users load buffalo_s without asserting the
        out-of-band rights chain."""
        entry = get_entry("insightface_buffalo_s")
        assert entry.commercial_unlock_requires_rights_assertion is True

    def test_license_class_is_research_non_commercial(self) -> None:
        """The license-drift lock in the overlay merger uses this
        field. Any relaxation (e.g. flipping to apache_2_0) requires
        two valid signatures."""
        entry = get_entry("insightface_buffalo_s")
        assert entry.upstream_claimed_license_class is LicenseClass.RESEARCH_NON_COMMERCIAL

    def test_status_is_available(self) -> None:
        """Status-tightening (available → withdrawn_no_new_downloads
        → legally_blocked) is single-signature safe. Starting in
        AVAILABLE is the only way the upstream-takedown channel can
        later flip the entry into a more-restrictive state."""
        entry = get_entry("insightface_buffalo_s")
        assert entry.status is ModelStatus.AVAILABLE

    def test_ack_text_kind_is_canonical(self) -> None:
        """Restricted built-ins use the canonical 4-checkbox dialog
        (BYOM uses its own shorter dialog). The discriminator routes
        the click-through code path."""
        entry = get_entry("insightface_buffalo_s")
        assert entry.ack_text_kind == "canonical"

    def test_terms_permalink_url_is_present(self) -> None:
        """The acceptance log records a permalink to the exact license
        text the user saw at acceptance time. Without it, a future
        upstream README rewording would make the acceptance row
        unverifiable."""
        entry = get_entry("insightface_buffalo_s")
        assert entry.terms_permalink_url is not None
        assert entry.terms_permalink_url.startswith("https://")
