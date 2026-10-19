"""Recompute + optimize endpoints: re-run selection / weight-tuning.

Extracted from bp_photos.py during the v0.1 cleanup. Both endpoints
drive scoring re-runs over the analyzed photo set:

* ``/api/v1/recompute`` — re-run global selection with optional
  weight, k, seed, face-filter, force-include/exclude, and
  date-range overrides. Persists the selection on All Photos.
* ``/api/v1/optimize`` — weight-tuning optimizer that suggests
  scoring weights maximizing coverage of selected_faces at a given k.

Both share the same RecomputeOptions / RECOMPUTE_* surface from
``bpp.web.recompute`` (the engine), so co-locating them keeps the
re-run concern in one file.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, jsonify, request

from bpp.config import cfg_get
from bpp.db.albums import (
    ensure_all_photos_album,
    get_album,
    get_album_photos,
    set_album_selection,
    update_album,
)
from bpp.db.clip import compute_adaptive_threshold
from bpp.errors import NotFoundError, PayloadTooLargeError, ValidationError
from bpp.utils.logging import get_logger
from bpp.web.recompute import (
    RECOMPUTE_FULL_PAYLOAD_LIMIT,
    RECOMPUTE_WEIGHT_KEYS,
    RecomputeOptions,
    optimize,
    recompute,
)
from bpp.web.share import requires_local_app
from bpp.web.state import clamp_k, get_ctx
from bpp.web.state_helpers import clamp_override

log = get_logger(__name__)

bp = Blueprint("recompute", __name__)


@bp.post("/api/v1/recompute")
@requires_local_app
def api_recompute() -> tuple[Response, int]:
    """Re-run global selection over all analyzed photos with optional
    weight, k, seed, face filter, force-include/exclude, and date-range
    overrides. Persists the selection on All Photos. ``delta: true``
    returns only paths and per-photo scores.

    Logs total wall time and returns it as ``stats.elapsed_ms`` so the
    UI can surface the duration — the path involves dedup, CLIP work,
    and DB writes and can take seconds on a large library.
    """
    import time as _time

    _t0 = _time.perf_counter()
    ctx = get_ctx()
    analysis = ctx.load_analysis_if_needed()
    if analysis is None:
        raise NotFoundError("No analysis data")

    params = request.get_json(silent=True) or {}
    cfg = _config_with_overrides(ctx.config, params)

    k = clamp_k(params.get("k", cfg_get(cfg, "default_selection_k")))
    seed = int(params.get("seed", cfg_get(cfg, "default_selection_seed")))
    selected_faces = params.get("selected_faces", [])

    conn = ctx.get_conn()
    album_id = ensure_all_photos_album(conn)
    album_photos = get_album_photos(conn, album_id)
    force_include = [p["filepath"] for p in album_photos if p["override"] == "include"]
    force_exclude = [p["filepath"] for p in album_photos if p["override"] == "exclude"]

    force_include.extend(params.get("force_include", []))
    force_exclude.extend(params.get("force_exclude", []))

    active: list[dict[str, Any]] = []
    deleted: list[dict[str, Any]] = []
    for p in analysis:
        (deleted if p.get("deleted_at") else active).append(p)

    active = _filter_date_range(active, params)

    # refuse oversized non-delta payloads BEFORE running
    # recompute() and the (expensive) CLIP embedding load. Previously
    # the cap fired post-recompute, so a 50k-photo library still paid
    # the full CPU/RAM cost just to be 413'd. The full-payload path
    # has no path to "send some photos but not all" — clients above
    # the cap must reissue with `delta: true`.
    if not params.get("delta"):
        _refuse_full_payload_over_cap(len(active) + len(deleted))

    face_cluster_map = None
    if selected_faces:
        face_cluster_map = ctx.get_face_cluster_map()

    clip_embs = ctx.load_clip_embeddings() or None
    clip_threshold = None
    threshold_info: dict[str, Any] = {}
    if clip_embs:
        conn2 = ctx.get_conn()
        clip_threshold, threshold_info = compute_adaptive_threshold(
            conn2, default=cfg_get(cfg, "clip_similarity_threshold")
        )

    result = recompute(
        RecomputeOptions(
            analysis=active,
            config=cfg,
            k=k,
            seed=seed,
            force_include=force_include,
            force_exclude=force_exclude,
            selected_faces=selected_faces,
            face_cluster_map=face_cluster_map,
            skip_dedupe=not ctx.analysis_store.phash_ready.is_set(),
            clip_embeddings=clip_embs if clip_embs else None,
            clip_threshold=clip_threshold,
        )
    )

    selected_paths = set(result["selected_paths"])  # ensure O(1) membership test

    # Persist selection state to DB so smart album views can read it
    selected_ids = {p["id"] for p in active if p["filepath"] in selected_paths}
    set_album_selection(conn, album_id, selected_ids)

    stats = result["stats"]
    if threshold_info:
        stats["clip_threshold"] = clip_threshold
        stats["clip_threshold_info"] = threshold_info

    elapsed_ms = int((_time.perf_counter() - _t0) * 1000)
    stats["elapsed_ms"] = elapsed_ms
    log.info(
        "Recompute completed in %dms (k=%d, %d active photos, dedup=%s)",
        elapsed_ms,
        k,
        len(active),
        stats.get("dedup_mode", "?"),
    )

    # Delta mode: return only selection + scores (no full photo metadata)
    if params.get("delta"):
        scores = {item["filepath"]: item.get("aggregate_score", 0) for item in result["photos"]}
        return jsonify(
            {
                "selected_paths": list(selected_paths),
                "scores": scores,
                "stats": stats,
            }
        ), 200

    photos = [
        ctx.build_photo_dict(item, selected=item["filepath"] in selected_paths)
        for item in result["photos"]
    ]
    photos.extend(ctx.build_photo_dict(item) for item in deleted)

    return jsonify(
        {
            "photos": photos,
            "selected_paths": list(selected_paths),
            "stats": stats,
        }
    ), 200


def _config_with_overrides(config: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """A copy of the config with the request's weight overrides (clamped
    to the schema) and the sensitive-photo policy applied."""
    cfg = dict(config)
    for key in RECOMPUTE_WEIGHT_KEYS:
        if key in params:
            cfg[key] = clamp_override(key, params[key])
    # Sensitive-photo policy is a string enum, not a clampable weight.
    if params.get("sensitive_in_picks") in ("allow", "exclude"):
        cfg["sensitive_in_picks"] = params["sensitive_in_picks"]
    return cfg


def _filter_date_range(
    active: list[dict[str, Any]], params: dict[str, Any]
) -> list[dict[str, Any]]:
    """Optional date-range filter (used by the calendar "Pick best" flow)."""
    start_date = params.get("start_date")
    end_date = params.get("end_date")
    if not (start_date and end_date):
        return active
    import datetime as _dt

    try:
        _dt.date.fromisoformat(start_date)
        _dt.date.fromisoformat(end_date)
    except (ValueError, TypeError) as e:
        raise ValidationError(
            "Invalid date format, use YYYY-MM-DD",
            start_date=start_date,
            end_date=end_date,
        ) from e
    if start_date > end_date:
        raise ValidationError(
            "start_date must be <= end_date",
            start_date=start_date,
            end_date=end_date,
        )
    before = len(active)
    active = [p for p in active if start_date <= (p.get("date") or "")[:10] <= end_date]
    log.info("Date range %s to %s: %d -> %d photos", start_date, end_date, before, len(active))
    return active


class _DeltaRequiredError(PayloadTooLargeError):
    """The recompute cap's 413: re-issue with ``{"delta": true}``."""

    code = "delta_required"


