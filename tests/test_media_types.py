"""Tests for the media-type discriminator seam.

The contract is small and load-bearing: every dispatch site that
used to bare-check `is_video` / `is_raw` (or call `is_video_file` /
`is_raw_file` on a path) now goes through `media_kind_from_dict`
or `media_kind_from_path`. These tests pin the mappings so a
future refactor of the storage columns or extension lists can't
silently change the dispatch outcome.
"""

from __future__ import annotations


class TestMediaKindFromDict:
    def test_video_flag_classifies_as_video(self):
        from bpp.media_types import MediaKind, media_kind_from_dict

        assert media_kind_from_dict({"is_video": 1}) is MediaKind.VIDEO
        assert media_kind_from_dict({"is_video": True}) is MediaKind.VIDEO

    def test_raw_flag_classifies_as_raw(self):
        from bpp.media_types import MediaKind, media_kind_from_dict

        assert media_kind_from_dict({"is_raw": 1}) is MediaKind.RAW

    def test_no_flags_classifies_as_photo(self):
        from bpp.media_types import MediaKind, media_kind_from_dict

        assert media_kind_from_dict({}) is MediaKind.PHOTO
        assert media_kind_from_dict({"is_video": 0, "is_raw": 0}) is MediaKind.PHOTO

    def test_video_takes_priority_over_raw(self):
        """Pathological data (both flags set) must dispatch
        deterministically — VIDEO wins. Real data should never have
        both, but the priority makes the dispatch defined."""
        from bpp.media_types import MediaKind, media_kind_from_dict

        assert media_kind_from_dict({"is_video": 1, "is_raw": 1}) is MediaKind.VIDEO


class TestMediaKindFromPath:
    def test_mp4_classifies_as_video(self):
        from bpp.media_types import MediaKind, media_kind_from_path

        assert media_kind_from_path("/x/foo.mp4") is MediaKind.VIDEO
        assert media_kind_from_path("/x/foo.MOV") is MediaKind.VIDEO

    def test_arw_classifies_as_raw(self):
        from bpp.media_types import MediaKind, media_kind_from_path

        # Sony RAW
        assert media_kind_from_path("/x/foo.ARW") is MediaKind.RAW

    def test_jpg_classifies_as_photo(self):
        from bpp.media_types import MediaKind, media_kind_from_path

        assert media_kind_from_path("/x/foo.jpg") is MediaKind.PHOTO
        assert media_kind_from_path("/x/foo.PNG") is MediaKind.PHOTO


class TestMediaKindContract:
    """Pin the enum surface — adding new kinds is fine; removing or
    renaming existing kinds breaks every call site."""

    def test_three_kinds_exist(self):
        from bpp.media_types import MediaKind

        assert MediaKind.PHOTO is not None
        assert MediaKind.RAW is not None
        assert MediaKind.VIDEO is not None

    def test_kinds_are_distinct_values(self):
        from bpp.media_types import MediaKind

        kinds = {MediaKind.PHOTO, MediaKind.RAW, MediaKind.VIDEO}
        assert len(kinds) == 3
