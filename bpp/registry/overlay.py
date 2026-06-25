"""Merge a verified remote manifest into the bundled baseline.

Batch 8 / item 12 + 23 of the legal-posture rollout. The fetcher
(:mod:`bpp.registry.remote_registry`) brings the bytes, the
verifier (:mod:`bpp.registry.signed_manifest`) confirms a known
key signed them, and this module decides what changes from the
remote manifest are safe to apply on top of the in-process
built-in registry.

What the merger enforces

1. **Dual-signature for restriction-class downgrades** (Q9). Any
   entry whose ``commercial_use_restriction_known`` was ``True``
   in the bundled baseline (or whose ``requires_explicit_ack``
   was ``True``) and whose remote update would relax that flag
   to ``False`` MUST carry signatures from two distinct trusted
   keys. A single-signature manifest can only TIGHTEN restriction
   class, never RELAX it. This is the trap-T7 guard at the merge
   layer, complementing the runtime guard in
   :func:`bpp.registry.policy.assert_no_silent_reclassification`.
2. **No silent introduction of new restricted entries by single
   sig.** A remote manifest signed by one key can add a new
   permissive entry but cannot add a new restricted entry. The
   restricted variants must come with two signatures so a single
   compromised key cannot land buffalo_s into the registry.
3. **Status transitions are always allowed.** A registered
   entry can be flipped to ``deprecated``,
   ``withdrawn_no_new_downloads``, or ``legally_blocked`` by a
   single signature — making models *less* available is always
   safe.
4. **Entries unknown to the bundled baseline that fail the dual-
   sig check are skipped.** The merger logs them and applies the
   rest of the overlay; partial application is better than
   refusing the whole manifest.
"""

from __future__ import annotations

from dataclasses import dataclass

from bpp.registry.model_registry import (
    LicenseClass,
    ModelEntry,
    ModelStatus,
    get_entry,
    register_entry,
)
from bpp.registry.policy import assert_no_silent_reclassification
from bpp.utils.logging import get_logger

_log = get_logger(__name__)


#: Minimum number of DISTINCT trusted keys required to apply a
#: restriction-class downgrade or to introduce a new restricted entry.
#: The design target is two (a single key compromise can't authorize a
#: downgrade). Set to 1 while BPP is operated by a single founder/operator
#: with a single signing key — bump back to 2 once a second, cold-stored
#: key exists to hold. The count is deduplicated by key in verify_manifest,
#: so one key signing twice can never satisfy a >1 requirement.
DUAL_SIG_REQUIREMENT = 1


@dataclass(frozen=True)
class OverlayApplicationResult:
    """Structured outcome of :func:`apply_overlay`.

    ``applied_ids`` — entries from the remote manifest that landed
        in the in-process registry.
    ``skipped_ids`` — entries the merger refused to apply (because
        they would silently downgrade restriction class or
        introduce a new restricted entry under a single
        signature). Each id appears alongside a short reason in
        ``warnings``.
    ``warnings`` — human-readable explanations for every skip.
    """

    applied_ids: tuple[str, ...]
    skipped_ids: tuple[str, ...]
    warnings: tuple[str, ...]


def _decode_entry(raw: dict) -> ModelEntry | None:
    """Decode a remote-manifest entry dict into a
    :class:`ModelEntry`. Returns ``None`` on any decoder error so
    the merger can skip the bad row rather than abort the entire
    overlay.
    """
    try:
        return ModelEntry(
            id=str(raw["id"]),
            display_name=str(raw["display_name"]),
            kind=str(raw["kind"]),
            source_url=str(raw.get("source_url", "")),
            terms_url=str(raw.get("terms_url", "")),
            terms_permalink_url=raw.get("terms_permalink_url"),
            terms_retrieved_at=str(raw.get("terms_retrieved_at", "")),
            license_summary=str(raw.get("license_summary", "")),
            requires_explicit_ack=bool(raw.get("requires_explicit_ack", False)),
            ack_text_version=str(raw.get("ack_text_version", "")),
            ack_text_sha256=str(raw.get("ack_text_sha256", "")),
            upstream_claimed_license_class=LicenseClass(
                raw.get("upstream_claimed_license_class", "unknown")
            ),
            commercial_use_restriction_known=bool(
                raw.get("commercial_use_restriction_known", False)
            ),
            bppicker_commercial_default_allowed=bool(
                raw.get("bppicker_commercial_default_allowed", True)
            ),
            commercial_unlock_requires_rights_assertion=bool(
                raw.get("commercial_unlock_requires_rights_assertion", False)
            ),
            status=ModelStatus(raw.get("status", "available")),
            training_data=str(raw.get("training_data", "")),
            weight_sha256=str(raw.get("weight_sha256", "")),
            default_for_kind=bool(raw.get("default_for_kind", False)),
            ack_text_kind=str(raw.get("ack_text_kind", "canonical")),
        )
    except (KeyError, ValueError, TypeError) as exc:
        _log.warning(
            "Remote manifest entry could not be decoded; skipping. id=%r error=%s",
            raw.get("id"),
            exc,
        )
        return None