def _refuse_full_payload_over_cap(
    photo_count: int, *, what: str = "Library", photos_at: str = "/api/v1/photos"
) -> None:
    """A full-payload recompute over the cap is a 413 with
    ``code: delta_required`` (the central handler shapes it)."""
    if photo_count <= RECOMPUTE_FULL_PAYLOAD_LIMIT:
        return
    raise _DeltaRequiredError(
        "Full-payload recompute over the cap",
        user_message=(
            f"{what} has {photo_count} photos; full-payload "
            f"recompute is capped at {RECOMPUTE_FULL_PAYLOAD_LIMIT}. "
            f'Re-issue with {{"delta": true}} and load photos via {photos_at}.'
        ),
        photo_count=photo_count,
        limit=RECOMPUTE_FULL_PAYLOAD_LIMIT,
    )


@bp.post("/api/v1/optimize")
@requires_local_app
def api_optimize() -> tuple[Response, int]:
    """Run the weight-tuning optimizer to suggest scoring weights that
    maximize coverage of selected_faces (if any) at a given k. Returns
    suggested weights and stats without persisting anything."""
    ctx = get_ctx()
    analysis = ctx.load_analysis_if_needed()
    if analysis is None:
        raise NotFoundError("No analysis data")

    params = request.get_json(silent=True) or {}
    selected_faces = params.get("selected_faces", [])
    cfg = dict(ctx.config)
    k = clamp_k(params.get("k", cfg_get(cfg, "default_selection_k")))

    face_cluster_map = None
    face_filepaths = None
    if selected_faces:
        face_cluster_map = ctx.get_face_cluster_map()
        selected_set = set(selected_faces)
        face_filepaths = {
            fp for fp, clusters in face_cluster_map.items() if selected_set & set(clusters)
        }

    result = optimize(
        RecomputeOptions(
            analysis=analysis,
            config=cfg,
            k=k,
            skip_dedupe=not ctx.analysis_store.phash_ready.is_set(),
            selected_faces=selected_faces or [],
            face_cluster_map=face_cluster_map,
        ),
        face_filepaths=face_filepaths,
    )

    return jsonify(result), 200


