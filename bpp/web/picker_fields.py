"""The Settings → Models picker's row for a registry entry.

Beyond the registry's own fields, the row carries what the picker used to
look up in tables of the built-ins:
the registry's own fields: the feature label the install state is keyed
by, the Settings toggle that switches the model, and, for a face
embedder, the ``face_embedding_method`` value that picks it. Built-ins
come from the table below; plugin entries come from what their plugin
registered (a detector's or scorer's ``toggle_key``, an embedder's
``name``), so the picker needs no table of its own."""

from __future__ import annotations

from typing import Any

from bpp.registry.labels import group_for_entry, ui_label_for_entry
from bpp.utils.logging import get_logger
from bpp.web.catalog_loaders import catalog_loaders, loader_selected_ids

log = get_logger(__name__)

#: registry id → (legacy feature label, Settings toggle key)
_BUILTIN_FEATURES: dict[str, tuple[str | None, str | None]] = {
    "sface_yunet": ("Face recognition", None),
    "dlib_face_recognition_resnet_v1": ("Face recognition", None),
    "insightface_buffalo_s": (None, None),
    "insightface_scrfd_25g": ("SCRFD face detection", "model_scrfd"),
    "opencv_yunet": ("Face detection", None),
    "mediapipe_blazeface_short_range": ("Face detection", None),
    "mediapipe_blazeface_full_range": ("BlazeFace full-range", "model_blazeface_fr"),
    "mediapipe_face_landmarker": ("Expression scoring", "model_face_landmarker"),
    "mediapipe_hand_landmarker": ("Hand suppression", "model_hand_landmarker"),
    "mediapipe_pose_landmarker_lite": ("Pose estimation", "model_pose"),
    "mediapipe_selfie_segmenter": ("Subject segmentation", "model_segmentation"),
    "openai_clip_vit_b32_onnx": ("Smart search & dedup", None),
    "ultralytics_yolov11n_pets": ("Pet detection", None),
    "nudenet_320n": ("Content filter", None),
    "lama_inpaint_research": ("AI object removal", None),
}


def picker_fields(entry: Any) -> dict[str, Any]:
    """``legacy_label``, ``toggle_key`` and ``method_value`` for ``entry``."""
    from bpp.scoring.face_detector_registry import list_detectors
    from bpp.scoring.face_embedder_registry import list_embedders
    from bpp.scoring.registry import iter_scorers

    label, toggle = _BUILTIN_FEATURES.get(entry.id, (None, None))
    builtin = entry.id in _BUILTIN_FEATURES
    if not builtin:
        for det in list_detectors():
            if det.registry_id == entry.id and det.toggle_key:
                toggle = det.toggle_key
                break
    if not builtin and toggle is None:
        for scorer in iter_scorers():
            if getattr(scorer, "registry_id", None) == entry.id and scorer.toggle_key:
                toggle = scorer.toggle_key
                break
    method = None
    if entry.kind == "face_embedder":
        for emb in list_embedders():
            if emb.registry_id == entry.id:
                method = emb.name
                break
    return {"legacy_label": label, "toggle_key": toggle, "method_value": method}


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


def entry_to_picker_dict(
    entry: Any, loaders: dict | None = None, states: dict[str, str] | None = None
) -> dict[str, Any]:
    """Serialise one :class:`ModelEntry` into the picker shape.

    ``loaders`` is the catalog-loader map (``catalog_loaders()``). The
    entries endpoint computes it ONCE and passes it in so this isn't
    rebuilt (with its lazy imports) per row. Callers that serialise a
    single entry can omit it — it's resolved lazily. Resolved per call
    rather than cached at module level so test patches on the loader
    functions still take effect.
    """
    if loaders is None:
        loaders = catalog_loaders()
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
        # What the runtime uses for the kind: its default, or the entry a
        # loader selects by id (kinds that have no default).
        "primary_for_kind": bool(entry.default_for_kind) or entry.id in loader_selected_ids(),
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
        # The feature label the install state is keyed by, the Settings
        # toggle, and (face embedders) the method value that picks the
        # entry: the server says it, so a plugin entry has them too.
        **picker_fields(entry),
    }
