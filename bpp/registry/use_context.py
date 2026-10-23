"""Use-context enum + canonical commercial-use definition + biometric
responsibility language used by the click-through dialog.

Three constants and one enum live here:

* :class:`UseContext` — what the user declared on the
  commercial-use gate (persisted by
  :mod:`bpp.registry.use_context_store`; the acceptance flow reads
  it as input).
* :data:`COMMERCIAL_USE_DEFINITION` — the definition of "commercial
  use". Shown inside the click-through dialog so "commercial" means
  the same thing to every user.
* :data:`BIOMETRIC_RESPONSIBILITY_TEXT` — paragraph telling
  the user face embeddings are biometric data and they are
  responsible for consent / compliance (Colorado HB24-1130 and
  Texas CUBI mentioned as the obvious US examples, without
  interpreting those laws).
* :data:`SEPARATE_RIGHTS_ASSERTION_TEMPLATE` — the model-specific
  rights-assertion sentence, with the model display
  name interpolated at acceptance time.

These constants ship from one place so the dialog, the CLI prompt,
and the acceptance-log audit record all reference identical
strings. A bespoke per-surface rewrite is exactly the failure mode
the warning-parity rule protects against.

Versioning

The user-facing text is versioned via
:data:`USE_CONTEXT_TEXT_VERSION`. Bumping the version causes new
acceptances to record the new version while preserving the prior
version in older acceptance-log rows. The hash of the rendered
acknowledgment text lives on each acceptance row separately, so
even within one text version, a rendering-level change (e.g. a
specific entry's rationale) produces a distinct hash.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum


class UseContext(StrEnum):
    """User's declared use context. Set on first launch and read
    here by the acceptance flow.

    Values:

    * ``personal`` — purely personal photo curation.
    * ``research`` — academic / research use; for the purposes of
      the restricted-license gate, treated like ``personal`` because
      most restricted weights' "research-only" clause covers it.
    * ``commercial`` — paid work, client work, business operations,
      professional services, internal company use. Triggers the
      hard-block of restricted models unless the user opts into
      the separate-rights override.

    The ``unspecified`` value is the bootstrap state before the
    user has answered the commercial-use gate. Pipeline entry
    points that need a definitive answer should refuse to proceed
    on unspecified.
    """

    PERSONAL = "personal"
    RESEARCH = "research"
    COMMERCIAL = "commercial"
    UNSPECIFIED = "unspecified"


#: Version identifier for the user-facing text constants below.
#: Increment whenever :data:`COMMERCIAL_USE_DEFINITION`,
#: :data:`BIOMETRIC_RESPONSIBILITY_TEXT`,
#: :data:`SEPARATE_RIGHTS_ASSERTION_TEMPLATE`, or the text of
#: :data:`REQUIRED_ACK_CHECKBOXES` changes — all four feed
#: :func:`use_context_text_sha256`.
#:
#: v2: the ``download_from_upstream`` checkbox no longer says the
#: model is downloaded "directly from the upstream source" (one
#: restricted model's weights are fetched from a third-party re-host).
USE_CONTEXT_TEXT_VERSION = "use-context-text-v2"


#: Define "commercial use" inside the dialog so a future dispute
#: cannot turn on "but I'm not selling the model."
COMMERCIAL_USE_DEFINITION = (
    "Commercial use means paid work, client work, business operations, "
    "professional services, internal company use, or any use intended to "
    "generate revenue or support a commercial activity."
)


#: Biometric / privacy responsibility language. Tells the
#: user face embeddings are biometric data, that BPP's local-first
#: architecture does not absolve them of consent / compliance
#: obligations, and names Colorado and Texas as concrete examples
#: without claiming to enumerate every jurisdiction.
#:
#: Phrased as the user's responsibility rather than as Arkalogy's
#: legal opinion, matching the registry's non-warranty posture.
BIOMETRIC_RESPONSIBILITY_TEXT = (
    "Face embeddings are biometric data in many jurisdictions. You are "
    "responsible for the legality of capturing, processing, and storing "
    "biometric data in your jurisdiction and for any subjects depicted in "
    "your photos. Examples of laws that may apply include the Colorado "
    "biometric privacy amendments (HB24-1130, effective July 1, 2025) and "
    "the Texas Capture or Use of Biometric Identifier Act (CUBI). BPP's "
    "local-first architecture does not exempt you from these obligations."
)


#: Model-specific rights assertion. Rendered with the
#: model's display name at acceptance time so the acknowledgment is
#: tied to one specific model rather than to an open-ended "I have
#: rights to some restricted models." A future override of the
#: hard-block re-prompts with this template for each model.
SEPARATE_RIGHTS_ASSERTION_TEMPLATE = (
    "I have separate rights to use {model_display_name} for this use."
)


#: Required checkboxes shown in the click-through dialog. Each tuple
#: is ``(checkbox_id, user-visible-text)``. Order matters — the
#: dialog must render them in this sequence and the acceptance flow
#: must verify all four are checked.
#:
#: Phrasing: the user "acknowledges and agrees to comply with
#: the displayed upstream terms" rather than "agrees to upstream's
#: license" (the latter implies Arkalogy is the upstream's agent).
REQUIRED_ACK_CHECKBOXES: tuple[tuple[str, str], ...] = (
    (
        "not_for_commercial_use",
        "I understand this model is not licensed for commercial use.",
    ),
    (
        "mit_does_not_grant_model_rights",
        "I understand Best Photo Picker's MIT license does not grant rights to this model.",
    ),
    (
        "download_from_upstream",
        "I understand Best Photo Picker downloads this model from a "
        "third-party host, not from Arkalogy.",
    ),
    (
        "agree_no_commercial_use_without_separate_rights",
        "I agree not to use this model for paid, client, business, or "
        "commercial workflows unless I obtain separate rights from the "
        "model owner.",
    ),
)


#: Single required checkbox shown on the permissive-attribution dialog.
#: Used in place of :data:`REQUIRED_ACK_CHECKBOXES` for entries whose
#: ``ack_text_kind`` is ``"permissive_attribution"`` — Apache 2.0,
#: BSD, Boost, etc. The disclaimer paragraphs already explain WHAT
#: the obligations are; this checkbox is the affirmative
#: acknowledgment that the user understands them.
PERMISSIVE_ATTRIBUTION_REQUIRED_CHECKBOXES: tuple[tuple[str, str], ...] = (
    (
        "permissive_attribution_acknowledged",
        "I acknowledge this permissive license requires preserving the "
        "copyright notice and license text (and any NOTICE file, where "
        "applicable) if I redistribute the model or ship a product that "
        "includes it.",
    ),
)


def required_checkbox_ids() -> frozenset[str]:
    """Return the set of checkbox ids the acceptance flow requires."""
    return frozenset(cb_id for cb_id, _ in REQUIRED_ACK_CHECKBOXES)


def use_context_text_sha256() -> str:
    """Hex SHA-256 of the bundle of user-facing text constants.

    The acceptance log records this so a future maintainer can prove
    which specific wording the user accepted, even after a version
    bump changes the on-screen text for new acceptances.
    """
    blob = "\n\n".join(
        [
            f"version={USE_CONTEXT_TEXT_VERSION}",
            COMMERCIAL_USE_DEFINITION,
            BIOMETRIC_RESPONSIBILITY_TEXT,
            SEPARATE_RIGHTS_ASSERTION_TEMPLATE,
            "\n".join(text for _, text in REQUIRED_ACK_CHECKBOXES),
        ]
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
