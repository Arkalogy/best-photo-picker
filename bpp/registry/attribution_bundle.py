"""Accept every attribution-licensed model in one step.

Apache 2.0 / BSD-style models (SFace, YuNet, the MediaPipe models, the
Haar cascade) carry attribution duties, so each needs an acceptance row
before it loads — but asking a new user to open eight separate dialogs
before their first analysis is friction with no legal benefit, because
every one of those dialogs shows the same permissive-attribution text
and the same single checkbox.

This module builds one combined prompt for all of them and writes one
acceptance row per model, each identical to the row the per-model
dialog writes. Restricted (research / copyleft) models are never part
of the bundle — they keep their own four-checkbox dialog.

The web pre-analysis dialog and ``bpp model accept-attribution`` both
call into here, so the two surfaces stay in lock-step.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from bpp.registry.acceptance import (
    AcceptanceError,
    confirm_acceptance,
    is_acceptance_valid_for,
    prepare_acceptance,
)
from bpp.registry.acceptance_log import AcceptanceRow
from bpp.registry.disclaimers import (
    PERMISSIVE_ATTRIBUTION_DISCLAIMER,
    PERMISSIVE_ATTRIBUTION_DISCLAIMER_COMPRESSED,
)
from bpp.registry.model_registry import ModelEntry, ModelStatus, get_entry, iter_entries
from bpp.registry.use_context import (
    BIOMETRIC_RESPONSIBILITY_TEXT,
    PERMISSIVE_ATTRIBUTION_REQUIRED_CHECKBOXES,
    UseContext,
)

_KIND = "permissive_attribution"


def is_attribution_entry(entry: ModelEntry) -> bool:
    """True for a model whose click-through is the attribution notice."""
    return entry.requires_explicit_ack and entry.ack_text_kind == _KIND


def pending_attribution_entries(*, log_path: Path | None = None) -> list[ModelEntry]:
    """Attribution models with no current acceptance, in registry order.

    Legally-blocked entries are left out — accepting their notice would
    not let them load.
    """
    return [
        e
        for e in iter_entries()
        if is_attribution_entry(e)
        and e.status is not ModelStatus.LEGALLY_BLOCKED
        and not is_acceptance_valid_for(e, log_path=log_path)
    ]


@dataclass(frozen=True)
class AttributionBundle:
    """Everything the combined prompt renders and validates against."""

    entries: tuple[ModelEntry, ...]
    compressed_disclaimer: str
    full_disclaimer: str
    biometric_responsibility_text: str
    required_checkboxes: tuple[tuple[str, str], ...]


def prepare_attribution_bundle(entries: Iterable[ModelEntry]) -> AttributionBundle:
    """Build the combined prompt for ``entries``.

    The biometric note is included when any listed model handles
    facial or hand geometry, matching the per-model dialog.
    """
    items = tuple(entries)
    biometric = any(e.produces_biometric_data for e in items)
    return AttributionBundle(
        entries=items,
        compressed_disclaimer=PERMISSIVE_ATTRIBUTION_DISCLAIMER_COMPRESSED,
        full_disclaimer=PERMISSIVE_ATTRIBUTION_DISCLAIMER,
        biometric_responsibility_text=BIOMETRIC_RESPONSIBILITY_TEXT if biometric else "",
        required_checkboxes=PERMISSIVE_ATTRIBUTION_REQUIRED_CHECKBOXES,
    )


def confirm_attribution_bundle(
    model_ids: Iterable[str],
    *,
    checkbox_responses: Mapping[str, bool],
    accepted_at: str,
    use_context: UseContext = UseContext.UNSPECIFIED,
    log_path: Path | None = None,
) -> list[AcceptanceRow]:
    """Record an acceptance row for every model in ``model_ids``.

    Everything is validated before the first row is written, so a bad
    request records nothing: every id must name an attribution model,
    and every required checkbox must be ticked.
    """
    ids = list(dict.fromkeys(model_ids))
    if not ids:
        raise AcceptanceError("confirm_attribution_bundle: no models to accept.")
    entries: list[ModelEntry] = []
    for model_id in ids:
        entry = get_entry(model_id)
        if entry is None:
            raise AcceptanceError(f"confirm_attribution_bundle: unknown model {model_id!r}.")
        if not is_attribution_entry(entry):
            raise AcceptanceError(
                f"confirm_attribution_bundle: {model_id!r} is not an "
                "attribution-licensed model. Restricted models need their "
                "own acceptance dialog."
            )
        entries.append(entry)
    required = [cb_id for cb_id, _ in PERMISSIVE_ATTRIBUTION_REQUIRED_CHECKBOXES]
    unchecked = [cb_id for cb_id in required if not checkbox_responses.get(cb_id)]
    if unchecked:
        raise AcceptanceError(
            "confirm_attribution_bundle: user must check every required box. "
            f"Unchecked: {unchecked}."
        )
    responses = {cb_id: True for cb_id in required}
    return [
        confirm_acceptance(
            prepare_acceptance(entry, use_context=use_context),
            checkbox_responses=responses,
            accepted_at=accepted_at,
            log_path=log_path,
        )
        for entry in entries
    ]
