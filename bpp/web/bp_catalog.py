"""Catalog-model download endpoints (Settings → Models "Download" /
"Uninstall" for runtime-fetched models).

Split out of :mod:`bpp.web.bp_model_registry` for the 500-LOC cap. This
module owns the catalog-loader map (the single switch that makes an entry
"runtime-fetched") and the two explicit ensure/remove endpoints. The
acceptance/registry/BYOM endpoints stay in ``bp_model_registry``;
:func:`_catalog_loaders` is imported there for the ``is_catalog_entry`` /
``catalog_on_disk`` flags on the picker payload.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from flask import Blueprint, jsonify, request

from bpp.errors import BppError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app

log = get_logger(__name__)

bp = Blueprint("catalog", __name__)


#: Maps catalog entry id → (is-on-disk fn, ensure-weights fn,
#: remove-weights fn). Catalog entries are runtime-fetched models
#: that have no install wiring in the legacy ``ModelRegistry`` (e.g.
#: buffalo_s, whose ZIP-extract flow doesn't fit the stock single-URL
#: download path). The picker uses ``is_on_disk`` to decide whether
#: to surface "Download" or "Use this model" as the next step,
#: ``ensure_weights`` is what the explicit "Download" button
#: triggers, and ``remove_weights`` backs the Uninstall step so the
#: catalog row has the same Review → Download → Use → Uninstall
#: lifecycle as installable entries. Adding a new catalog entry
#: means adding one row here.
def _catalog_loaders() -> dict[
    str,
    tuple[Callable[[], bool], Callable[[], str], Callable[[], int]],
]:
    from bpp.ai.inpainting import (
        ensure_lama_model,
    )
    from bpp.ai.inpainting import (
        is_on_disk as lama_is_on_disk,
    )
    from bpp.ai.inpainting import (
        remove_local_weights as lama_remove,
    )
    from bpp.scoring.face_embed_buffalo_s import (
        ensure_buffalo_s_model,
    )
    from bpp.scoring.face_embed_buffalo_s import (
        is_on_disk as buffalo_s_is_on_disk,
    )
    from bpp.scoring.face_embed_buffalo_s import (
        remove_local_weights as buffalo_s_remove,
    )
    from bpp.scoring.nudity import (
        ensure_nudenet_model,
    )
    from bpp.scoring.nudity import (
        is_on_disk as nudenet_is_on_disk,
    )
    from bpp.scoring.nudity import (
        remove_local_weights as nudenet_remove,
    )

    return {
        "insightface_buffalo_s": (
            buffalo_s_is_on_disk,
            ensure_buffalo_s_model,
            buffalo_s_remove,
        ),
        "nudenet_320n": (
            nudenet_is_on_disk,
            ensure_nudenet_model,
            nudenet_remove,
        ),
        "lama_inpaint_research": (
            lama_is_on_disk,
            ensure_lama_model,
            lama_remove,
        ),
    }


@bp.post("/api/v1/face-embedders/ensure-weights")
@requires_local_app
def api_ensure_face_embedder_weights() -> tuple[Any, int]:
    """Force a catalog entry's weights to be downloaded NOW, before
    the user activates the model.

    Body: ``{"registry_id": "<id>"}``.

    Catalog entries (currently only ``insightface_buffalo_s``) are
    runtime-fetched — they don't have install wiring in the legacy
    ``ModelRegistry`` and so the redownload endpoint can't find them.
    Without this endpoint, the picker has no way to honour the
    "Download before Use" lifecycle for them: clicking "Use this
    model" would flip the setting and silently trigger a ~121 MB
    fetch at first analyze. That violates the project's "nothing
    should be silent" rule and gives the user no way to opt out of
    the network call after the fact.

    Synchronous — blocks until the fetch + SHA-verifying extract
    completes. The download itself routes through the canonical
    ``bpp.utils.download.download_file`` gate (registry_id required,
    policy gate fires pre-network), so a restricted entry that has
    not been accepted is refused here too — the explicit "Download"
    button cannot bypass acceptance.

    Returns ``{ok: true, size_bytes: int}`` on success. On a policy
    refusal raises ``ValidationError`` (400) carrying the
    ModelLoadBlockedError reason.
    """
    body = request.get_json(silent=True) or {}
    registry_id = (body.get("registry_id") or "").strip()
    if not registry_id:
        raise ValidationError("registry_id is required")
    loaders = _catalog_loaders()
    if registry_id not in loaders:
        raise ValidationError(
            f"No catalog loader for {registry_id!r}. Only runtime-"
            "fetched catalog entries are supported by this endpoint; "
            "regular installable models go through /api/v1/models/"
            "redownload."
        )
    _, ensure_fn, _ = loaders[registry_id]

    try:
        path = ensure_fn()
    except Exception as exc:
        # Surface the underlying reason — could be a ModelLoadBlockedError
        # (policy refusal), a ModelIntegrityError (SHA mismatch on the
        # zip or extracted file), or a network OSError. All three need
        # to reach the picker so the toast says what actually went wrong.
        raise BppError(
            "Failed to download catalog model weights",
            user_message=f"Couldn't download {registry_id}: {exc!s}",
            diagnostic_message=(f"ensure-weights failed for {registry_id}: {exc!s}"),
            registry_id=registry_id,
        ) from exc
    log.info(
        "Catalog weights ensured: id=%s path=%s",
        registry_id,
        path,
    )
    return (
        jsonify({"ok": True, "size_bytes": os.path.getsize(path)}),
        200,
    )


@bp.post("/api/v1/face-embedders/uninstall-weights")
@requires_local_app
def api_uninstall_face_embedder_weights() -> tuple[Any, int]:
    """Delete a catalog entry's locally cached weights.

    Body: ``{"registry_id": "<id>"}``.

    Symmetric counterpart to ``/ensure-weights`` for the Uninstall
    step in the picker menu. Catalog entries don't appear in the
    legacy ``ModelRegistry``, so the regular ``/models/uninstall``
    path can't find them; this endpoint dispatches to the
    per-entry ``remove_weights`` function registered in
    :func:`_catalog_loaders`.

    Returns ``{ok: true, bytes_freed: int}``. Idempotent: removing
    weights that are not on disk returns ``bytes_freed=0`` rather
    than an error.
    """
    body = request.get_json(silent=True) or {}
    registry_id = (body.get("registry_id") or "").strip()
    if not registry_id:
        raise ValidationError("registry_id is required")
    loaders = _catalog_loaders()
    if registry_id not in loaders:
        raise ValidationError(
            f"No catalog loader for {registry_id!r}. Only runtime-"
            "fetched catalog entries are supported by this endpoint."
        )
    _, _, remove_fn = loaders[registry_id]
    try:
        bytes_freed = remove_fn()
    except Exception as exc:
        raise BppError(
            "Failed to uninstall catalog model weights",
            user_message=f"Couldn't uninstall {registry_id}: {exc!s}",
            diagnostic_message=(f"uninstall-weights failed for {registry_id}: {exc!s}"),
            registry_id=registry_id,
        ) from exc
    log.info(
        "Catalog weights removed: id=%s bytes_freed=%d",
        registry_id,
        bytes_freed,
    )
    return jsonify({"ok": True, "bytes_freed": bytes_freed}), 200
