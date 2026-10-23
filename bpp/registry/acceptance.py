"""Click-through acceptance flow: build the dialog payload, verify
the user's response, write the audit row.

The HTML/JS dialog (and the CLI text prompt) call into this module —
neither side re-implements the four-checkbox +
commercial-use-definition + biometric-note + rights-assertion gate.
One implementation here keeps all surfaces in lock-step, so every
surface shows the same warnings.

Entry points:

* :func:`prepare_acceptance` — given a :class:`ModelEntry` and the
  user's declared :class:`UseContext`, build a structured
  :class:`AcceptanceDraft` containing every string the dialog
  must render and the checkbox set the user must check off.
* :func:`confirm_acceptance` — verify the user's response matches
  the draft (all required boxes checked, the optional
  source-of-rights field is a string, and so on), write the
  audit row to the acceptance log, return the row.
* :func:`acceptance_state` — the one judgement of an acceptance (current /
  needs re-accept / withdrawn) that every reader uses
* :func:`is_acceptance_valid_for` — ``acceptance_state(entry).current``:
  what the model-load hard-block gate (:mod:`bpp.registry.policy`) asks.
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from bpp.registry.acceptance_log import (
    AcceptanceRow,
    append_row,
    find_latest_for_model,
)
from bpp.registry.disclaimers import (
    BYOM_DISCLAIMER,
    BYOM_DISCLAIMER_COMPRESSED,
    BYOM_DISCLAIMER_VERSION,
    CANONICAL_DISCLAIMER,
    CANONICAL_DISCLAIMER_COMPRESSED,
    CANONICAL_DISCLAIMER_VERSION,
    PERMISSIVE_ATTRIBUTION_DISCLAIMER,
    PERMISSIVE_ATTRIBUTION_DISCLAIMER_COMPRESSED,
    PERMISSIVE_ATTRIBUTION_DISCLAIMER_VERSION,
    byom_disclaimer_sha256,
    canonical_disclaimer_sha256,
    permissive_attribution_disclaimer_sha256,
)
from bpp.registry.model_registry import ModelEntry
from bpp.registry.use_context import (
    BIOMETRIC_RESPONSIBILITY_TEXT,
    COMMERCIAL_USE_DEFINITION,
    PERMISSIVE_ATTRIBUTION_REQUIRED_CHECKBOXES,
    REQUIRED_ACK_CHECKBOXES,
    SEPARATE_RIGHTS_ASSERTION_TEMPLATE,
    USE_CONTEXT_TEXT_VERSION,
    UseContext,
    use_context_text_sha256,
)


class AcceptanceError(RuntimeError):
    """The user's response did not satisfy the acceptance flow."""


@dataclass(frozen=True)
class AcceptanceDraft:
    """Everything the dialog (or CLI prompt) needs to render and
    everything :func:`confirm_acceptance` needs to validate.

    Frozen — the dialog must not be able to mutate the strings
    between rendering and confirmation; that would defeat the
    snapshot-hash audit trail.

    Fields:

    * ``entry`` — the :class:`ModelEntry` the user is being asked
      to accept.
    * ``compressed_disclaimer`` — short two-sentence summary for
      the dialog header.
    * ``full_disclaimer`` — full canonical disclaimer text
      surfaced behind a "Read full terms" link.
    * ``commercial_use_definition`` — the in-dialog definition of
      "commercial use".
    * ``biometric_responsibility_text`` — the biometric-data
      responsibility note.
    * ``required_checkboxes`` — ordered tuple of
      ``(checkbox_id, user-visible-text)`` pairs. All must be
      checked for the acceptance to confirm.
    * ``separate_rights_assertion`` — model-specific assertion
      rendered with the entry's display name.
    * ``ack_text_version`` + ``ack_text_sha256`` — pointers to the
      canonical disclaimer wording so the dialog and the
      acceptance-log row reference the same exact bytes.
    * ``use_context`` — the user's declared use context the dialog
      should label with.
    """

    entry: ModelEntry
    compressed_disclaimer: str
    full_disclaimer: str
    commercial_use_definition: str
    biometric_responsibility_text: str
    required_checkboxes: tuple[tuple[str, str], ...]
    separate_rights_assertion: str
    ack_text_version: str
    ack_text_sha256: str
    use_context_text_version: str
    use_context_text_sha256: str
    use_context: UseContext = field(default=UseContext.UNSPECIFIED)


