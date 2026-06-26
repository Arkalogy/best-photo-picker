"""Bundled Ed25519 public keys that BPP trusts for remote-registry
manifest signatures.

Batch 8 / item 23 of the legal-posture rollout. The keys here are
what :func:`bpp.registry.signed_manifest.verify_manifest` checks
remote-manifest signatures against. Rotating a key means shipping
a BPP release with a new entry in this list (per Q9 — rotation
tooling is deferred to a follow-up minor; for v1 the trust set
is committed source).

Why two keys

The dual-signature requirement on restriction-class downgrades
(see :data:`bpp.registry.overlay.DUAL_SIG_REQUIREMENT`) presumes
at least two distinct keys are available for the maintainer to
sign with. Even on a solo-maintainer project, the second key
should be held separately (hardware key, sealed-envelope
airgap) per Q9's recommendation, so a single device compromise
cannot forge a downgrade-signed manifest.

Key custody

The matching private keys live OUTSIDE the repo, at
``~/.config/bpp/signing-keys/<slug>.private.key`` (mode 0600).
The maintainer is expected to move the secondary key to physically
separate storage (USB stick in a drawer, sealed envelope) and only
bring it out for restriction-class relaxations or new restricted-
entry additions. The primary key signs every other manifest
(status tightening, ack-text version bumps, new permissive entries)
on its own.

If you suspect a key compromise: generate a new keypair, replace
the entry below in a release, bump the BPP version, and publish a
takedown-status manifest signed by the unaffected key for every
entry signed under the suspect key. See ``docs/key-rotation.md``
for the long-form procedure.

Rotation cadence

Calendar-rotate the primary key every 12 months even without a
suspected compromise; the secondary key may rotate less often if
it has remained in cold storage. Each rotation appends a new
``TrustedKey`` to the tuple below and ships in a BPP release;
older entries stay in the tuple for one release after rotation so
manifests still signed under the old keys verify during the cut-
over window. After that window, the old entry is removed in the
following release.
"""

from __future__ import annotations

from bpp.registry.signed_manifest import TrustedKey

#: Trusted Ed25519 public keys. Each entry is base64-encoded raw
#: 32-byte Ed25519 public key + a short slug to identify it in a
#: manifest signature block + a maintainer label that shows up in
#: the audit logs when the overlay applies.
#:
#: ``key_id`` slugs encode the role and rotation epoch
#: (``arkalogy-primary-2026-06`` = primary key, generated 2026-06).
#: Slugs are stable: a signature block carries the slug to point
#: at which key signed it, so changing a slug invalidates every
#: existing signature.
#:
#: First real keypair shipped 2026-06-03. The placeholder keys that
#: shipped earlier in Batch 8 are GONE — any manifest still signed
#: under the placeholder key_ids ("dev-placeholder-key-1" / -2)
#: will fail verification, which is the desired behaviour (no
#: published manifests existed under the placeholder keys).
TRUSTED_KEYS: tuple[TrustedKey, ...] = (
    TrustedKey(
        key_id="arkalogy-primary-2026-06",
        public_key_b64="Z/rvh66dX1lz1PR3HrsbL+/w6wnOTYBsu1GZytuMW6g=",
        maintainer_label="Arkalogy primary signing key (single-sig overlay)",
    ),
    # Secondary slot reserved for the dual-sig downgrade key. The
    # previous placeholder was orphaned (no matching private key) and
    # was removed 2026-06-05. Until a real secondary keypair is
    # generated and the private key escrowed (cold storage / co-signer
    # device / hardware token), the overlay quorum
    # (overlay.DUAL_SIG_REQUIREMENT) is 1, so the primary key alone can
    # authorize restriction relaxations / new restricted entries. Raise
    # the quorum back to 2 the moment a real secondary key lands here —
    # the verifier counts DISTINCT keys, so the primary signing twice
    # can never satisfy a quorum above 1 on its own.
)


def trusted_key_list() -> list[TrustedKey]:
    """Return the bundled trusted-key set as a list.

    Wrapper so :mod:`bpp.registry.signed_manifest` callers do not
    need to know whether the underlying constant is a tuple, list,
    or function-returning-list. Tests that need a different trust
    set override this via patching.
    """
    return list(TRUSTED_KEYS)


#: Marker substring used in placeholder ``maintainer_label`` values.
#: A trust set in which every key carries this marker is considered
#: "all placeholders" — :func:`is_all_placeholder_keys` returns
#: ``True`` and the registry-overlay fetch is skipped. With real
#: keys in production, this returns ``False`` and the overlay fetch
#: runs as designed.
_PLACEHOLDER_LABEL_MARKER = "placeholder"


def is_all_placeholder_keys(keys: list[TrustedKey] | None = None) -> bool:
    """Return ``True`` when every trusted key in ``keys`` is a
    placeholder — that is, every ``maintainer_label`` contains
    :data:`_PLACEHOLDER_LABEL_MARKER`.

    The registry-overlay startup hook calls this to decide whether
    to attempt a remote-manifest fetch at all. With the real keys
    landed in 2026-06, this returns ``False`` and the overlay fetch
    runs as designed.
    """
    effective = keys if keys is not None else trusted_key_list()
    if not effective:
        return True
    return all(_PLACEHOLDER_LABEL_MARKER in k.maintainer_label.lower() for k in effective)
