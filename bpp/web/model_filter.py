"""Pure decision logic for the pre-analyze "Download ML models?" prompt.

Extracted from ``bp_core.api_models_pending`` so the eligibility rules
are unit-testable without an HTTP round-trip and the endpoint stays a
thin wrapper. No Flask imports here — this is plain data in, plain data
out.

The single entry point is :func:`compute_pending_and_blocked`, which
splits the manifest's not-yet-downloaded models into:

* ``items``  — downloadable now (permissive, or restricted + accepted).
* ``blocked`` — exists but can't be fetched yet (needs license, or a
  status that forbids new downloads). The frontend turns these into the
  "available in Settings → Models" hint rather than a silent omission.

Redundant restricted alternates for a capability already covered by a
permissive model (e.g. YuNet vs the permissive SCRFD default) are
dropped from BOTH lists — listing them would contradict Settings →
Models, which shows the permissive default already running.
"""

from __future__ import annotations

from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _covered_kinds() -> set[str]:
    """Kinds satisfied by a permissive, downloadable registry entry.

    Such a kind is never a "missing feature" — its permissive entry
    downloads silently or is already on disk — so any restricted
    sibling of the same kind is redundant.
    """
    from bpp.registry import iter_entries, status_behavior

    return {
        legal.kind
        for legal in iter_entries()
        if not legal.requires_explicit_ack and status_behavior(legal.status).new_download_allowed
    }


def compute_pending_and_blocked() -> tuple[list[dict], list[dict]]:
    """Return ``(items, blocked)`` for the pre-analyze consent prompt.

    ``items`` are downloadable now; ``blocked`` exist but are gated.
    See the module docstring for the full contract.
    """
    from bpp.registry import get_entry, iter_entries, status_behavior
    from bpp.registry.acceptance_log import has_accepted
    from bpp.scoring.model_manifest import pending_downloads

    covered_kinds = _covered_kinds()

    items: list[dict] = []
    blocked: list[dict] = []
    for e in pending_downloads():
        # Permissive / ancillary entries — no legal counterpart, pass
        # through to the consent prompt unchanged.
        if e.legal_entry_id is None:
            items.append({"name": e.name, "size_mb": e.size_mb, "host": e.host, "url": e.url})
            continue
        legal = get_entry(e.legal_entry_id)
        if legal is None:
            # Manifest references a legal id the registry doesn't know —
            # treat as ancillary so the user can still proceed; log so a
            # maintainer can catch the drift.
            log.warning(
                "Manifest entry %s legal_entry_id=%r not in registry",
                e.name,
                e.legal_entry_id,
            )
            items.append({"name": e.name, "size_mb": e.size_mb, "host": e.host, "url": e.url})
            continue
        # Two gates an entry must clear to be downloadable now:
        #   1. Status allows new downloads (status_behavior).
        #   2. Either not restricted, or restricted + already accepted.
        status_ok = status_behavior(legal.status).new_download_allowed
        license_ok = (not legal.requires_explicit_ack) or has_accepted(legal.id)
        if status_ok and license_ok:
            items.append(
                {
                    "name": e.name,
                    "size_mb": e.size_mb,
                    "host": e.host,
                    "url": e.url,
                    "legal_entry_id": legal.id,
                }
            )
        elif not license_ok and legal.kind in covered_kinds:
            # Restricted alternate for an already-covered capability —
            # drop entirely rather than offer it (free download) or nag
            # for its license. The permissive default serves this kind.
            continue
        else:
            reason = "needs_license" if not license_ok else "status_blocked"
            blocked.append(
                {
                    "name": e.name,
                    "size_mb": e.size_mb,
                    "host": e.host,
                    "legal_entry_id": legal.id,
                    "display_name": legal.display_name,
                    "kind": legal.kind,
                    "reason": reason,
                }
            )

    # Second pass: legal-registry entries the manifest doesn't cover
    # (NudeNet ships inside its pip wheel; LaMa is fetched on-demand by
    # the inpaint flow; neither is in pending_downloads). If restricted
    # AND not accepted, the user still needs to know they exist and where
    # to enable them — otherwise the "Settings → Models" hint is
    # incomplete from the pre-flight dialog's point of view.
    already_blocked_ids = {b["legal_entry_id"] for b in blocked}
    already_listed_ids = {
        item.get("legal_entry_id") for item in items if item.get("legal_entry_id")
    }
    for legal in iter_entries():
        if legal.id in already_blocked_ids or legal.id in already_listed_ids:
            continue
        if not legal.requires_explicit_ack:
            continue
        if has_accepted(legal.id):
            continue
        if legal.kind in covered_kinds:
            # Redundant alternate for an already-available capability —
            # not a missing feature, so don't advertise it as one.
            continue
        if not status_behavior(legal.status).new_download_allowed:
            continue
        # Size for these is unknown at pre-flight time — the manifest
        # only carries it for entries it knows about. Use the legal
        # registry's expected_download_size_bytes if set, else 0.
        size_bytes = getattr(legal, "expected_download_size_bytes", 0) or 0
        blocked.append(
            {
                "name": legal.display_name,
                "size_mb": round(size_bytes / (1024 * 1024), 1) if size_bytes else 0,
                "host": "",
                "legal_entry_id": legal.id,
                "display_name": legal.display_name,
                "kind": legal.kind,
                "reason": "needs_license",
            }
        )

    return items, blocked