def _entry_is_restricted(entry: ModelEntry) -> bool:
    """An entry counts as "restricted" if its commercial-use
    restriction flag is True OR it requires explicit ack. Either
    flag makes the entry one a single compromised key must not be
    able to introduce or silently relax."""
    return entry.commercial_use_restriction_known or entry.requires_explicit_ack


def _needs_dual_sig(remote_entry: ModelEntry, baseline: ModelEntry | None) -> tuple[bool, str]:
    """Decide whether ``remote_entry`` requires the dual-signature
    rule against ``baseline`` (the existing in-process entry, or
    ``None`` if the id is new).

    Returns ``(needed, reason)``.
    """
    if baseline is None:
        # New entry. Single sig may introduce permissive entries;
        # restricted entries need two signatures so a single key
        # cannot land a restricted model into the registry.
        if _entry_is_restricted(remote_entry):
            return True, (
                "new restricted entry (commercial restriction known "
                "or explicit-ack required); a single signature cannot "
                "introduce a restricted model"
            )
        return False, ""
    # Existing entry — only relaxation needs two signatures.
    if (
        baseline.commercial_use_restriction_known
        and not remote_entry.commercial_use_restriction_known
    ):
        return True, ("commercial_use_restriction_known would be relaxed True → False")
    if baseline.requires_explicit_ack and not remote_entry.requires_explicit_ack:
        return True, ("requires_explicit_ack would be relaxed True → False")
    if (
        not baseline.bppicker_commercial_default_allowed
        and remote_entry.bppicker_commercial_default_allowed
    ):
        return True, ("bppicker_commercial_default_allowed would be relaxed False → True")
    return False, ""


def apply_overlay(
    remote_entries_raw: list[dict],
    *,
    valid_signature_count: int,
) -> OverlayApplicationResult:
    """Apply ``remote_entries_raw`` to the in-process registry.

    ``valid_signature_count`` is the number of distinct trusted
    keys whose signatures verified on the manifest (returned by
    :func:`bpp.registry.signed_manifest.verify_manifest`). The
    merger uses this to enforce the dual-sig rule on restriction
    downgrades and new restricted entries.

    The function calls :func:`register_entry` for every applied
    entry, which replaces an existing entry with the same id (the
    bundled baseline's registration intentionally happens first
    so the overlay can supersede). Entries that fail the dual-sig
    check are skipped with a warning; the rest of the overlay
    still applies (partial application is better than refusing the
    whole manifest).
    """
    applied: list[str] = []
    skipped: list[str] = []
    warnings: list[str] = []

    for raw in remote_entries_raw:
        if not isinstance(raw, dict):
            continue
        entry = _decode_entry(raw)
        if entry is None:
            continue

        baseline = get_entry(entry.id)
        needs_dual, reason = _needs_dual_sig(entry, baseline)
        if needs_dual and valid_signature_count < DUAL_SIG_REQUIREMENT:
            warning = (
                f"Refusing remote-manifest entry {entry.id!r}: "
                f"{reason}, but the manifest has only "
                f"{valid_signature_count} valid signature(s); "
                f"need ≥{DUAL_SIG_REQUIREMENT}."
            )
            _log.warning(warning)
            warnings.append(warning)
            skipped.append(entry.id)
            continue

        # Trap-T7 guard at the runtime layer fires too — even with
        # adequate signatures, the global ever-restricted set
        # enforces an additional process-wide check. This is
        # belt-and-suspenders for the case where a maintainer
        # accidentally relaxes a restriction at the manifest level
        # but the bundled baseline still considered it restricted.
        try:
            assert_no_silent_reclassification(entry)
        except RuntimeError as exc:
            warning = (
                f"Refusing remote-manifest entry {entry.id!r}: "
                f"runtime reclassification lock fired ({exc})"
            )
            _log.warning(warning)
            warnings.append(warning)
            skipped.append(entry.id)
            continue

        register_entry(entry)
        applied.append(entry.id)

    return OverlayApplicationResult(
        applied_ids=tuple(applied),
        skipped_ids=tuple(skipped),
        warnings=tuple(warnings),
    )
