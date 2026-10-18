"""Click-through dialog endpoints for the restricted-model registry.

The HTML/JS dialog
(and the CLI text prompt — see :mod:`bpp.commands.model` for the
CLI parity story) POSTs through here. The blueprint stays thin —
all the validation + persistence lives in
:mod:`bpp.registry.acceptance`. This module only deals with HTTP.

Endpoints

  GET  /api/v1/model-registry/entries
      Return the structured list of registered :class:`ModelEntry`
      records grouped by license posture (permissive vs
      restricted). Renders the picker.

  GET  /api/v1/model-registry/acceptance/draft?model_id=<id>&use_context=<ctx>
      Return the :class:`AcceptanceDraft` payload for the dialog —
      every string the dialog must render plus the checkbox set.
      Read-only.

  POST /api/v1/model-registry/acceptance/confirm
      Body: {model_id, use_context, checkbox_responses,
             accepted_at, separate_rights_asserted,
             source_of_rights_note}. Validates the response,
      persists the acceptance row, returns the persisted record.

  GET  /api/v1/model-registry/acceptance/list
      Read-only view of the acceptance log. Settings → "View your
      acceptances" panel consumes this.

  POST /api/v1/model-registry/acceptance/revoke
      Body: {model_id}. Withdraw a prior acceptance. Append-only — the
      original acceptance row is preserved; a new event="revoke" row
      supersedes it and the model re-gates until re-accepted.

Every mutating route requires the @requires_local_app gate (the
same gate the rest of BPP uses for owner-only operations) so a LAN
device cannot drive someone else's acceptance. The two read
endpoints are loopback-only by virtue of the gate as well: the
dialog payload contains license text the user is about to be asked
to accept on their device, not a public surface.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from flask import Blueprint, jsonify, request

from bpp.errors import ValidationError
from bpp.registry import (
    AcceptanceError,
    UseContext,
    append_revocation,
    confirm_acceptance,
    get_entry,
    iter_entries,
    prepare_acceptance,
    read_record,
    set_use_context,
    utc_now_iso,
)
from bpp.registry.labels import group_for_entry, ui_label_for_entry
from bpp.utils.logging import get_logger
from bpp.web.bp_catalog import _catalog_loaders
from bpp.web.share import requires_local_app

log = get_logger(__name__)

bp = Blueprint("model_registry", __name__)


def _entry_to_picker_dict(
    entry: Any, loaders: dict | None = None, states: dict[str, str] | None = None
) -> dict[str, Any]:
    """Serialise one :class:`ModelEntry` into the picker shape.

    ``loaders`` is the catalog-loader map (``_catalog_loaders()``). The
    entries endpoint computes it ONCE and passes it in so this isn't
    rebuilt (with its lazy imports) per row. Callers that serialise a
    single entry can omit it — it's resolved lazily. Resolved per call
    rather than cached at module level so test patches on the loader
    functions still take effect.
    """
    if loaders is None:
        loaders = _catalog_loaders()
    group_title, group_subtitle = group_for_entry(entry)
    catalog_info = loaders.get(entry.id)
    return {
        "id": entry.id,
        "display_name": entry.display_name,
        "kind": entry.kind,
        "ui_label": ui_label_for_entry(entry),
        "group_title": group_title,
        "group_subtitle": group_subtitle,
        "license_summary": entry.license_summary,
        "status": entry.status.value,
        "requires_explicit_ack": entry.requires_explicit_ack,
        "commercial_use_restriction_known": (entry.commercial_use_restriction_known),
        "default_for_kind": entry.default_for_kind,
        "runs_alongside_default": getattr(entry, "runs_alongside_default", False),
        "ships_with_runtime": getattr(entry, "ships_with_runtime", False),
        "ack_text_kind": entry.ack_text_kind,
        # The load gate's decision (ok / needs_license / needs_reaccept /
        # needs_commercial_rights / blocked) — the row's license state.
        "load_state": _load_state(entry, states),
        # Surfaced so the Settings → Models picker can show the
        # download size BEFORE the user accepts and triggers the
        # fetch. ``0`` means unknown — picker falls back to "—".
        "expected_download_size_bytes": getattr(entry, "expected_download_size_bytes", 0),
        # Catalog entries are runtime-fetched (weights pulled on demand,
        # not in the download manifest). The picker reads these to drive
        # the download lifecycle. ``is_catalog_entry`` says "this entry's
        # weights come via the catalog ensure-weights endpoint" — it is
        # the authoritative routing signal, independent of whether a
        # (fileless) legacy install record happens to be attached. Before
        # this flag the picker inferred catalog-ness from "no install
        # record", which broke for LaMa / NudeNet: both DO have a legacy
        # feature row (with an empty file list), so they were misrouted
        # and lost their Download action entirely.
        "is_catalog_entry": bool(catalog_info),
        # ``catalog_on_disk`` is the real on-disk check for catalog
        # entries. ``False`` for non-catalog entries (they report
        # presence via ``install.files``).
        "catalog_on_disk": bool(catalog_info and catalog_info[0]()),
    }


@bp.get("/api/v1/model-registry/entries")
@requires_local_app
def api_model_registry_entries() -> tuple[Any, int]:
    """List every registered :class:`ModelEntry`, grouped by license
    posture.

    Response shape:

    .. code-block:: json

        {
          "groups": [
            {"title": "...", "subtitle": "...", "entries": [...]},
            ...
          ]
        }
    """
    from bpp.registry import iter_byom_model_entries

    # Build the catalog-loader map once for the whole response instead
    # of rebuilding it (with its lazy imports) inside every row.
    loaders = _catalog_loaders()
    states = _registry_load_states()  # cached; one pass for the whole response
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in iter_entries():
        key = group_for_entry(entry)
        grouped.setdefault(key, []).append(_entry_to_picker_dict(entry, loaders, states))
    # BYOM entries appear in the picker under their own group so
    # users can distinguish their own files from registered models.
    # group_for_entry returns the permissive subtitle for BYOM
    # (since BYOM is commercial_use_restriction_known=False); the
    # ack_text_kind=="byom" surface in the picker dict tells the UI
    # to render them in a separate sub-section.
    for entry in iter_byom_model_entries():
        key = group_for_entry(entry)
        grouped.setdefault(key, []).append(_entry_to_picker_dict(entry, loaders, states))
    # Stable ordering: permissive first, restricted second. The
    # permissive group does not carry the "commercial use restricted"
    # signal so a maintainer reading the picker output sees the safe
    # entries before the restricted ones.
    response_groups: list[dict[str, Any]] = []
    for (title, subtitle), entries in grouped.items():
        response_groups.append({"title": title, "subtitle": subtitle, "entries": entries})
    response_groups.sort(
        key=lambda g: 0 if "permissive" in g["title"].lower() else 1,
    )
    return jsonify({"groups": response_groups}), 200


def _registry_load_states() -> dict[str, str] | None:
    """Cached gate decisions for every registry entry (non-OK only), or
    None if they can't be read (an unreadable acceptance log): the picker
    then falls back to the acceptance list instead of failing to load."""
    from bpp.web.model_filter import model_load_states

    try:
        return model_load_states()
    except Exception:
        log.warning("Couldn't compute model load states", exc_info=True)
        return None


def _load_state(entry: Any, states: dict[str, str] | None) -> str | None:
    """One row's load-gate decision: from the cached registry map, or
    computed for a user-supplied (BYOM) entry, which the map doesn't hold."""
    if states is None:
        return None
    if not entry.id.startswith("byom_"):
        return states.get(entry.id, "ok")
    from bpp.web.model_filter import load_state

    try:
        return load_state(entry)
    except Exception:
        log.warning("Couldn't compute the load state of %s", entry.id, exc_info=True)
        return None