#: Single required checkbox shown on the BYOM dialog. Used in place of
#: :data:`REQUIRED_ACK_CHECKBOXES` for entries whose ``ack_text_kind``
#: is ``"byom"``. The user-responsibility paragraph carries the rights
#: chain; the checkbox is the affirmative acknowledgment.
BYOM_REQUIRED_CHECKBOXES: tuple[tuple[str, str], ...] = (
    (
        "byom_user_takes_responsibility",
        "I confirm I have rights to use this model file for my "
        "intended purpose. I understand bppicker does not verify or "
        "grant rights to user-provided model files.",
    ),
)


def prepare_acceptance(
    entry: ModelEntry,
    *,
    use_context: UseContext = UseContext.UNSPECIFIED,
) -> AcceptanceDraft:
    """Build an :class:`AcceptanceDraft` for ``entry``.

    Pure — does not read the acceptance log, does not write
    anything. Pure determinism is important: the same call must
    return the same draft so the dialog rendering and the
    confirmation check agree on every byte.

    Dispatches on ``entry.ack_text_kind``:

    * ``"canonical"`` (default) — restricted third-party model.
      Dialog renders the canonical disclaimer family + four
      required checkboxes + commercial-use definition + biometric
      responsibility text + per-model rights assertion.

    * ``"byom"`` — user-supplied Bring-Your-Own-Model file.
      Dialog renders the shorter BYOM disclaimer +
      single user-responsibility checkbox. The commercial-use
      definition and biometric text are still shown (face
      embeddings are biometric regardless of source) but the
      restricted-model checkboxes do not apply because there is
      no upstream license for Arkalogy to gate against.

    Caller responsibility: pass the user's declared
    :class:`UseContext` from the commercial-use gate. When the user
    hasn't declared one yet, ``UseContext.UNSPECIFIED`` is a valid
    input — the dialog will label the use context as "not
    specified."
    """
    if entry.ack_text_kind == "byom":
        # BYOM dialog: shorter disclaimer, single checkbox, no
        # per-model rights assertion (there is no upstream model
        # owner whose rights the user could be asserting separately).
        return AcceptanceDraft(
            entry=entry,
            compressed_disclaimer=BYOM_DISCLAIMER_COMPRESSED,
            full_disclaimer=BYOM_DISCLAIMER,
            commercial_use_definition=COMMERCIAL_USE_DEFINITION,
            biometric_responsibility_text=BIOMETRIC_RESPONSIBILITY_TEXT,
            required_checkboxes=BYOM_REQUIRED_CHECKBOXES,
            separate_rights_assertion="",
            ack_text_version=BYOM_DISCLAIMER_VERSION,
            ack_text_sha256=byom_disclaimer_sha256(),
            use_context_text_version=USE_CONTEXT_TEXT_VERSION,
            use_context_text_sha256=use_context_text_sha256(),
            use_context=use_context,
        )

    if entry.ack_text_kind == "permissive_attribution":
        # Permissive-attribution dialog: Apache 2.0 / BSD / Boost
        # entries. Shorter disclaimer + single attribution-
        # acknowledgment checkbox. No commercial-use restrictions
        # apply (these licenses permit commercial use), so we don't
        # include the not-for-commercial checkbox or the separate-
        # rights assertion. The commercial-use definition and the
        # biometric-responsibility text stay because face embeddings
        # are biometric regardless of the model's license posture.
        return AcceptanceDraft(
            entry=entry,
            compressed_disclaimer=PERMISSIVE_ATTRIBUTION_DISCLAIMER_COMPRESSED,
            full_disclaimer=PERMISSIVE_ATTRIBUTION_DISCLAIMER,
            commercial_use_definition=COMMERCIAL_USE_DEFINITION,
            biometric_responsibility_text=BIOMETRIC_RESPONSIBILITY_TEXT,
            required_checkboxes=PERMISSIVE_ATTRIBUTION_REQUIRED_CHECKBOXES,
            separate_rights_assertion="",
            ack_text_version=PERMISSIVE_ATTRIBUTION_DISCLAIMER_VERSION,
            ack_text_sha256=permissive_attribution_disclaimer_sha256(),
            use_context_text_version=USE_CONTEXT_TEXT_VERSION,
            use_context_text_sha256=use_context_text_sha256(),
            use_context=use_context,
        )

    # Default / "canonical" — restricted third-party model.
    return AcceptanceDraft(
        entry=entry,
        compressed_disclaimer=CANONICAL_DISCLAIMER_COMPRESSED,
        full_disclaimer=CANONICAL_DISCLAIMER,
        commercial_use_definition=COMMERCIAL_USE_DEFINITION,
        biometric_responsibility_text=BIOMETRIC_RESPONSIBILITY_TEXT,
        required_checkboxes=REQUIRED_ACK_CHECKBOXES,
        separate_rights_assertion=SEPARATE_RIGHTS_ASSERTION_TEMPLATE.format(
            model_display_name=entry.display_name,
        ),
        ack_text_version=CANONICAL_DISCLAIMER_VERSION,
        ack_text_sha256=canonical_disclaimer_sha256(),
        use_context_text_version=USE_CONTEXT_TEXT_VERSION,
        use_context_text_sha256=use_context_text_sha256(),
        use_context=use_context,
    )


