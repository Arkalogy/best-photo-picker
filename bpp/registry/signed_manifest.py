"""Sign + verify the remote model-registry manifest.

Batch 8 / item 23 of the legal-posture rollout. The remote
registry overlay (item 12) lets BPP fetch updated model metadata
without shipping a new release — new entries, status changes
(``available`` → ``withdrawn_no_new_downloads`` → ``legally_blocked``),
ack-text version bumps. That capability is dangerous unsigned: a
compromised registry could silently introduce a restricted model
or relax a restriction class. The signing primitives here close
that risk with three layered checks.

What the verifier enforces

1. **Cryptographic authenticity** — the manifest carries one or
   more Ed25519 signatures over its canonical bytes. The bundled
   public-key set lists every key allowed to sign. At least one
   signature must verify against a known key.
2. **Dual-signature for restriction-class downgrades** (Q9) — an
   entry whose ``commercial_use_restriction_known`` was ``True``
   in the bundled baseline cannot be relaxed to ``False`` without
   TWO valid signatures from distinct keys on the same manifest.
   Same rule for changes that flip ``requires_explicit_ack``
   from True to False. A single signature can only tighten, never
   relax. This is the trap-T7 guard at the manifest layer; the
   runtime guard in :func:`bpp.registry.policy.assert_no_silent_reclassification`
   is the second line of defence.
3. **Source-domain allowlist** — see :mod:`bpp.registry.remote_registry`
   for the fetch-side check that the manifest URL itself comes
   from an allowed host.

Why Ed25519 rather than HMAC or RSA

Ed25519 keys are tiny (32 bytes public, 32 bytes private),
signatures are fixed-length, signing/verifying are deterministic
and constant-time, and the algorithm has no known practical
weaknesses. The standard PyCA ``cryptography`` library provides
the verifier. HMAC was considered and rejected — its symmetry
means anyone who can verify can also forge, which defeats the
point of distributing a verification key in the app.

Canonical serialization

Signatures cover the JSON-canonical encoding of the manifest with
the ``signatures`` field temporarily replaced by ``None``. This
lets the verifier reconstruct exactly what the signer signed
regardless of dict key order or whitespace choices in the
distributed file. The canonical form uses :func:`json.dumps` with
``sort_keys=True`` and ``separators=(",", ":")`` — same shape the
rest of BPP uses for hash-stable JSON.

Out of scope for Batch 8

* Key rotation tooling. Per Q9, rotation is a follow-up post-
  release minor. The bundled key set is committed; rotating it
  ships in a BPP release.
* Anti-rollback nonces / monotonic timestamps. Today the
  verifier accepts any signed manifest the bundled keys signed.
  A future hardening pass can add timestamp-monotonic checks if
  rollback becomes a realistic concern.
* Network fetch. See :mod:`bpp.registry.remote_registry` for the
  fetch-side allowlist + TLS-only check.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from bpp.utils.logging import get_logger

_log = get_logger(__name__)


MANIFEST_SCHEMA_VERSION = 1


class ManifestVerificationError(RuntimeError):
    """Raised by :func:`verify_manifest` when the manifest does not
    satisfy every authenticity rule. Carries a list of human-
    readable errors so the caller can show them all at once
    rather than one-failure-per-retry."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Manifest verification failed:\n  - " + "\n  - ".join(errors))


@dataclass(frozen=True)
class TrustedKey:
    """One public key the bundled key set trusts.

    ``key_id`` is the short slug a signature carries to point at
    which key signed it (so the verifier doesn't have to try every
    bundled key per signature). ``public_key_b64`` is the
    base64-encoded raw 32-byte Ed25519 public key.
    ``maintainer_label`` is a short name shown in log lines so
    audit reads stay legible.
    """

    key_id: str
    public_key_b64: str
    maintainer_label: str

    def public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(self.public_key_b64))


@dataclass(frozen=True)
class Signature:
    """One signature block on a manifest.

    ``signature_b64`` is the base64-encoded Ed25519 signature over
    the canonical manifest bytes.
    """

    key_id: str
    signature_b64: str


