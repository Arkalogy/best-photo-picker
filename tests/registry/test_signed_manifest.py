"""Batch 8 — Ed25519 manifest verifier tests.

Pins:

* :func:`canonical_manifest_bytes` is stable across dict ordering
  and ignores the ``signatures`` field, so signers and verifiers
  hash exactly the same bytes.
* :func:`sign_manifest` round-trips: a manifest signed with a
  private key verifies under the matching public key, fails under
  a different key, and fails after any byte of the manifest is
  tampered with.
* :func:`verify_manifest` rejects an unsigned, partially signed,
  or wrong-key-signed manifest, and accepts a correctly-signed
  manifest with the right key in the trust set.
* Trap-T7 rule at the merge layer: a remote manifest cannot relax
  ``commercial_use_restriction_known`` from True to False without
  two valid signatures.
"""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from bpp.registry import (
    LicenseClass,
    ManifestVerificationResult,
    ModelEntry,
    ModelStatus,
    TrustedKey,
    canonical_manifest_bytes,
    sign_manifest,
    verify_manifest,
)
from bpp.registry.builtins import register_builtins
from bpp.registry.model_registry import _reset_registry_for_tests
from bpp.registry.overlay import DUAL_SIG_REQUIREMENT, apply_overlay


@pytest.fixture(autouse=True)
def _restore_registry_after_test():
    """Restore the registry to the bundled-baseline state after every
    test. Tests in this file mutate the registry (via
    ``_reset_registry_for_tests()`` + ``register_entry`` for the
    restricted baselines they need); without this teardown, downstream
    test files would see an empty registry — pytest-randomly's order
    randomization makes that a load-bearing flake."""
    yield
    _reset_registry_for_tests()
    register_builtins()