def confirm_acceptance(
    draft: AcceptanceDraft,
    *,
    checkbox_responses: Mapping[str, bool],
    accepted_at: str,
    separate_rights_asserted: bool = False,
    source_of_rights_note: str = "",
    log_path: Path | None = None,
) -> AcceptanceRow:
    """Validate the user's response, write the acceptance row,
    return the row.

    Raises :class:`AcceptanceError` if any required checkbox is
    missing or unchecked, or if ``accepted_at`` is empty.
    ``separate_rights_asserted`` and ``source_of_rights_note`` are
    persisted verbatim — neither is validated against the
    declared use context because the model-load policy gate
    (:mod:`bpp.registry.policy`) enforces that (so the same call shape works for both
    commercial-mode-blocked overrides and non-commercial-mode
    voluntary attestations).

    The row is written through :mod:`bpp.registry.acceptance_log`
    so the JSONL file location and the platform-config-dir lookup
    stay in one place.
    """
    if not accepted_at:
        raise AcceptanceError(
            "confirm_acceptance: accepted_at is empty. The caller must "
            "stamp an ISO-8601 timestamp in UTC."
        )
    # Audit trail: restricted third-party entries
    # MUST carry a permalink so the acceptance row records a URL that
    # resolves to the exact wording the user saw, years later. A
    # missing or empty permalink would record the floating
    # ``terms_url`` (typically a main-branch URL) which rots when the
    # upstream rewrites their README. BYOM entries are exempt —
    # there is no upstream owner whose permalink we could pin; the
    # ack_text_sha256 carries the evidence.
    entry = draft.entry
    if (
        entry.requires_explicit_ack
        and entry.ack_text_kind != "byom"
        and not (entry.terms_permalink_url or "").strip()
    ):
        raise AcceptanceError(
            "confirm_acceptance: restricted-license entry "
            f"{entry.id!r} has no terms_permalink_url. The acceptance "
            "row would record a floating ``terms_url`` that may rot "
            "when the upstream rewrites their README, breaking the "
            "audit trail. Register the entry with a permalink "
            "pinned to a specific upstream commit."
        )
    # Required ids come from the draft, not the global canonical list:
    # BYOM drafts ship a different (shorter) checkbox set so the
    # required-ids check must match what the dialog actually rendered.
    required_ids = frozenset(cb_id for cb_id, _ in draft.required_checkboxes)
    missing = required_ids - set(checkbox_responses.keys())
    if missing:
        raise AcceptanceError(
            "confirm_acceptance: missing required checkbox responses: "
            f"{sorted(missing)}. The dialog must POST every required "
            "checkbox even when the user did not check it (use "
            "value=false rather than omitting)."
        )
    # Only a real True counts — "false" (a non-empty string) must never
    # read as a checked box or a rights assertion.
    not_bool = sorted(
        cb_id for cb_id in required_ids if type(checkbox_responses[cb_id]) is not bool
    )
    if not_bool or type(separate_rights_asserted) is not bool:
        raise AcceptanceError(
            "confirm_acceptance: checkbox responses and separate_rights_asserted "
            f"must be true or false (got non-boolean: {not_bool or ['separate_rights_asserted']})."
        )
    unchecked = [cb_id for cb_id in required_ids if not checkbox_responses[cb_id]]
    if unchecked:
        raise AcceptanceError(
            "confirm_acceptance: user must check every required box. "
            f"Unchecked: {sorted(unchecked)}."
        )

    # Per-checkbox audit trail (schema v2): the per-checkbox map is
    # the artifact, not the values. Filter to required ids only —
    # the dialog can post extra ids the user clicked but we don't
    # care about; we record exactly the set the registry says is
    # required for this entry's ack_text_kind.
    persisted_checkboxes = {cb_id: checkbox_responses[cb_id] for cb_id in required_ids}
    row = AcceptanceRow(
        model_id=draft.entry.id,
        model_sha256=draft.entry.weight_sha256,
        ack_text_version=draft.ack_text_version,
        ack_text_sha256=draft.ack_text_sha256,
        use_context_text_version=draft.use_context_text_version,
        use_context_text_sha256=draft.use_context_text_sha256,
        use_context_at_acceptance=draft.use_context.value,
        separate_rights_asserted=separate_rights_asserted,
        terms_url=draft.entry.terms_url,
        terms_permalink_url=draft.entry.terms_permalink_url or "",
        terms_retrieved_at=draft.entry.terms_retrieved_at,
        accepted_at=accepted_at,
        source_of_rights_note=source_of_rights_note,
        checkbox_responses=persisted_checkboxes,
    )
    append_row(row, path=log_path)
    return row


