"""Policy layer that decides whether a model may be loaded.

Batch 5 / item 16 of the legal-posture rollout. Every model-load
chokepoint (face_embed, face_orchestrator, any future model-loading
code path including the CLI and headless invocations) calls
:func:`check_model_load_allowed` and either proceeds or raises
:class:`ModelLoadBlockedError`. There is no warning-only branch —
the policy is the chokepoint, not a hint.

Decision rules (in order)

1. Status :data:`ModelStatus.LEGALLY_BLOCKED` — block under any
   condition. The status was set because upstream sent a takedown
   or the maintainer learned the model can no longer be lawfully
   used. Even an existing acceptance does not override this.
2. Permissive entries (``commercial_use_restriction_known=False``)
   — always allow. The use-context gate does not apply because the
   upstream license permits commercial use.
3. Restricted entries (``commercial_use_restriction_known=True``):
   * If the user has not accepted the current acknowledgment
     wording, block as ``NEEDS_ACK``. Batch 4's click-through
     dialog is the resolution path.
   * If the use context is :data:`UseContext.COMMERCIAL`:
     * If the acceptance row asserts separate rights for this
       model, allow.
     * Otherwise, block as ``COMMERCIAL_NO_RIGHTS``.
   * If the use context is anything other than COMMERCIAL (
     personal, research, unspecified), allow.

Trap T7 — runtime registry reclassification

A separate guard in :func:`assert_no_silent_reclassification` —
called from the registry overlay path (Batch 8 will wire this in
fully when the signed remote registry lands) — ensures that an
entry whose ``commercial_use_restriction_known`` was ever ``True``
cannot be silently flipped to ``False`` at runtime. A relaxation
must come through the signed registry's dual-sig path (Q9). The
guard maintains a small process-wide set of "ever restricted"
ids so the check is O(1) on the overlay merge.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from bpp.registry.acceptance import is_acceptance_valid_for
from bpp.registry.acceptance_log import find_latest_for_model
from bpp.registry.model_registry import ModelEntry, ModelStatus
from bpp.registry.use_context import UseContext
from bpp.utils.logging import get_logger

_log = get_logger(__name__)


class ModelLoadDecision(StrEnum):
    """Outcome of :func:`check_model_load_allowed`. Every value other
    than :data:`ALLOW` is a hard block — the caller MUST NOT proceed
    to load the model."""

    ALLOW = "allow"
    BLOCKED_LEGAL = "blocked_legal"
    BLOCKED_NEEDS_ACK = "blocked_needs_ack"
    BLOCKED_COMMERCIAL_NO_RIGHTS = "blocked_commercial_no_rights"
    BLOCKED_WITHDRAWN = "blocked_withdrawn"


@dataclass(frozen=True)
class PolicyResult:
    """Structured outcome of the policy check.

    ``decision`` — the categorical decision. ALLOW means proceed;
    every other value means block.
    ``reason`` — short human-readable string suitable for an error
    message. For BLOCKED_NEEDS_ACK it tells the caller which dialog
    to open; for BLOCKED_COMMERCIAL_NO_RIGHTS it explains the
    override path.
    ``entry_id`` — the model id the decision applies to. Included
    so the error message and the log line carry the same handle
    without callers having to re-thread it.
    """

    decision: ModelLoadDecision
    reason: str
    entry_id: str


class ModelLoadBlockedError(RuntimeError):
    """Raised when policy denies a model load.

    Carries the :class:`PolicyResult` on the exception so the
    caller can render a specific error (and so the test suite can
    assert the categorical decision without parsing the message).
    """

    def __init__(self, result: PolicyResult) -> None:
        self.result = result
        super().__init__(
            f"Model load blocked: {result.entry_id} — {result.decision.value}: {result.reason}"
        )


def check_model_load_allowed(
    entry: ModelEntry,
    *,
    use_context: UseContext,
    acceptance_log_path: Path | None = None,
) -> PolicyResult:
    """Return the policy decision for loading ``entry`` under
    ``use_context``.

    Pure: only reads from the registry entry, the passed
    use_context, and the acceptance log. Does not write. Idempotent.
    Callers that want to raise on a block can pass the result to
    :func:`raise_if_blocked` or check ``result.decision is
    ModelLoadDecision.ALLOW`` directly.
    """
    # Rule 1: legally blocked beats every other condition.
    if entry.status is ModelStatus.LEGALLY_BLOCKED:
        return PolicyResult(
            decision=ModelLoadDecision.BLOCKED_LEGAL,
            reason=(
                "this model has been marked legally blocked at the "
                "registry level. No existing acceptance overrides "
                "this — load is refused."
            ),
            entry_id=entry.id,
        )

    # Rule 1b: withdrawn restricted entries block new loads.
    # Existing local copies remain usable per the multi-status
    # contract from Batch 1 / item 20 — but we don't know here
    # whether a local copy exists. The conservative call: if the
    # status is withdrawn AND the entry is restricted, block.
    # Permissive withdrawn entries fall through to the permissive
    # path below — they're typically retired versions of safe models.
    if entry.status is ModelStatus.WITHDRAWN_NO_NEW_DOWNLOADS and entry.requires_explicit_ack:
        return PolicyResult(
            decision=ModelLoadDecision.BLOCKED_WITHDRAWN,
            reason=(
                "this restricted model has been withdrawn from new "
                "downloads at the upstream level. Existing local "
                "copies may still work; new loads are refused."
            ),
            entry_id=entry.id,
        )

    # Rule 2: any entry that requires an explicit ack — restricted
    # third-party model OR BYOM user-responsibility ack — must have
    # a valid acceptance row on file. Checking this BEFORE the
    # commercial-restriction shortcut lets BYOM entries
    # (commercial_use_restriction_known=False but
    # requires_explicit_ack=True) still hit the ack gate without
    # the policy short-circuiting to ALLOW first.
    if entry.requires_explicit_ack and not is_acceptance_valid_for(
        entry, log_path=acceptance_log_path
    ):
        return PolicyResult(
            decision=ModelLoadDecision.BLOCKED_NEEDS_ACK,
            reason=(
                "this model requires the user to complete the "
                "click-through acknowledgment dialog first. Open the "
                f"dialog (web UI), run `bpp model accept {entry.id}` "
                "(CLI), or call the registry-coordinated acceptance "
                "flow."
            ),
            entry_id=entry.id,
        )

    # Rule 3: permissively-licensed entries (or BYOM entries whose
    # ack is now on file) — no restriction on the commercial-use
    # gate. Allow.
    if not entry.commercial_use_restriction_known:
        return PolicyResult(
            decision=ModelLoadDecision.ALLOW,
            reason=(
                "permissively-licensed or BYOM with user-responsibility "
                "ack on file; no commercial-restriction gate applies"
            ),
            entry_id=entry.id,
        )

    # Rule 4: restricted-license entries in commercial mode need
    # the per-model separate-rights assertion.
    if use_context is UseContext.COMMERCIAL:
        latest = find_latest_for_model(entry.id, path=acceptance_log_path)
        if latest is None or not latest.separate_rights_asserted:
            return PolicyResult(
                decision=ModelLoadDecision.BLOCKED_COMMERCIAL_NO_RIGHTS,
                reason=(
                    "use_context is 'commercial' and no separate-rights "
                    "assertion is on file for this model. To proceed: "
                    "re-run the acceptance flow under commercial mode "
                    "and check the separate-rights box for "
                    f"{entry.id}, or switch the use-context declaration "
                    "to a non-commercial value."
                ),
                entry_id=entry.id,
            )

    return PolicyResult(
        decision=ModelLoadDecision.ALLOW,
        reason=(f"restricted model with valid acceptance for use_context={use_context.value}"),
        entry_id=entry.id,
    )


def enforce_load_policy_for(model_id: str) -> None:
    """Single-call enforcement gate for restricted-model loaders.

    Called from each scoring loader (NudeNet, YOLOv11n pet detector,
    LaMa inpainter, buffalo_s face embedder, …) BEFORE the model
    file is opened. Resolves the entry from the registry, evaluates
    the policy under the current declared use-context, and raises
    :class:`ModelLoadBlockedError` on any non-ALLOW decision.

    Purpose
    -------

    Until this helper landed, the Batch 4 click-through dialog
    surfaces (CLI + web UI) recorded an acceptance row but did NOT
    actually gate inference — a user could ``pip install
    bppicker[nudity]`` and run NudeNet without ever seeing the
    dialog. This function closes that gap by making every restricted
    loader fail-closed when the acceptance row is missing or the
    commercial-mode gate trips.

    Permissive entries pass through silently — :func:`check_model_load_allowed`
    returns ALLOW immediately, and the loader proceeds without
    additional cost.

    Unknown model id
    ----------------

    An unknown ``model_id`` is treated as a programmer error and
    raises ``ValueError``. Loaders should pass the canonical
    registry id (e.g. ``"nudenet_320n"``); a typo here would
    silently allow the load otherwise.
    """
    # Late import: the registry surface routes through
    # bpp.registry.__init__ which imports policy.py — avoid a cycle
    # by importing the lookup lazily here.
    from bpp.registry.model_registry import get_entry
    from bpp.registry.use_context_store import get_use_context

    entry = get_entry(model_id)
    if entry is None:
        raise ValueError(
            f"enforce_load_policy_for: no registry entry with id "
            f"{model_id!r}. The loader is asking the policy gate "
            "about a model the registry has never heard of — most "
            "likely a typo in the model_id argument or an entry "
            "removed without updating the loader."
        )
    raise_if_blocked(check_model_load_allowed(entry, use_context=get_use_context()))


def raise_if_blocked(result: PolicyResult) -> None:
    """Raise :class:`ModelLoadBlockedError` if ``result`` is anything
    other than :data:`ModelLoadDecision.ALLOW`.

    Convenience helper so call sites can write
    ``raise_if_blocked(check_model_load_allowed(entry, ...))`` as a
    single guard.
    """
    if result.decision is ModelLoadDecision.ALLOW:
        return
    raise ModelLoadBlockedError(result)


# ── Trap T7 — runtime reclassification lock ──


_lock = threading.Lock()
_ever_restricted_ids: set[str] = set()


def assert_no_silent_reclassification(entry: ModelEntry) -> None:
    """Trap T7 guard. Raise if an entry whose
    ``commercial_use_restriction_known`` was ever ``True`` is being
    reclassified to ``False`` without going through the signed
    remote-registry dual-sig path.

    Called from the registry overlay merge (Batch 8 will wire the
    signed registry; until then the guard is exercised by tests
    and remains available for any code that wants to defend
    against a programming-error reclassification).

    Maintains a process-wide set of restricted ids. The set lives
    in this module rather than on the registry instance so the
    check survives a registry reset (tests that reset the registry
    don't accidentally relax the lock).
    """
    with _lock:
        if entry.commercial_use_restriction_known:
            _ever_restricted_ids.add(entry.id)
            return
        if entry.id in _ever_restricted_ids:
            raise RuntimeError(
                f"Silent reclassification refused: entry {entry.id!r} "
                "was previously registered as restricted "
                "(commercial_use_restriction_known=True) but is now "
                "being re-registered as permissive without going through "
                "the signed remote-registry dual-sig review. Relaxing a "
                "restriction class requires the second-maintainer "
                "signature (Q9). If this is intentional, the change "
                "must come through the signed registry overlay; if not, "
                "investigate why the registry merge logic produced this "
                "downgrade."
            )


def _reset_reclassification_lock_for_tests() -> None:
    """Clear the process-wide ever-restricted set. Test-only — the
    production code never needs to forget that an entry was once
    restricted."""
    with _lock:
        _ever_restricted_ids.clear()
