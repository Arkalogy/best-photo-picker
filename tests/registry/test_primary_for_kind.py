"""Which entry the runtime uses for a kind, as the picker shows it.

The content filter, the pet detector and object removal run although
none is marked its kind's default: their loaders select them by id.
The picker used to read them as "Backup — only as auto-fallback" even
when accepted, on disk and running. The entries payload now carries
``primary_for_kind``: the kind's default, or, for a kind without one,
the entry its loader selects by id.
"""

from __future__ import annotations

import dataclasses

from bpp.registry.builtins import NUDENET_320N_ENTRY
from bpp.registry.model_registry import iter_entries, register_entry
from bpp.web.catalog_loaders import loader_selected_ids


def _entries(client) -> dict[str, dict]:
    groups = client.get("/api/v1/model-registry/entries").get_json()["groups"]
    return {e["id"]: e for g in groups for e in g["entries"]}


def test_every_kind_without_a_default_has_exactly_one_loader_selected_entry():
    """A new kind whose loader picks by id must be listed in
    ``loader_selected_ids`` or its row regresses to "Backup"."""
    entries = list(iter_entries())
    kinds_with_default = {e.kind for e in entries if e.default_for_kind}
    for kind in {e.kind for e in entries} - kinds_with_default:
        selected = [e.id for e in entries if e.kind == kind and e.id in loader_selected_ids()]
        assert len(selected) == 1, f"kind {kind!r}: loader-selected entries {selected}"


def test_loader_selected_models_are_primary(registry_client):
    by_id = _entries(registry_client)
    for model_id in ("nudenet_320n", "ultralytics_yolov11n_pets", "lama_inpaint_research"):
        assert by_id[model_id]["default_for_kind"] is False, model_id
        assert by_id[model_id]["primary_for_kind"] is True, model_id


def test_defaults_are_primary_and_their_alternatives_are_not(registry_client):
    by_id = _entries(registry_client)
    assert by_id["sface_yunet"]["primary_for_kind"] is True
    assert by_id["dlib_face_recognition_resnet_v1"]["primary_for_kind"] is False
    assert by_id["insightface_buffalo_s"]["primary_for_kind"] is False, (
        "picked by setting, not primary"
    )


def test_a_second_model_of_a_loader_kind_does_not_demote_the_one_that_runs(registry_client):
    register_entry(dataclasses.replace(NUDENET_320N_ENTRY, id="other_content_filter"))
    by_id = _entries(registry_client)
    assert by_id["nudenet_320n"]["primary_for_kind"] is True
    assert by_id["other_content_filter"]["primary_for_kind"] is False