def _find_any_entry(model_id: str) -> Any:
    """Return a :class:`ModelEntry` for ``model_id`` whether it's a
    built-in registry entry or a user-supplied BYOM entry. ``None``
    if neither has it. BYOM entries are looked up first because
    their ids carry a ``byom_`` prefix that registered entries never
    use, so the dispatch is unambiguous.
    """
    from bpp.registry import get_byom_entry

    if model_id.startswith("byom_"):
        byom = get_byom_entry(model_id)
        if byom is not None:
            return byom.to_model_entry()
        return None
    return get_entry(model_id)


def _parse_use_context(value: str | None) -> UseContext:
    """Translate a request parameter to a :class:`UseContext`, with
    a clear error on an unknown value rather than silently defaulting.
    Empty / missing → UNSPECIFIED."""
    if not value:
        return UseContext.UNSPECIFIED
    try:
        return UseContext(value)
    except ValueError as exc:
        raise ValidationError(
            f"Unknown use_context value: {value!r}. Expected one of "
            f"{[c.value for c in UseContext]}."
        ) from exc


@bp.get("/api/v1/model-registry/acceptance/draft")
@requires_local_app
def api_acceptance_draft() -> tuple[Any, int]:
    """Return the :class:`AcceptanceDraft` payload for one model."""
    model_id = request.args.get("model_id", "").strip()
    if not model_id:
        raise ValidationError("model_id is required")
    entry = _find_any_entry(model_id)
    if entry is None:
        raise ValidationError(f"Unknown model_id: {model_id!r}")
    use_context = _parse_use_context(request.args.get("use_context"))
    draft = prepare_acceptance(entry, use_context=use_context)
    return (
        jsonify(
            {
                "model_id": draft.entry.id,
                "model_display_name": draft.entry.display_name,
                "compressed_disclaimer": draft.compressed_disclaimer,
                "full_disclaimer": draft.full_disclaimer,
                "commercial_use_definition": draft.commercial_use_definition,
                # Suppress the biometric block on entries that don't
                # actually consume biometric data (pet detector,
                # nudity classifier, semantic search, inpainter). The
                # canonical disclaimer in CANONICAL_DISCLAIMER still
                # carries the paragraph — the acceptance row's
                # ack_text_sha256 covers the same baseline. The GUI
                # block is suppressed here for relevance only.
                "biometric_responsibility_text": (
                    draft.biometric_responsibility_text
                    if draft.entry.produces_biometric_data
                    else ""
                ),
                "produces_biometric_data": (draft.entry.produces_biometric_data),
                "required_checkboxes": [
                    {"id": cb_id, "text": text} for cb_id, text in draft.required_checkboxes
                ],
                "separate_rights_assertion": (draft.separate_rights_assertion),
                "ack_text_version": draft.ack_text_version,
                "ack_text_sha256": draft.ack_text_sha256,
                "use_context_text_version": draft.use_context_text_version,
                "use_context_text_sha256": draft.use_context_text_sha256,
                "use_context": draft.use_context.value,
                "terms_url": draft.entry.terms_url,
                "terms_permalink_url": draft.entry.terms_permalink_url or "",
                "terms_retrieved_at": draft.entry.terms_retrieved_at,
            }
        ),
        200,
    )


