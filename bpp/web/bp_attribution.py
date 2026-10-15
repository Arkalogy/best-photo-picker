"""Combined acceptance of every attribution-licensed model.

  GET  /api/v1/model-registry/acceptance/attribution-bundle
  POST /api/v1/model-registry/acceptance/attribution-bundle/confirm

The pre-analysis dialog shows the GET payload as one prompt and POSTs
the user's answer; :mod:`bpp.registry.attribution_bundle` validates it
and writes one acceptance row per model. Restricted models are refused
here — they keep their own dialog (``/acceptance/draft`` + ``/confirm``).
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from bpp.errors import ValidationError
from bpp.registry.acceptance import AcceptanceError, utc_now_iso
from bpp.registry.attribution_bundle import (
    confirm_attribution_bundle,
    pending_attribution_entries,
    prepare_attribution_bundle,
)
from bpp.registry.labels import plain_english_license_label
from bpp.registry.use_context_store import get_use_context
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app

log = get_logger(__name__)

bp = Blueprint("model_attribution", __name__)


@bp.get("/api/v1/model-registry/acceptance/attribution-bundle")
@requires_local_app
def api_attribution_bundle() -> tuple[Any, int]:
    """The attribution models not yet accepted, plus the notice and the
    checkbox the combined prompt renders. ``models`` is empty when
    there is nothing to accept."""
    bundle = prepare_attribution_bundle(pending_attribution_entries())
    return (
        jsonify(
            {
                "models": [
                    {
                        "id": e.id,
                        "display_name": e.display_name,
                        "kind": e.kind,
                        "license": plain_english_license_label(e.upstream_claimed_license_class),
                        "terms_url": e.terms_url,
                    }
                    for e in bundle.entries
                ],
                "compressed_disclaimer": bundle.compressed_disclaimer,
                "full_disclaimer": bundle.full_disclaimer,
                "biometric_responsibility_text": bundle.biometric_responsibility_text,
                "required_checkboxes": [
                    {"id": cb_id, "text": text} for cb_id, text in bundle.required_checkboxes
                ],
            }
        ),
        200,
    )


@bp.post("/api/v1/model-registry/acceptance/attribution-bundle/confirm")
@requires_local_app
def api_attribution_bundle_confirm() -> tuple[Any, int]:
    """Record an acceptance for every model in ``model_ids``.

    Body: ``{"model_ids": [...], "checkbox_responses": {id: bool}}``.
    All-or-nothing: a restricted id or an unticked box records nothing.
    """
    body = request.get_json(silent=True) or {}
    model_ids = body.get("model_ids")
    if not isinstance(model_ids, list) or not all(isinstance(m, str) for m in model_ids):
        raise ValidationError("model_ids must be a list of model ids.")
    checkboxes = body.get("checkbox_responses") or {}
    if not isinstance(checkboxes, dict):
        raise ValidationError(
            "checkbox_responses must be an object mapping checkbox id to boolean."
        )
    try:
        rows = confirm_attribution_bundle(
            model_ids,
            checkbox_responses=checkboxes,  # true/false checked downstream
            accepted_at=utc_now_iso(),
            use_context=get_use_context(),
        )
    except AcceptanceError as exc:
        raise ValidationError(str(exc)) from exc
    # Re-arm gates and drop cached "unavailable" verdicts so the
    # acceptance takes effect in this process without a restart.
    from bpp.web.bp_models import reset_runtime_for_legal_id

    for row in rows:
        reset_runtime_for_legal_id(row.model_id)
    accepted = [row.model_id for row in rows]
    log.info("Attribution acceptance recorded for %d model(s): %s", len(accepted), accepted)
    return jsonify({"accepted": accepted}), 200
