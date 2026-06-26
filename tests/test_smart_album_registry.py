"""Ensure _SMART_ALBUM_TYPES registry is complete and consistent.

Every entry must have both a refresh_fn (or None) and a get_ids_fn.
The registry is the single source of truth — refresh_smart_albums()
and get_smart_album_photo_ids() both dispatch from it.
"""

from __future__ import annotations

from bpp.db.smart_albums import _SMART_ALBUM_TYPES


def test_registry_has_all_expected_types():
    """Known smart album types must be present in the registry."""
    expected = {
        "all",
        "smart_time",
        "smart_score",
        "smart_unsorted",
        "smart_recent",
        "smart_hidden",
        "smart_person",
        "smart_pet",
        "smart_group",
        "smart_video",
        "smart_screenshot",
        "smart_duplicates",
        "smart_no_faces",
        "smart_document",
        "smart_deleted",
        "smart_edited",
        "smart_enhanced",
        "smart_tag",
    }
    actual = set(_SMART_ALBUM_TYPES.keys())
    missing = expected - actual
    assert not missing, f"Missing from _SMART_ALBUM_TYPES: {missing}"


def test_registry_entries_are_valid_tuples():
    """Every entry must be a (refresh_fn_or_None, get_ids_fn) tuple."""
    for album_type, entry in _SMART_ALBUM_TYPES.items():
        assert isinstance(entry, tuple) and len(entry) == 2, (
            f"{album_type}: expected (refresh_fn, get_ids_fn) tuple, got {type(entry)}"
        )
        refresh_fn, get_ids_fn = entry
        assert refresh_fn is None or callable(refresh_fn), (
            f"{album_type}: refresh_fn must be callable or None"
        )
        assert callable(get_ids_fn), f"{album_type}: get_ids_fn must be callable"


def test_registry_get_ids_fns_are_unique():
    """No two album types should share the same get_ids function."""
    seen: dict[int, str] = {}
    for album_type, (_, get_ids_fn) in _SMART_ALBUM_TYPES.items():
        fn_id = id(get_ids_fn)
        if fn_id in seen:
            other = seen[fn_id]
            raise AssertionError(
                f"{album_type} and {other} share the same get_ids_fn — "
                "each album type should have its own handler"
            )
        seen[fn_id] = album_type


# ─── Plugin registration API ────────────────────────────────────────


import pytest  # noqa: E402

from bpp.db.smart_albums import SmartAlbumRegistry  # noqa: E402


@pytest.fixture(autouse=False)
def _isolate_registry():
    """Roll back to built-ins after each test that mutates."""
    yield
    SmartAlbumRegistry._reset_for_tests()


class TestSmartAlbumRegistryAPI:
    """Pin the contract for plugin registration."""

    def test_register_new_kind(self, _isolate_registry):
        def my_refresh(conn):
            pass

        def my_get_ids(conn, rule):
            return []

        SmartAlbumRegistry.register("smart_my_kind", my_refresh, my_get_ids)
        assert SmartAlbumRegistry.get("smart_my_kind") == (my_refresh, my_get_ids)

    def test_register_collision_raises(self, _isolate_registry):
        SmartAlbumRegistry.register("smart_x", None, lambda c, r: [])
        with pytest.raises(ValueError, match="already registered"):
            SmartAlbumRegistry.register("smart_x", None, lambda c, r: [1])

    def test_register_replace_overrides(self, _isolate_registry):
        def original(c, r):
            return ["original"]

        def replacement(c, r):
            return ["replacement"]

        SmartAlbumRegistry.register("smart_x", None, original)
        SmartAlbumRegistry.register("smart_x", None, replacement, replace=True)
        _, get_ids = SmartAlbumRegistry.get("smart_x")
        assert get_ids(None, {}) == ["replacement"]

    def test_register_idempotent_for_same_callable(self, _isolate_registry):
        # Re-registering with the *same* callables is fine (no-op)
        def fn(c, r):
            return []

        SmartAlbumRegistry.register("smart_y", None, fn)
        SmartAlbumRegistry.register("smart_y", None, fn)  # no error

    def test_get_ids_dispatches_through_registry(self, _isolate_registry):
        """End-to-end: a registered plugin's get_ids_fn is reached by
        get_smart_album_photo_ids when an album of that type is queried."""
        from bpp.db.smart_albums import get_smart_album_photo_ids

        sentinel = [42, 43]

        def my_get_ids(conn, rule):
            return sentinel

        SmartAlbumRegistry.register("smart_plugin_demo", None, my_get_ids)

        result = get_smart_album_photo_ids(
            None,  # conn unused by our test handler
            {"album_type": "smart_plugin_demo", "rule": {}},
        )
        assert result is sentinel

    def test_reset_drops_plugins_keeps_builtins(self, _isolate_registry):
        SmartAlbumRegistry.register("smart_plugin", None, lambda c, r: [])
        assert SmartAlbumRegistry.get("smart_plugin") is not None
        SmartAlbumRegistry._reset_for_tests()
        assert SmartAlbumRegistry.get("smart_plugin") is None
        # Built-ins survive
        assert SmartAlbumRegistry.get("smart_video") is not None


class TestSearchabilityAndResultBucket:
    """Registry flags that replaced bp_search's hardcoded _SKIP_TYPES set
    and `if atype == "smart_person"` routing (Review 2026-06-17)."""

    def test_internal_albums_not_searchable(self):
        from bpp.db.smart_albums import SmartAlbumRegistry

        for t in ("all", "smart_deleted", "smart_hidden"):
            assert SmartAlbumRegistry.is_searchable(t) is False, t

    def test_normal_albums_searchable_by_default(self):
        from bpp.db.smart_albums import SmartAlbumRegistry

        for t in ("smart_person", "smart_pet", "smart_video", "smart_tag"):
            assert SmartAlbumRegistry.is_searchable(t) is True, t
        # Unregistered/unknown types default to searchable (True).
        assert SmartAlbumRegistry.is_searchable("smart_unknown_plugin") is True

    def test_smart_person_routes_to_people_bucket(self):
        from bpp.db.smart_albums import SmartAlbumRegistry

        assert SmartAlbumRegistry.get_result_bucket("smart_person") == "people"

    def test_default_result_bucket_is_album(self):
        from bpp.db.smart_albums import SmartAlbumRegistry

        for t in ("smart_pet", "smart_video", "smart_unknown_plugin"):
            assert SmartAlbumRegistry.get_result_bucket(t) == "album", t

    def test_plugin_can_opt_out_of_search_and_declare_bucket(self):
        """A plugin registering with searchable=False / result_bucket=... is
        honored — the whole point of moving these off the hardcoded set."""
        from bpp.db.smart_albums import SmartAlbumRegistry

        try:
            SmartAlbumRegistry.register(
                "smart_plugin_hidden", None, lambda c, r: [], searchable=False
            )
            SmartAlbumRegistry.register(
                "smart_plugin_team", None, lambda c, r: [], result_bucket="people"
            )
            assert SmartAlbumRegistry.is_searchable("smart_plugin_hidden") is False
            assert SmartAlbumRegistry.get_result_bucket("smart_plugin_team") == "people"
        finally:
            SmartAlbumRegistry._reset_for_tests()