# Per-album recompute. Same shape as /api/v1/recompute but scoped
# to one album's photos + per-album config overrides. Extracted
# from bp_albums during the v0.1 cleanup — co-locating the two
# recompute paths keeps the RecomputeOptions / RECOMPUTE_* surface
# referenced from a single module.
@bp.post("/api/v1/albums/<int:album_id>/recompute")
@requires_local_app
def api_album_recompute(album_id: int) -> tuple[Response, int]:
    """Re-run selection for an album with optional weight/k overrides
    from the JSON body, then persist the resulting selection and
    config. Pass ``delta: true`` to receive only ``selected_paths`` and
    per-photo scores instead of the full photo dicts."""
    ctx = get_ctx()
    conn = ctx.get_conn()
    album = get_album(conn, album_id)
    if not album:
        raise NotFoundError("Album not found", album_id=album_id)

    photos_data = get_album_photos(conn, album_id, include_deleted=True)
    if not photos_data:
        raise NotFoundError("No photos in album", album_id=album_id)

    params = request.get_json(silent=True) or {}

    cfg = dict(ctx.config)
    if album.get("config"):
        cfg.update(album["config"])
    # (The sensitive-photo policy is persisted with the album config via
    # update_album() below.)
    cfg = _config_with_overrides(cfg, params)

    k = clamp_k(params.get("k", album.get("k", 50)))

    active = [p for p in photos_data if not p.get("deleted_at")]
    deleted = [p for p in photos_data if p.get("deleted_at")]
    force_include = [p["filepath"] for p in active if p.get("override") == "include"]
    force_exclude = [p["filepath"] for p in active if p.get("override") == "exclude"]

    # refuse oversized non-delta payloads BEFORE running
    # recompute() and the (expensive) CLIP embedding load. The
    # full-payload path has no path to "send some photos but not
    # all" — clients above the cap must reissue with `delta: true`
    # and load photos via /api/v1/albums/<id>/photos.
    if not params.get("delta"):
        _refuse_full_payload_over_cap(
            len(active) + len(deleted), what="Album", photos_at="/api/v1/albums/<id>/photos"
        )

    clip_embs = ctx.load_clip_embeddings() or None
    clip_threshold = None
    if clip_embs:
        conn_t = ctx.get_conn()
        clip_threshold, _ = compute_adaptive_threshold(
            conn_t,
            default=cfg_get(cfg, "clip_similarity_threshold"),
            album_id=album_id,
        )

    result = recompute(
        RecomputeOptions(
            analysis=active,
            config=cfg,
            k=k,
            force_include=force_include,
            force_exclude=force_exclude,
            skip_dedupe=not ctx.analysis_store.phash_ready.is_set(),
            clip_embeddings=clip_embs if clip_embs else None,
            clip_threshold=clip_threshold,
        )
    )

    selected_paths = result["selected_paths"]
    selected_ids = {p["id"] for p in active if p["filepath"] in selected_paths}
    set_album_selection(conn, album_id, selected_ids)
    update_album(conn, album_id, config=cfg, k=k)

    # Delta mode: return only selection + scores (no full photo metadata)
    if params.get("delta"):
        scores = {item["filepath"]: item.get("aggregate_score", 0) for item in result["photos"]}
        return jsonify(
            {
                "selected_paths": list(selected_paths),
                "scores": scores,
                "stats": result["stats"],
            }
        ), 200

    photos = [
        ctx.build_photo_dict(item, selected=item["filepath"] in selected_paths)
        for item in result["photos"]
    ]
    photos.extend(ctx.build_photo_dict(item) for item in deleted)

    return jsonify(
        {
            "photos": photos,
            "selected_paths": list(selected_paths),
            "stats": result["stats"],
        }
    ), 200