@bp.post("/api/v1/model-registry/acceptance/confirm")
@requires_local_app
def api_acceptance_confirm() -> tuple[Any, int]:
    """Validate the dialog response, persist the acceptance row,
    return the row."""
    body = request.get_json(silent=True) or {}
    model_id = (body.get("model_id") or "").strip()
    if not model_id:
        raise ValidationError("model_id is required")
    entry = _find_any_entry(model_id)
    if entry is None:
        raise ValidationError(f"Unknown model_id: {model_id!r}")
    from bpp.registry.policy import registry_block

    if registry_block(entry) is not None:
        # Withdrawn or legally blocked: an acceptance couldn't enable it,
        # so don't record one (or tell the user it worked). Status only —
        # no file reads, so this can't fail.
        raise ValidationError(
            f"{entry.display_name} has been withdrawn or blocked in the model "
            "registry, so it can't be enabled."
        )
    use_context = _parse_use_context(body.get("use_context"))
    raw_checkboxes = body.get("checkbox_responses") or {}
    if not isinstance(raw_checkboxes, dict):
        raise ValidationError(
            "checkbox_responses must be an object mapping checkbox id to boolean."
        )
    accepted_at = (body.get("accepted_at") or "").strip() or utc_now_iso()
    separate_rights_asserted = body.get("separate_rights_asserted", False)  # bool checked below
    source_of_rights_note = str(body.get("source_of_rights_note") or "")

    draft = prepare_acceptance(entry, use_context=use_context)
    # Normalise against the DRAFT's required checkboxes, not a single
    # global constant. Different ack_text_kind values render different
    # checkbox sets (canonical 4-box, BYOM 1-box, permissive-attribution
    # 1-box); the old normalisation here was hardcoded to the canonical
    # set and silently dropped checkboxes for the other kinds, so the
    # POST always failed missing-required-checkbox validation for any
    # non-canonical dialog.
    checkbox_responses = {
        cb_id: raw_checkboxes.get(cb_id, False) for cb_id, _ in draft.required_checkboxes
    }
    try:
        row = confirm_acceptance(
            draft,
            checkbox_responses=checkbox_responses,
            accepted_at=accepted_at,
            separate_rights_asserted=separate_rights_asserted,
            source_of_rights_note=source_of_rights_note,
        )
    except AcceptanceError as exc:
        # AcceptanceError surfaces user-input validation failures
        # (missing checkbox, unchecked required box, empty
        # timestamp, missing permalink). Use ValidationError so the
        # response carries http_status=400 — these are client
        # errors, not server bugs, and shouldn't trip
        # 5xx-severity monitoring.
        raise ValidationError(str(exc)) from exc
    # Re-arm gates / once-per-process skip notices so the new
    # acceptance is picked up immediately.
    from bpp.web.bp_models import reset_runtime_for_legal_id

    reset_runtime_for_legal_id(row.model_id)
    log.info(
        "Acceptance recorded: model=%s use_context=%s separate_rights_asserted=%s",
        row.model_id,
        row.use_context_at_acceptance,
        row.separate_rights_asserted,
    )
    return jsonify({"acceptance": asdict(row)}), 200


