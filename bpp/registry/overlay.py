"""Merge a verified remote manifest into the bundled baseline.

The fetcher
(:mod:`bpp.registry.remote_registry`) brings the bytes, the
verifier (:mod:`bpp.registry.signed_manifest`) confirms a known
key signed them, and this module decides what changes from the
remote manifest are safe to apply on top of the in-process
built-in registry.

What the merger enforces

1. **Signature quorum for restriction-class relaxations.** An
   update that relaxes an existing entry's
   ``commercial_use_restriction_known`` (True → False),
   ``requires_explicit_ack`` (True → False), or
   ``bppicker_commercial_default_allowed`` (False → True) is applied
   only when the manifest carries valid signatures from at least
   :data:`DUAL_SIG_REQUIREMENT` distinct trusted keys.
2. **Same quorum for new restricted entries.** A manifest may add
   a new permissive entry with any valid signature; a new entry
   with either restriction flag set needs the quorum.
3. **Status changes are not quorum-gated.** ``status`` is applied
   from any manifest that verifies. Tightening (``deprecated``,
   ``withdrawn_no_new_downloads``, ``legally_blocked``) is the
   intended use; note that the merger does not compare status
   against the baseline, so a verified manifest can also move an
   entry back to ``available`` — a newer one, that is: the startup
   path refuses a manifest older than the last one applied
   (:mod:`bpp.registry.manifest_rollback`), so a replay cannot.
4. **Failing entries are skipped, not fatal.** An entry that
   misses the quorum (or trips the runtime reclassification lock)
   is logged and skipped; the rest of the overlay still applies.

Current quorum

:data:`DUAL_SIG_REQUIREMENT` is ``1`` because one signing key is
trusted today (see :mod:`bpp.registry.trusted_keys`). Every
manifest that passes :func:`bpp.registry.signed_manifest.verify_manifest`
already carries one valid signature, so in practice the single
trusted key can authorize every change listed above, including
relaxations and new restricted entries. The mechanism counts
distinct keys, so the quorum can be raised to 2 once a second key
is added; raising it is a manual code change in a release.
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
#: restriction-class relaxation or to introduce a new restricted entry.
#: It is 1 today because exactly one key is trusted, so that key alone
#: can authorize those changes. Raise it to 2 (by editing this constant
#: in a release) once a second trusted key is added. The count is
#: deduplicated by key in verify_manifest, so one key signing twice
#: can never satisfy a requirement above 1.
DUAL_SIG_REQUIREMENT = 1


@dataclass(frozen=True)
class OverlayApplicationResult:
    """Structured outcome of :func:`apply_overlay`.

    ``applied_ids`` — entries from the remote manifest that landed
        in the in-process registry.
    ``skipped_ids`` — entries the merger refused to apply (because
        they would relax restriction class or introduce a new
        restricted entry with fewer than
        :data:`DUAL_SIG_REQUIREMENT` distinct valid signatures, or
        because the runtime reclassification lock fired). Each id
        appears alongside a short reason in ``warnings``.
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
    flag makes introducing or relaxing the entry subject to the
    :data:`DUAL_SIG_REQUIREMENT` quorum."""
    return entry.commercial_use_restriction_known or entry.requires_explicit_ack


def _needs_dual_sig(remote_entry: ModelEntry, baseline: ModelEntry | None) -> tuple[bool, str]:
    """Decide whether ``remote_entry`` is subject to the signature
    quorum (:data:`DUAL_SIG_REQUIREMENT`) against ``baseline`` (the existing in-process entry, or
    ``None`` if the id is new).

    Returns ``(needed, reason)``.
    """
    if baseline is None:
        # New entry. Any valid signature may introduce a permissive
        # entry; a restricted entry needs the quorum.
        if _entry_is_restricted(remote_entry):
            return True, (
                "new restricted entry (commercial restriction known "
                "or explicit-ack required) needs the signature quorum"
            )
        return False, ""
    # Existing entry — only relaxation needs the quorum.
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
    merger compares it with :data:`DUAL_SIG_REQUIREMENT` for
    restriction relaxations and new restricted entries.

    The function calls :func:`register_entry` for every applied
    entry, which replaces an existing entry with the same id (the
    bundled baseline's registration intentionally happens first
    so the overlay can supersede). Entries that miss the quorum
    are skipped with a warning; the rest of the overlay
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

        # Runtime reclassification lock (policy.py). It remembers ids
        # that were applied as restricted earlier in this process and
        # refuses a later permissive version of the same id. Built-in
        # entries are not seeded into that set, so on its own it does
        # not stop a quorum-meeting manifest from relaxing a built-in
        # entry; the quorum check above is the gate for that.
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
