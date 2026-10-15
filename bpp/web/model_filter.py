"""Pure decision logic for the pre-analyze "Download ML models?" prompt.

Extracted from ``bp_core.api_models_pending`` so the eligibility rules
are unit-testable without an HTTP round-trip and the endpoint stays a
thin wrapper. No Flask imports here — this is plain data in, plain data
out.

The single entry point is :func:`compute_pending_and_blocked`, which
splits the manifest's not-yet-downloaded models into:

* ``items``  — downloadable now (permissive, or click-through +
  accepted), plus attribution models (Apache / BSD-style) not yet
  accepted, flagged ``needs_acceptance``: the pre-analysis dialog asks
  for all of those in one step and they download only once accepted.
* ``blocked`` — restricted models that can't be fetched yet (needs
  license, needs re-acceptance after the license text changed, or a
  status that forbids new downloads). The frontend turns these into the
  "available in Settings → Models" hint rather than a silent omission.

Redundant restricted alternates for a capability already covered by a
permissive model (a restricted alternate when a no-click-through
default of the same kind exists) are
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


def license_state(legal) -> str:
    """Classify a legal-registry entry's acceptance for the UI.

    Returns one of:

    * ``"ok"`` — no click-through needed, or a CURRENT acceptance is on
      file (its acknowledgment-text hash matches the entry's current
      text — the same check the load gate uses).
    * ``"needs_reaccept"`` — the latest row is an acceptance, but of an
      older acknowledgment text (e.g. the canonical disclaimer was
      bumped). The load gate refuses it, so the UI must not show it as
      accepted.
    * ``"needs_license"`` — never accepted, or the user withdrew it.
    """
    from bpp.registry.acceptance import is_acceptance_valid_for
    from bpp.registry.acceptance_log import has_accepted

    if not legal.requires_explicit_ack or is_acceptance_valid_for(legal):
        return "ok"
    if has_accepted(legal.id):
        return "needs_reaccept"
    return "needs_license"


def load_state(legal) -> str:
    """The load gate's decision for a legal-registry entry, in UI terms.

    Same decision as the gate that loads the model (license accepted with
    the current text, use context, entry status), so the UI can't show a
    model as fine while it's being skipped. One of:

    * ``"ok"`` — it loads.
    * ``"needs_license"`` — never accepted, or withdrawn.
    * ``"needs_reaccept"`` — accepted under an older license text.
    * ``"needs_commercial_rights"`` — accepted, but the use context is
      commercial and the separate-rights assertion wasn't made.
    * ``"blocked"`` — the registry withdrew or legally blocked the model;
      accepting can't help.
    """
    from bpp.registry.acceptance_log import has_accepted
    from bpp.registry.policy import ModelLoadDecision, check_model_load_allowed
    from bpp.registry.use_context_store import get_use_context

    decision = check_model_load_allowed(legal, use_context=get_use_context()).decision
    if decision is ModelLoadDecision.ALLOW:
        return "ok"
    if decision is ModelLoadDecision.BLOCKED_NEEDS_ACK:
        return "needs_reaccept" if has_accepted(legal.id) else "needs_license"
    if decision is ModelLoadDecision.BLOCKED_COMMERCIAL_NO_RIGHTS:
        return "needs_commercial_rights"
    return "blocked"


#: ``{"entry": (files signature, registry entries, {legal_id: state})}``.
_license_blocked_cache: dict = {}

#: Reasons reported by :func:`license_blocked_models`, in display order.
BLOCK_REASONS = ("needs_license", "needs_reaccept", "needs_commercial_rights", "blocked")


def model_load_states() -> dict[str, str]:
    """``{legal_id: reason}`` for every registered model the load gate
    currently refuses (models that load are left out). Cached until the
    acceptance / use-context files change or a registry entry is replaced
    (a remote-registry overlay can change terms or status)."""
    from bpp.registry.model_registry import iter_entries
    from bpp.scoring.model_load_gate import _inputs_signature

    entries = tuple(iter_entries())
    files_sig = _inputs_signature()
    # Entries are frozen: a different object means it was replaced (an
    # overlay can change any field the decision depends on). Compared by
    # identity, holding the objects so an id can't be reused.
    cached = _license_blocked_cache.get("entry")
    if (
        cached is not None
        and cached[0] == files_sig
        and len(cached[1]) == len(entries)
        and all(a is b for a, b in zip(cached[1], entries, strict=True))
    ):
        return cached[2]
    states: dict[str, str] = {}
    for legal in entries:
        state = load_state(legal)
        if state != "ok":
            states[legal.id] = state
    # One assignment: a reader never sees a new signature with old states.
    _license_blocked_cache["entry"] = (files_sig, entries, states)
    return states


def license_blocked_models() -> dict[str, list[str]]:
    """The registered models the load gate currently refuses, by reason."""
    states = model_load_states()
    return {reason: sorted(i for i, s in states.items() if s == reason) for reason in BLOCK_REASONS}


def _is_research_only(legal) -> bool:
    from bpp.registry import LicenseClass

    return legal.upstream_claimed_license_class is LicenseClass.RESEARCH_NON_COMMERCIAL


def _blocked_item(
    name: str, size_mb: float, host: str, legal, reason: str, *, from_plugin: bool = False
) -> dict:
    return {
        "from_plugin": from_plugin,
        "name": name,
        "size_mb": size_mb,
        "host": host,
        "legal_entry_id": legal.id,
        "display_name": legal.display_name,
        "kind": legal.kind,
        "reason": reason,
        # Lets the pre-flight dialog label opt-in extras (e.g. SCRFD)
        # as optional + research-only instead of implying the core
        # capability is missing.
        "default_for_kind": bool(legal.default_for_kind),
        "research_only": _is_research_only(legal),
    }


def compute_pending_and_blocked() -> tuple[list[dict], list[dict]]:
    """Return ``(items, blocked)`` for the pre-analyze consent prompt.

    ``items`` are downloadable now; ``blocked`` exist but are gated.
    See the module docstring for the full contract.
    """
    from bpp.registry import get_entry, iter_entries, status_behavior
    from bpp.registry.attribution_bundle import is_attribution_entry
    from bpp.scoring.model_manifest import pending_downloads

    covered_kinds = _covered_kinds()

    items: list[dict] = []
    blocked: list[dict] = []
    for e in pending_downloads():
        legal = get_entry(e.legal_entry_id)
        if legal is None:
            # Fail closed: a download whose registry entry is missing is
            # not offered (a structural test keeps the two in sync).
            log.warning(
                "Manifest entry %s legal_entry_id=%r not in registry",
                e.name,
                e.legal_entry_id,
            )
            continue
        # Two gates an entry must clear to be downloadable now:
        #   1. Status allows new downloads (status_behavior).
        #   2. Either not restricted, or restricted + accepted with the
        #      CURRENT acknowledgment text (version-aware, matching the
        #      load gate — a stale acceptance would download weights the
        #      gate then refuses to load).
        status_ok = status_behavior(legal.status).new_download_allowed
        lic = license_state(legal)
        license_ok = lic == "ok"
        if status_ok and (license_ok or is_attribution_entry(legal)):
            # An unaccepted attribution model downloads only if the user
            # accepts it in the pre-analysis dialog's combined step.
            items.append(
                {
                    "name": e.name,
                    "size_mb": e.size_mb,
                    "host": e.host,
                    "url": e.url,
                    "legal_entry_id": legal.id,
                    "needs_acceptance": not license_ok,
                }
            )
        elif lic == "needs_license" and legal.kind in covered_kinds and not e.from_plugin:
            # Restricted alternate for an already-covered capability —
            # drop entirely rather than offer it (free download) or nag
            # for its license. The permissive default serves this kind.
            # (A stale acceptance is still surfaced: the user opted in
            # once and the feature has now stopped.)
            continue
        else:
            reason = lic if not license_ok else "status_blocked"
            blocked.append(
                _blocked_item(e.name, e.size_mb, e.host, legal, reason, from_plugin=e.from_plugin)
            )

    # Second pass: legal-registry entries the manifest doesn't cover
    # (NudeNet weights are fetched by the catalog ensure-weights flow
    # after acceptance; LaMa is fetched on-demand by the inpaint flow;
    # neither is in pending_downloads). If restricted AND not accepted
    # (or accepted under an older text), the user still needs to know
    # they exist and where to enable them — otherwise the "Settings →
    # Models" hint is incomplete from the pre-flight dialog's point of
    # view.
    already_blocked_ids = {b["legal_entry_id"] for b in blocked}
    already_listed_ids = {
        item.get("legal_entry_id") for item in items if item.get("legal_entry_id")
    }
    for legal in iter_entries():
        if legal.id in already_blocked_ids or legal.id in already_listed_ids:
            continue
        # Attribution models are accepted in the dialog's combined step.
        if not legal.requires_explicit_ack or is_attribution_entry(legal):
            continue
        lic = license_state(legal)
        if lic == "ok":
            continue
        if lic == "needs_license" and legal.kind in covered_kinds:
            # Redundant alternate for an already-available capability —
            # not a missing feature, so don't advertise it as one.
            continue
        if not status_behavior(legal.status).new_download_allowed:
            continue
        # Size for these is unknown at pre-flight time — the manifest
        # only carries it for entries it knows about. Use the legal
        # registry's expected_download_size_bytes if set, else 0.
        size_bytes = getattr(legal, "expected_download_size_bytes", 0) or 0
        size_mb = round(size_bytes / (1024 * 1024), 1) if size_bytes else 0
        blocked.append(_blocked_item(legal.display_name, size_mb, "", legal, lic))

    return items, blocked
