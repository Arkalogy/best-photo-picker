"""Model-management endpoints: Bring-Your-Own-Model registration and
model removal (with derived-data purge).

Split out of :mod:`bpp.web.bp_model_registry` for the 500-LOC cap. These
are the write-side admin operations on the registry; the read-side
listing + the acceptance flow stay in ``bp_model_registry``, and the
catalog download endpoints live in ``bp_catalog``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from bpp.errors import ValidationError
from bpp.registry import (
    ModelRemovalError,
    add_byom_entry,
    count_derived_for_model,
    list_byom_entries,
    remove_byom_entry,
    remove_model_with_derived_choice,
)
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app

log = get_logger(__name__)

bp = Blueprint("model_admin", __name__)


@bp.get("/api/v1/model-registry/byom")
@requires_local_app
def api_byom_list() -> tuple[Any, int]:
    """List the user's registered Bring-Your-Own-Model entries.

    Read-only. Returns one dict per entry with the same shape the
    CLI prints — id, display_name, kind, file_path, weight_sha256,
    added_at, ack_text_version, ack_text_sha256.
    """
    payload = [
        {
            "id": e.id,
            "display_name": e.display_name,
            "kind": e.kind,
            "file_path": e.file_path,
            "weight_sha256": e.weight_sha256,
            "added_at": e.added_at,
            "ack_text_version": e.ack_text_version,
            "ack_text_sha256": e.ack_text_sha256,
        }
        for e in list_byom_entries()
    ]
    return jsonify({"byom_entries": payload}), 200


@bp.post("/api/v1/model-registry/byom")
@requires_local_app
def api_byom_add() -> tuple[Any, int]:
    """Register a user-supplied model file.

    Body: ``{"file_path": "/abs/path/to/model.onnx",
            "display_name": "...", "kind": "face_embedder"}``.
    Returns the projected :class:`ModelEntry`-shaped dict so the
    caller can immediately drive the acceptance dialog on it via
    ``/api/v1/model-registry/acceptance/draft?model_id=<id>``.

    The endpoint does NOT walk the acceptance dialog — registration
    and acknowledgment are intentionally two steps so the dialog
    can render the BYOM ack text and the caller can re-snapshot the
    hash at acceptance time.
    """
    body = request.get_json(silent=True) or {}
    file_path = (body.get("file_path") or "").strip()
    if not file_path:
        raise ValidationError("file_path is required")
    display_name = str(body.get("display_name") or "").strip()
    kind = str(body.get("kind") or "face_embedder").strip()
    try:
        entry = add_byom_entry(
            display_name=display_name,
            kind=kind,
            file_path=Path(file_path).expanduser(),
        )
    except FileNotFoundError as exc:
        raise ValidationError(str(exc)) from exc
    log.info(
        "BYOM entry registered: %s (kind=%s, sha256=%s…)",
        entry.id,
        entry.kind,
        entry.weight_sha256[:16],
    )
    return (
        jsonify(
            {
                "id": entry.id,
                "display_name": entry.display_name,
                "kind": entry.kind,
                "file_path": entry.file_path,
                "weight_sha256": entry.weight_sha256,
                "added_at": entry.added_at,
                "ack_text_version": entry.ack_text_version,
                "ack_text_sha256": entry.ack_text_sha256,
            }
        ),
        201,
    )


@bp.delete("/api/v1/model-registry/byom/<entry_id>")
@requires_local_app
def api_byom_remove(entry_id: str) -> tuple[Any, int]:
    """Forget a BYOM entry. Does NOT delete the underlying file
    from disk — BYOM is a pointer abstraction."""
    if not remove_byom_entry(entry_id):
        raise ValidationError(f"No BYOM entry with id {entry_id!r}")
    return jsonify({"removed": entry_id}), 200


@bp.get("/api/v1/model-registry/removal/preview")
@requires_local_app
def api_removal_preview() -> tuple[Any, int]:
    """Return the derived-data counts that the confirmation modal
    will display before the user clicks through.

    Query string: ``model_id=<id>``. Pure read; nothing is deleted.
    """
    from bpp.web.state import get_ctx

    model_id = request.args.get("model_id", "").strip()
    if not model_id:
        raise ValidationError("model_id is required")
    ctx = get_ctx()
    conn = ctx.get_conn()
    summary = count_derived_for_model(model_id, conn)
    return (
        jsonify(
            {
                "model_id": model_id,
                "embeddings": summary.embeddings,
                "distinct_clusters": summary.distinct_clusters,
                "distinct_photos": summary.distinct_photos,
            }
        ),
        200,
    )


@bp.post("/api/v1/model-registry/removal")
@requires_local_app
def api_remove_model() -> tuple[Any, int]:
    """Remove a model entry and optionally purge its derived data.

    Body: ``{"model_id": "...", "purge_derived": true|false}``.
    Both fields are required — the endpoint fails closed when
    ``purge_derived`` is omitted, matching the CLI's explicit-flag
    requirement (Q8). Default behaviour in the GUI is purge; the
    confirmation modal sends ``purge_derived=true`` unless the user
    explicitly checked the "Keep derived data" opt-out.
    """
    from bpp.web.state import get_ctx

    body = request.get_json(silent=True) or {}
    model_id = (body.get("model_id") or "").strip()
    if not model_id:
        raise ValidationError("model_id is required")
    if "purge_derived" not in body:
        raise ValidationError(
            "purge_derived is required (true or false). The endpoint "
            "fails closed because a silent default that leaves "
            "biometric data behind would undercut the privacy posture."
        )
    purge_derived = bool(body.get("purge_derived"))
    ctx = get_ctx()
    conn = ctx.get_conn()
    try:
        result = remove_model_with_derived_choice(model_id, purge_derived=purge_derived, conn=conn)
    except ModelRemovalError as exc:
        conn.rollback()
        raise ValidationError(str(exc)) from exc
    conn.commit()
    log.info(
        "Model removed via API: id=%s kind=%s purged=%s",
        result.model_id,
        result.entry_kind,
        result.purged,
    )
    return (
        jsonify(
            {
                "model_id": result.model_id,
                "entry_kind": result.entry_kind,
                "embeddings_purged": (result.derived_summary.embeddings if result.purged else 0),
                "distinct_clusters_affected": (result.derived_summary.distinct_clusters),
                "distinct_photos_affected": (result.derived_summary.distinct_photos),
                "purged": result.purged,
            }
        ),
        200,
    )
