"""Bundled Ed25519 public keys that BPP trusts for remote-registry
manifest signatures.

The keys here are
what :func:`bpp.registry.signed_manifest.verify_manifest` checks
remote-manifest signatures against. Rotating a key means shipping
a BPP release with a new entry in this list (rotation tooling is
deferred to a later minor release; for now the trust set is
committed source).

How many keys

One key is trusted today (``arkalogy-primary-2026-06``), and the
overlay quorum (:data:`bpp.registry.overlay.DUAL_SIG_REQUIREMENT`)
is 1 to match. That key alone can therefore sign every kind of
manifest change, including restriction-class relaxations and new
restricted entries. The verifier counts distinct keys, so adding a
second key here and raising the quorum to 2 in the same release is
what would make a single-key compromise insufficient for those
changes.

Key custody

The matching private key is kept outside the repository and is
never shipped in a release.

If you suspect a key compromise: generate a new keypair, replace
the entry below, and ship a BPP release. With a single trusted
key there is no second key to sign an interim takedown manifest,
so until the release ships the remote overlay can be switched off
per install with ``BPP_DISABLE_REMOTE_REGISTRY=1``.

Rotation

Each rotation appends a new ``TrustedKey`` to the tuple below and
ships in a BPP release. Older entries can stay in the tuple for
one release so manifests signed under the old key still verify
during the cut-over, then are removed in the following release.
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
#: shipped before it are GONE — any manifest still signed
#: under the placeholder key_ids ("dev-placeholder-key-1" / -2)
#: will fail verification, which is the desired behaviour (no
#: published manifests existed under the placeholder keys).
TRUSTED_KEYS: tuple[TrustedKey, ...] = (
    TrustedKey(
        key_id="arkalogy-primary-2026-06",
        public_key_b64="Z/rvh66dX1lz1PR3HrsbL+/w6wnOTYBsu1GZytuMW6g=",
        maintainer_label="Arkalogy primary signing key (single-sig overlay)",
    ),
    # Only one key is trusted. While that is the case the overlay
    # quorum (overlay.DUAL_SIG_REQUIREMENT) is 1, so this key alone can
    # authorize restriction relaxations and new restricted entries.
    # To require two keys: add a second TrustedKey here and raise the
    # quorum to 2 in the same release. The verifier counts DISTINCT
    # keys, so this key signing twice can never satisfy a quorum
    # above 1 on its own.
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