@dataclass(frozen=True)
class AcceptanceState:
    """One model's license acceptance, as every reader sees it — the load
    gate, the acceptance list and the picker all decide from this, so a
    change to the rule is made once.

    * ``latest`` — the row judged (the model's latest, or the one given).
    * ``accepted`` — that row is an acceptance, not a withdrawal.
    * ``current`` — it may load: accepted under the entry's CURRENT
      acknowledgment text (a registry update that changes the wording —
      a :data:`CANONICAL_DISCLAIMER_VERSION` bump, a new model-specific
      rationale — invalidates older acceptances and re-prompts the user;
      that is the desired behaviour). An entry needing no click-through
      is current without the log being read at all (the gate's own
      short-circuit); a row given for one is current only if it is an
      acceptance.
    * ``needs_reaccept`` — accepted, but under an older text.
    * ``separate_rights_asserted`` — the commercial-use rights assertion
      on that row (rule 4 of the load policy).
    """

    latest: AcceptanceRow | None
    accepted: bool
    current: bool
    needs_reaccept: bool
    separate_rights_asserted: bool


def acceptance_state(
    entry: ModelEntry | None,
    *,
    row: AcceptanceRow | None = None,
    log_path: Path | None = None,
) -> AcceptanceState:
    """Judge ``entry``'s acceptance: its latest log row (one read), or
    ``row`` when given (the acceptance list judges every row). ``entry``
    is None for a row whose model the registry no longer knows — never
    current, but still an acceptance that would need re-accepting."""
    if row is None and entry is not None and not entry.requires_explicit_ack:
        # No click-through needed: it may load, and the log isn't read
        # (a stray withdrawal row for it changes nothing).
        return AcceptanceState(
            latest=None,
            accepted=False,
            current=True,
            needs_reaccept=False,
            separate_rights_asserted=False,
        )
    if row is None and entry is not None:
        row = find_latest_for_model(entry.id, path=log_path)
    # A withdrawal (a revocation as the latest row) re-gates the model —
    # the load policy blocks it until the user re-accepts.
    accepted = row is not None and row.event == "accept"
    if entry is None:
        current = False
    elif not entry.requires_explicit_ack:
        # (A given row for a no-click-through entry: an acceptance is
        # current; a withdrawal isn't one.)
        current = accepted
    else:
        current = accepted and row is not None and row.ack_text_sha256 == entry.ack_text_sha256
    return AcceptanceState(
        latest=row,
        accepted=accepted,
        current=current,
        needs_reaccept=accepted and not current,
        separate_rights_asserted=row is not None and bool(row.separate_rights_asserted),
    )


def is_acceptance_valid_for(
    entry: ModelEntry,
    *,
    log_path: Path | None = None,
) -> bool:
    """Whether ``entry`` may load as far as its license goes — see
    :class:`AcceptanceState.current`. The model-load hard-block gate
    calls this before letting a restricted model load."""
    return acceptance_state(entry, log_path=log_path).current


def utc_now_iso() -> str:
    """Helper for callers that want a default ``accepted_at`` value.

    Kept here rather than baked into :func:`confirm_acceptance` so
    tests can pass a fixed timestamp without monkey-patching the
    clock.
    """
    return datetime.datetime.now(datetime.UTC).isoformat()