@bp.get("/api/v1/model-registry/use-context")
@requires_local_app
def api_use_context_get() -> tuple[Any, int]:
    """Return the user's current declared use context + the audit
    history. Read-only. Settings → "Use context" panel and the
    first-launch gate both call this."""
    record = read_record()
    return (
        jsonify(
            {
                "use_context": record.use_context.value,
                "set_at": record.set_at,
                "set_via": record.set_via,
                "history": list(record.history),
            }
        ),
        200,
    )


@bp.post("/api/v1/model-registry/use-context")
@requires_local_app
def api_use_context_set() -> tuple[Any, int]:
    """Persist a use-context declaration.

    Body: ``{"use_context": "...", "set_via": "settings"|"first-launch-gate"}``.
    ``set_via`` defaults to ``"settings"`` because the GUI Settings
    panel is the most common caller; the first-launch gate flow
    sends ``"first-launch-gate"`` explicitly.
    """
    body = request.get_json(silent=True) or {}
    raw = (body.get("use_context") or "").strip()
    if not raw:
        raise ValidationError("use_context is required")
    try:
        ctx = UseContext(raw)
    except ValueError as exc:
        raise ValidationError(
            f"Unknown use_context value: {raw!r}. Expected one of {[c.value for c in UseContext]}."
        ) from exc
    set_via = str(body.get("set_via") or "settings").strip()
    record = set_use_context(ctx, set_via=set_via)
    log.info(
        "Use context updated: %s via %s",
        record.use_context.value,
        record.set_via,
    )
    return (
        jsonify(
            {
                "use_context": record.use_context.value,
                "set_at": record.set_at,
                "set_via": record.set_via,
            }
        ),
        200,
    )


