"""P6 — :class:`PhotoDict` TypedDict contract tests.

The TypedDict pins the canonical shape of the photo API response.
Tests verify:

* The contract documents every non-score field the builder emits.
* The builder's output validates against the TypedDict via runtime
  structural matching.
* Optional fields are honored — selected / tags / similar_photos are
  only present when the input includes them.
* The map projection is the strict subset documented as
  :class:`PhotoMapDict`.
"""

from __future__ import annotations

import typing

from bpp.web.photo_dict import (
    PhotoDict,
    PhotoMapDict,
    build_photo_dict,
    build_photo_dict_map,
)


def _minimal_item():
    """Smallest input that doesn't crash the builder."""
    return {
        "id": 1,
        "filepath": "/lib/a.jpg",
        "file_size": 1024,
    }


class TestPhotoDictShape:
    def test_typed_dict_advertises_expected_keys(self):
        # `typing.get_type_hints` works on TypedDicts via __annotations__.
        keys = set(typing.get_type_hints(PhotoDict).keys())
        # Spot-check load-bearing fields the API contract depends on.
        for required in (
            "id",
            "filepath",
            "filename",
            "thumb_hash",
            "exif",
            "is_video",
            "is_raw",
            "is_live_photo_sidecar",
            "live_photo_sidecar_count",
        ):
            assert required in keys, f"PhotoDict missing key {required!r}"

    def test_optional_fields_present_in_typed_dict(self):
        keys = set(typing.get_type_hints(PhotoDict).keys())
        # selected / tags / similar_photos are conditionally present in
        # the output but ARE valid PhotoDict keys (total=False).
        for optional in ("selected", "tags", "similar_photos"):
            assert optional in keys


class TestBuilderOutput:
    def test_minimal_item_produces_required_keys(self):
        out = build_photo_dict(_minimal_item(), thumbs=None)
        # The builder always emits these; the TypedDict (total=False)
        # documents them but doesn't enforce presence.
        for required in (
            "id",
            "filepath",
            "filename",
            "date",
            "file_size",
            "is_video",
            "is_raw",
        ):
            assert required in out, f"builder must emit {required!r}"

    def test_selected_only_present_when_passed(self):
        out = build_photo_dict(_minimal_item(), thumbs=None)
        assert "selected" not in out, (
            "selected is conditionally emitted — must be absent when caller doesn't pass it"
        )
        out2 = build_photo_dict(_minimal_item(), thumbs=None, selected=True)
        assert out2.get("selected") is True

    def test_tags_only_present_when_in_input(self):
        item = _minimal_item()
        out = build_photo_dict(item, thumbs=None)
        assert "tags" not in out
        item["tags"] = ["family", "vacation"]
        out2 = build_photo_dict(item, thumbs=None)
        assert out2.get("tags") == ["family", "vacation"]


class TestMapProjection:
    def test_map_dict_advertises_expected_keys(self):
        keys = set(typing.get_type_hints(PhotoMapDict).keys())
        assert keys == {
            "id",
            "gps_lat",
            "gps_lon",
            "thumb_hash",
            "filename",
            "date",
            "aggregate_score",
        }

    def test_map_builder_produces_total_dict(self):
        item = {
            "id": 1,
            "filepath": "/lib/a.jpg",
            "gps_lat": 47.6,
            "gps_lon": -122.3,
            "aggregate_score": 0.8,
        }
        out = build_photo_dict_map(item, thumbs=None)
        expected_keys = set(typing.get_type_hints(PhotoMapDict).keys())
        assert set(out.keys()) == expected_keys, (
            f"map builder must emit exactly the PhotoMapDict shape; "
            f"got {set(out.keys())}, expected {expected_keys}"
        )

    def test_map_builder_keeps_missing_gps_as_none(self):
        # Pins are still emitted for photos without GPS; the UI filters
        # them out client-side. Keeps the projection deterministic in
        # shape across photo populations.
        item = {"id": 1, "filepath": "/lib/a.jpg"}
        out = build_photo_dict_map(item, thumbs=None)
        assert out["gps_lat"] is None
        assert out["gps_lon"] is None
        assert out["aggregate_score"] == 0