def _generate_key() -> tuple[str, Ed25519PrivateKey, TrustedKey]:
    """Return ``(key_id, private_key, trusted_key)`` for a fresh
    random keypair so each test exercises real Ed25519 signing
    without depending on a fixed test key on disk."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    raw_pub = pub.public_bytes_raw()
    key_id = "test-key-" + base64.b64encode(raw_pub[:6]).decode("ascii")
    trusted = TrustedKey(
        key_id=key_id,
        public_key_b64=base64.b64encode(raw_pub).decode("ascii"),
        maintainer_label="test",
    )
    return key_id, priv, trusted


def _base_manifest() -> dict:
    return {
        "schema_version": 1,
        "generated_at": "2026-06-03T00:00:00+00:00",
        "entries": [
            {
                "id": "test_permissive",
                "display_name": "Test permissive",
                "kind": "face_embedder",
                "source_url": "https://example.invalid/p.onnx",
                "terms_url": "https://example.invalid/L",
                "terms_permalink_url": None,
                "terms_retrieved_at": "2026-06-03",
                "license_summary": "Permissive test entry",
                "requires_explicit_ack": False,
                "ack_text_version": "canonical-disclaimer-v1",
                "ack_text_sha256": "0" * 64,
                "upstream_claimed_license_class": "apache_2_0",
                "commercial_use_restriction_known": False,
                "bppicker_commercial_default_allowed": True,
                "commercial_unlock_requires_rights_assertion": False,
                "status": "available",
                "training_data": "synthetic",
                "weight_sha256": "0" * 64,
                "default_for_kind": False,
                "ack_text_kind": "canonical",
            }
        ],
    }


# ── canonical_manifest_bytes ──


class TestCanonicalSerialization:
    def test_is_stable_across_dict_ordering(self) -> None:
        a = {"schema_version": 1, "entries": [], "signatures": []}
        b = {"signatures": [], "entries": [], "schema_version": 1}
        assert canonical_manifest_bytes(a) == canonical_manifest_bytes(b)

    def test_ignores_existing_signatures(self) -> None:
        """Signatures shouldn't change canonical bytes — that's how
        sign-then-verify can hash the same payload."""
        a = {"schema_version": 1, "entries": [], "signatures": []}
        b = {
            "schema_version": 1,
            "entries": [],
            "signatures": [{"key_id": "x", "signature_b64": "y"}],
        }
        assert canonical_manifest_bytes(a) == canonical_manifest_bytes(b)

    def test_any_entry_change_changes_bytes(self) -> None:
        a = {
            "schema_version": 1,
            "entries": [{"id": "x"}],
            "signatures": None,
        }
        b = {
            "schema_version": 1,
            "entries": [{"id": "y"}],
            "signatures": None,
        }
        assert canonical_manifest_bytes(a) != canonical_manifest_bytes(b)


# ── sign + verify round-trip ──


class TestSignAndVerifyRoundTrip:
    def test_signed_manifest_verifies(self) -> None:
        key_id, priv, trusted = _generate_key()
        signed = sign_manifest(_base_manifest(), [(key_id, priv)])
        result = verify_manifest(signed, trusted_keys=[trusted])
        assert isinstance(result, ManifestVerificationResult)
        assert result.is_valid
        assert key_id in result.valid_signatures

    def test_same_key_signing_twice_counts_once(self) -> None:
        """A manifest signed by ONE key twice must yield a single valid
        signature, not two. The overlay merger gates restriction downgrades on
        the signature COUNT as a proxy for distinct trusted keys; counting the
        duplicate would let one key (or one compromised key) fake a multi-key
        quorum and clear a >1 requirement on its own."""
        key_id, priv, trusted = _generate_key()
        signed = sign_manifest(_base_manifest(), [(key_id, priv), (key_id, priv)])
        # Two signature entries are present on the wire...
        assert len(signed["signatures"]) == 2
        result = verify_manifest(signed, trusted_keys=[trusted])
        assert result.is_valid
        # ...but they collapse to one distinct trusted key.
        assert result.valid_signatures == (key_id,), result.valid_signatures

    def test_wrong_key_fails_verification(self) -> None:
        key_id, priv, _ = _generate_key()
        _, _, wrong_trusted = _generate_key()
        signed = sign_manifest(_base_manifest(), [(key_id, priv)])
        result = verify_manifest(signed, trusted_keys=[wrong_trusted])
        assert not result.is_valid
        # The signature was against a key NOT in trusted_keys, so the
        # verifier marks the key_id as untrusted.
        assert any(
            "not in the bundled trusted-key set" in err or "does not verify" in err
            for err in result.errors
        )

    def test_tampered_manifest_fails_verification(self) -> None:
        key_id, priv, trusted = _generate_key()
        signed = sign_manifest(_base_manifest(), [(key_id, priv)])
        # Tamper the entries list AFTER signing.
        signed["entries"][0]["display_name"] = "TAMPERED"
        result = verify_manifest(signed, trusted_keys=[trusted])
        assert not result.is_valid
        assert any("does not verify" in err for err in result.errors)

    def test_unsigned_manifest_fails_verification(self) -> None:
        _, _, trusted = _generate_key()
        manifest = _base_manifest()
        manifest["signatures"] = []
        result = verify_manifest(manifest, trusted_keys=[trusted])
        assert not result.is_valid
        assert any(
            "no signatures" in err.lower() or "no signature" in err.lower() for err in result.errors
        )


# ── Trap T7: dual-sig enforcement at the merge layer ──


class TestOverlayDualSigEnforcement:
    """The overlay merger refuses to relax a restriction class
    (commercial_use_restriction_known: True → False, or
    requires_explicit_ack: True → False) without two valid
    signatures. Pins the rule at the merge layer; the runtime
    guard via assert_no_silent_reclassification provides a second
    line of defence."""

    @pytest.fixture(autouse=True)
    def _isolate_registry(self) -> None:
        from bpp.registry.policy import (
            _reset_reclassification_lock_for_tests,
        )

        # Reset both the registry and the T7 lock so each test
        # starts from a clean baseline.
        _reset_registry_for_tests()
        _reset_reclassification_lock_for_tests()

    def _seed_restricted_baseline(self) -> ModelEntry:
        from bpp.registry import register_entry

        entry = ModelEntry(
            id="downgrade_target",
            display_name="Restricted Test",
            kind="face_embedder",
            source_url="",
            terms_url="",
            terms_permalink_url=None,
            terms_retrieved_at="",
            license_summary="restricted test",
            requires_explicit_ack=True,
            ack_text_version="v1",
            ack_text_sha256="0" * 64,
            upstream_claimed_license_class=LicenseClass.RESEARCH_NON_COMMERCIAL,
            commercial_use_restriction_known=True,
            bppicker_commercial_default_allowed=False,
            commercial_unlock_requires_rights_assertion=True,
            status=ModelStatus.AVAILABLE,
            training_data="",
            weight_sha256="0" * 64,
            default_for_kind=False,
        )
        register_entry(entry)
        return entry

    @pytest.mark.skipif(
        DUAL_SIG_REQUIREMENT < 2,
        reason="no 'valid but insufficient' band when the requirement is 1 "
        "(single-operator mode); re-engages automatically when bumped to 2",
    )
    def test_below_quorum_cannot_relax_restriction(self) -> None:
        baseline = self._seed_restricted_baseline()
        relaxed_raw = {
            "id": baseline.id,
            "display_name": baseline.display_name,
            "kind": baseline.kind,
            "requires_explicit_ack": False,  # RELAXED
            "ack_text_version": "v1",
            "ack_text_sha256": "0" * 64,
            "upstream_claimed_license_class": "research_non_commercial",
            "commercial_use_restriction_known": False,  # RELAXED
            "bppicker_commercial_default_allowed": True,
            "commercial_unlock_requires_rights_assertion": False,
            "status": "available",
            "training_data": "",
            "weight_sha256": "0" * 64,
            "default_for_kind": False,
            "ack_text_kind": "canonical",
        }
        # One short of the quorum must not relax.
        result = apply_overlay([relaxed_raw], valid_signature_count=DUAL_SIG_REQUIREMENT - 1)
        assert baseline.id in result.skipped_ids
        # Baseline is untouched.
        from bpp.registry import get_entry

        assert get_entry(baseline.id).commercial_use_restriction_known is True

    def test_quorum_allows_relaxation(self) -> None:
        baseline = self._seed_restricted_baseline()
        # Reset T7 lock so a successful relaxation can land via the
        # signed path (the lock is the runtime second line of
        # defence; the test here is for the merge-layer rule).
        from bpp.registry.policy import (
            _reset_reclassification_lock_for_tests,
        )

        _reset_reclassification_lock_for_tests()
        relaxed_raw = {
            "id": baseline.id,
            "display_name": baseline.display_name,
            "kind": baseline.kind,
            "requires_explicit_ack": False,
            "ack_text_version": "v1",
            "ack_text_sha256": "0" * 64,
            "upstream_claimed_license_class": "research_non_commercial",
            "commercial_use_restriction_known": False,
            "bppicker_commercial_default_allowed": True,
            "commercial_unlock_requires_rights_assertion": False,
            "status": "available",
            "training_data": "",
            "weight_sha256": "0" * 64,
            "default_for_kind": False,
            "ack_text_kind": "canonical",
        }
        # At-quorum signatures relax the restriction (one key in
        # single-operator mode, two once the requirement is bumped).
        result = apply_overlay([relaxed_raw], valid_signature_count=DUAL_SIG_REQUIREMENT)
        assert baseline.id in result.applied_ids

    @pytest.mark.skipif(
        DUAL_SIG_REQUIREMENT < 2,
        reason="no 'valid but insufficient' band when the requirement is 1 "
        "(single-operator mode); re-engages automatically when bumped to 2",
    )
    def test_below_quorum_cannot_introduce_new_restricted_entry(
        self,
    ) -> None:
        new_restricted_raw = {
            "id": "byom_or_arcface_new",
            "display_name": "New Restricted",
            "kind": "face_embedder",
            "requires_explicit_ack": True,  # restricted
            "ack_text_version": "v1",
            "ack_text_sha256": "0" * 64,
            "upstream_claimed_license_class": "research_non_commercial",
            "commercial_use_restriction_known": True,  # restricted
            "bppicker_commercial_default_allowed": False,
            "commercial_unlock_requires_rights_assertion": True,
            "status": "available",
            "training_data": "",
            "weight_sha256": "0" * 64,
            "default_for_kind": False,
            "ack_text_kind": "canonical",
        }
        result = apply_overlay([new_restricted_raw], valid_signature_count=DUAL_SIG_REQUIREMENT - 1)
        assert "byom_or_arcface_new" in result.skipped_ids

    def test_single_sig_can_tighten_status(self) -> None:
        """Making models LESS available is always safe — even a
        single signature can flip a permissive entry to
        legally_blocked."""
        from bpp.registry import register_entry

        baseline = ModelEntry(
            id="will_be_blocked",
            display_name="Will Block",
            kind="face_embedder",
            source_url="",
            terms_url="",
            terms_permalink_url=None,
            terms_retrieved_at="",
            license_summary="",
            requires_explicit_ack=False,
            ack_text_version="",
            ack_text_sha256="",
            upstream_claimed_license_class=LicenseClass.APACHE_2_0,
            commercial_use_restriction_known=False,
            bppicker_commercial_default_allowed=True,
            commercial_unlock_requires_rights_assertion=False,
            status=ModelStatus.AVAILABLE,
            training_data="",
            weight_sha256="0" * 64,
            default_for_kind=False,
        )
        register_entry(baseline)
        blocked_raw = {
            "id": baseline.id,
            "display_name": baseline.display_name,
            "kind": baseline.kind,
            "requires_explicit_ack": False,
            "ack_text_version": "",
            "ack_text_sha256": "",
            "upstream_claimed_license_class": "apache_2_0",
            "commercial_use_restriction_known": False,
            "bppicker_commercial_default_allowed": True,
            "commercial_unlock_requires_rights_assertion": False,
            "status": "legally_blocked",
            "training_data": "",
            "weight_sha256": "0" * 64,
            "default_for_kind": False,
            "ack_text_kind": "canonical",
        }
        result = apply_overlay([blocked_raw], valid_signature_count=1)
        assert baseline.id in result.applied_ids
        from bpp.registry import get_entry

        assert get_entry(baseline.id).status is ModelStatus.LEGALLY_BLOCKED

    def test_single_sig_can_add_new_permissive_entry(self) -> None:
        permissive_raw = {
            "id": "new_permissive",
            "display_name": "Add Me",
            "kind": "face_embedder",
            "requires_explicit_ack": False,
            "ack_text_version": "",
            "ack_text_sha256": "",
            "upstream_claimed_license_class": "apache_2_0",
            "commercial_use_restriction_known": False,
            "bppicker_commercial_default_allowed": True,
            "commercial_unlock_requires_rights_assertion": False,
            "status": "available",
            "training_data": "",
            "weight_sha256": "0" * 64,
            "default_for_kind": False,
            "ack_text_kind": "canonical",
        }
        result = apply_overlay([permissive_raw], valid_signature_count=1)
        assert "new_permissive" in result.applied_ids


# ── Placeholder-key short-circuit ──


class TestPlaceholderKeyShortCircuit:
    """The short-circuit lets a checkout with only placeholder keys
    skip the inevitable overlay fetch failure. With real keys landed
    2026-06, the bundled set should NOT short-circuit — the fetch
    runs as designed."""

    def test_bundled_set_does_not_short_circuit_with_real_keys(
        self,
    ) -> None:
        """The 2026-06 release shipped real keys. The short-circuit
        must return False so the overlay fetch actually runs."""
        from bpp.registry import is_all_placeholder_keys, trusted_key_list

        assert is_all_placeholder_keys(trusted_key_list()) is False

    def test_real_key_does_not_short_circuit(self) -> None:
        from bpp.registry import TrustedKey, is_all_placeholder_keys

        real = TrustedKey(
            key_id="real-key-1",
            public_key_b64="A" * 44,
            maintainer_label="Arkalogy primary signing key",
        )
        assert is_all_placeholder_keys([real]) is False

    def test_explicit_placeholder_set_short_circuits(self) -> None:
        """A hypothetical fork that resets to placeholders (for an
        air-gapped offline build, for example) must short-circuit."""
        from bpp.registry import TrustedKey, is_all_placeholder_keys

        placeholder = TrustedKey(
            key_id="x-placeholder-key",
            public_key_b64="A" * 44,
            maintainer_label="placeholder (do not ship)",
        )
        assert is_all_placeholder_keys([placeholder]) is True

    def test_mixed_set_does_not_short_circuit(self) -> None:
        """Transitional release: one real key alongside a placeholder.
        The fetch should run because the real signature can verify."""
        from bpp.registry import TrustedKey, is_all_placeholder_keys

        real = TrustedKey(
            key_id="real-key-1",
            public_key_b64="A" * 44,
            maintainer_label="Arkalogy primary signing key",
        )
        placeholder = TrustedKey(
            key_id="x-placeholder-key",
            public_key_b64="B" * 44,
            maintainer_label="placeholder (do not ship)",
        )
        assert is_all_placeholder_keys([real, placeholder]) is False

    def test_empty_set_treated_as_placeholder(self) -> None:
        """A trust set with zero entries can never verify anything;
        treat as 'all placeholders' so the fetch is skipped."""
        from bpp.registry import is_all_placeholder_keys

        assert is_all_placeholder_keys([]) is True