@bp.get("/api/v1/model-registry/acceptance/list")
@requires_local_app
def api_acceptance_list() -> tuple[Any, int]:
    """Read-only view of the acceptance log. Settings → "View your
    acceptances" panel consumes this. No filtering — the list is
    typically short (a handful of restricted-model acceptances per
    user, lifetime) so client-side rendering is fine.

    Each row carries ``current`` — True when it is an acceptance of the
    entry's CURRENT acknowledgment text (the same hash check the load
    gate uses) — and ``needs_reaccept`` — True for an acceptance of an
    older text (e.g. after a disclaimer-version bump). The picker reads
    these off each model's latest row so a stale acceptance shows as
    "re-accept needed" instead of "accepted" while the gate refuses
    the model."""
    from bpp.registry.acceptance_log import snapshot

    # One read of the log: each model's latest row is judged as the load
    # gate sees it (an acceptance followed by a damaged line reads as
    # withdrawn), and ``withdrawn_by_damage`` lets Settings say why.
    snap = snapshot()
    latest_index = {row.model_id: i for i, row in enumerate(snap.rows)}
    entries: dict[str, Any] = {}
    rows: list[dict[str, Any]] = []
    for i, row in enumerate(snap.rows):
        if row.model_id not in entries:
            entries[row.model_id] = _find_any_entry(row.model_id)
        judged = snap.gate_view[row.model_id] if latest_index[row.model_id] == i else row
        rows.append({**asdict(row), **_acceptance_freshness(judged, entries[row.model_id])})
    return jsonify(
        {"acceptances": rows, "withdrawn_by_damage": list(snap.withdrawn_by_damage)}
    ), 200


def _acceptance_freshness(row: Any, entry: Any) -> dict[str, bool]:
    """``current`` / ``needs_reaccept`` flags for one acceptance-log row
    (see :func:`api_acceptance_list`) — the load gate's own judgement
    (:func:`acceptance_state`), so the list can't disagree with it."""
    from bpp.registry.acceptance import acceptance_state

    st = acceptance_state(entry, row=row)
    return {"current": st.current, "needs_reaccept": st.needs_reaccept}


@bp.post("/api/v1/model-registry/acceptance/revoke")
@requires_local_app
def api_acceptance_revoke() -> tuple[Any, int]:
    """Withdraw a prior acceptance for a restricted model.

    Append-only: the original acceptance row is NOT deleted (it stays in
    the legal audit trail). A withdrawal writes a new ``event="revoke"``
    row that supersedes it, so the model re-gates — the server-side load
    policy (:func:`enforce_load_policy_for`) blocks inference until the
    user re-accepts. LOCAL_APP-only, same blast radius as confirm."""
    body = request.get_json(silent=True) or {}
    model_id = (body.get("model_id") or "").strip()
    if not model_id:
        raise ValidationError("model_id is required")
    if _find_any_entry(model_id) is None:
        raise ValidationError(f"Unknown model_id: {model_id!r}")
    row = append_revocation(model_id)
    if row is None:
        # Nothing currently accepted to withdraw — surface as a 400 so the
        # UI can tell the user rather than silently no-op.
        raise ValidationError(f"No active acceptance to withdraw for {model_id!r}.")
    # Take effect now, not after a restart: re-arm memoized license
    # gates + drop cached sessions for this model's files.
    from bpp.web.bp_models import reset_runtime_for_legal_id

    reset_names = reset_runtime_for_legal_id(model_id)
    log.info("Acceptance withdrawn: model=%s (reset: %s)", model_id, reset_names or "none")
    return jsonify({"revocation": asdict(row)}), 200