@dataclass(frozen=True)
class ManifestVerificationResult:
    """Structured outcome of :func:`verify_manifest`.

    ``is_valid`` — every authenticity rule passed. The caller may
    apply the manifest's entries.
    ``valid_signatures`` — list of key_ids whose signatures
    verified. Used by the overlay merger to check whether a
    restriction downgrade has the dual-signature it needs.
    ``entries`` — the entries as raw dicts. The overlay merger
    decodes them into :class:`ModelEntry` objects.
    ``errors`` — collected error strings when ``is_valid`` is
    ``False``.
    """

    is_valid: bool
    valid_signatures: tuple[str, ...] = field(default_factory=tuple)
    entries: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Return the canonical bytes that signatures cover.

    The ``signatures`` field is temporarily replaced by ``None`` so
    the signer + verifier hash exactly the same bytes regardless
    of which signatures are present in the eventual distributed
    file. JSON encoding uses ``sort_keys=True`` and tight
    separators so dict key ordering and whitespace do not affect
    the hash.
    """
    stripped = dict(manifest)
    stripped["signatures"] = None
    return json.dumps(
        stripped,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sign_manifest(
    manifest: dict[str, Any],
    private_keys: list[tuple[str, Ed25519PrivateKey]],
) -> dict[str, Any]:
    """Add signatures to ``manifest`` and return the result.

    Used by the maintainer's signing tool (not by BPP at
    runtime). The function takes a list of ``(key_id,
    private_key)`` pairs so the maintainer can stamp multiple
    signatures in one pass — necessary for restriction-class
    downgrades, which require two keys.
    """
    payload_bytes = canonical_manifest_bytes(manifest)
    sigs: list[dict[str, str]] = []
    for key_id, priv in private_keys:
        sig_bytes = priv.sign(payload_bytes)
        sigs.append(
            {
                "key_id": key_id,
                "signature_b64": base64.b64encode(sig_bytes).decode("ascii"),
            }
        )
    out = dict(manifest)
    out["signatures"] = sigs
    return out


def _parse_signatures(raw: Any) -> tuple[list[Signature], list[str]]:
    """Parse the ``signatures`` field. Returns ``(sigs, errors)``."""
    if not isinstance(raw, list) or not raw:
        return [], [
            "manifest has no signatures field or it is empty; at least "
            "one Ed25519 signature is required"
        ]
    sigs: list[Signature] = []
    errors: list[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"signature #{i} is not an object")
            continue
        key_id = item.get("key_id")
        sig_b64 = item.get("signature_b64")
        if not isinstance(key_id, str) or not key_id:
            errors.append(f"signature #{i} missing key_id")
            continue
        if not isinstance(sig_b64, str) or not sig_b64:
            errors.append(f"signature #{i} ({key_id}) missing signature_b64")
            continue
        sigs.append(Signature(key_id=key_id, signature_b64=sig_b64))
    return sigs, errors


def _verify_one_signature(
    payload: bytes,
    signature: Signature,
    trusted_keys: dict[str, TrustedKey],
) -> tuple[bool, str]:
    """Verify ``signature`` against ``payload`` using
    ``trusted_keys``. Returns ``(verified, error_or_empty)``."""
    trusted = trusted_keys.get(signature.key_id)
    if trusted is None:
        return False, (
            f"signature for key_id={signature.key_id!r} is not in the "
            "bundled trusted-key set; possible compromise or stale BPP "
            "version"
        )
    try:
        sig_bytes = base64.b64decode(signature.signature_b64)
    except (ValueError, TypeError) as exc:
        return False, f"signature for {signature.key_id!r} is not valid base64: {exc}"
    try:
        trusted.public_key().verify(sig_bytes, payload)
    except InvalidSignature:
        return False, (
            f"signature for {signature.key_id!r} does not verify against "
            f"the manifest payload; tampered manifest or key mismatch"
        )
    return True, ""


def verify_manifest(
    manifest: dict[str, Any],
    *,
    trusted_keys: list[TrustedKey],
) -> ManifestVerificationResult:
    """Verify ``manifest`` against the bundled ``trusted_keys``.

    Pure: reads only the inputs, never raises on a bad manifest.
    Returns a :class:`ManifestVerificationResult` whose
    ``is_valid`` field tells the caller whether to apply the
    overlay. ``valid_signatures`` is the list of key_ids whose
    signatures verified — the overlay merger uses this to check
    whether a restriction downgrade has the dual signature it
    needs.

    The function does NOT enforce dual-sig here. That check
    belongs to the merger, which knows the bundled baseline and
    can tell which entries are restriction downgrades. The
    verifier only confirms the manifest's signatures are
    cryptographically valid.
    """
    errors: list[str] = []

    if not isinstance(manifest, dict):
        return ManifestVerificationResult(
            is_valid=False,
            errors=("manifest is not a JSON object",),
        )

    schema = manifest.get("schema_version")
    if schema != MANIFEST_SCHEMA_VERSION:
        errors.append(f"manifest schema_version is {schema!r}; expected {MANIFEST_SCHEMA_VERSION}")

    entries_raw = manifest.get("entries")
    if not isinstance(entries_raw, list):
        errors.append("manifest entries field missing or not a list")
        entries_raw = []
    else:
        for i, entry in enumerate(entries_raw):
            if not isinstance(entry, dict):
                errors.append(f"entry #{i} is not an object")
            elif not entry.get("id"):
                errors.append(f"entry #{i} missing id")

    signatures, sig_errors = _parse_signatures(manifest.get("signatures"))
    errors.extend(sig_errors)

    trusted_by_id = {tk.key_id: tk for tk in trusted_keys}
    payload = canonical_manifest_bytes(manifest)
    # Deduplicate by key_id: the overlay merger gates restriction downgrades
    # on the COUNT of valid signatures as a proxy for "N distinct trusted
    # keys signed this". Counting raw signatures would let one key signing
    # twice fake a higher count and clear a multi-key requirement on its own
    # — defeating exactly the single-key-compromise containment the gate
    # exists for. dict.fromkeys preserves first-seen order for stable output.
    seen_keys: set[str] = set()
    valid_sig_ids: list[str] = []
    for sig in signatures:
        ok, err = _verify_one_signature(payload, sig, trusted_by_id)
        if not ok:
            errors.append(err)
            continue
        if sig.key_id in seen_keys:
            continue
        seen_keys.add(sig.key_id)
        valid_sig_ids.append(sig.key_id)

    if not valid_sig_ids:
        errors.append(
            "no signature on the manifest verified against the bundled "
            "trusted-key set; refusing to apply the overlay"
        )

    if errors:
        return ManifestVerificationResult(
            is_valid=False,
            valid_signatures=tuple(valid_sig_ids),
            entries=tuple(e for e in entries_raw if isinstance(e, dict)),
            errors=tuple(errors),
        )

    return ManifestVerificationResult(
        is_valid=True,
        valid_signatures=tuple(valid_sig_ids),
        entries=tuple(entries_raw),
    )
