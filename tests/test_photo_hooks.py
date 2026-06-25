"""Tests for the photo-deletion lifecycle hooks (bpp/db/photo_hooks.py)."""

from __future__ import annotations

import sqlite3

import pytest

from bpp.db.photo_hooks import (
    dispatch_photo_deletion,
    register_photo_deletion_hook,
    unregister_photo_deletion_hook,
)
from bpp.db.photos import (
    permanent_delete_photos,
    restore_photos,
    soft_delete_photos,
    upsert_photo,
)
from bpp.db.schema import create_tables


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    create_tables(c)
    yield c
    c.close()


@pytest.fixture
def photo_ids(conn):
    """Seed 3 photos and return their IDs."""
    ids = []
    for i in range(3):
        upsert_photo(
            conn,
            {
                "filepath": f"/tmp/test_hook_{i}.jpg",
                "file_size": 100,
                "file_mtime": 1700000000.0 + i,
            },
        )
        row = conn.execute(
            "SELECT id FROM photos WHERE filepath = ?",
            (f"/tmp/test_hook_{i}.jpg",),
        ).fetchone()
        ids.append(row["id"])
    conn.commit()
    return ids


@pytest.fixture
def captured():
    """Captures hook invocations. Auto-deregisters at teardown."""
    calls: list[tuple[list[int], str]] = []

    def hook(_conn, photo_ids, kind):
        calls.append((list(photo_ids), kind))

    register_photo_deletion_hook(hook)
    yield calls
    unregister_photo_deletion_hook(hook)


class TestDispatch:
    def test_no_hook_no_dispatch(self, conn, photo_ids):
        # No hook registered → soft_delete still works, no error.
        n = soft_delete_photos(conn, photo_ids)
        assert n == 3

    def test_empty_ids_skipped(self, conn, captured):
        # The dispatcher short-circuits on empty input — wakes nobody.
        dispatch_photo_deletion(conn, [], "soft")
        assert captured == []

    def test_hook_swallows_exceptions(self, conn, photo_ids):
        """A misbehaving plugin must not break the user-facing delete."""

        def bad_hook(_c, _ids, _k):
            raise RuntimeError("plugin bug")

        register_photo_deletion_hook(bad_hook)
        try:
            n = soft_delete_photos(conn, photo_ids)
            assert n == 3, "soft_delete should succeed despite bad hook"
        finally:
            unregister_photo_deletion_hook(bad_hook)


class TestSoftDeleteHook:
    def test_fires_with_soft_kind(self, conn, photo_ids, captured):
        soft_delete_photos(conn, photo_ids)
        assert len(captured) == 1
        ids, kind = captured[0]
        assert sorted(ids) == sorted(photo_ids)
        assert kind == "soft"

    def test_no_fire_when_zero_rows_changed(self, conn, photo_ids, captured):
        """A second soft-delete on already-deleted rows moves zero rows.
        Hooks receive affected IDs only, so no plugin is woken for a no-op."""
        soft_delete_photos(conn, photo_ids)  # first call: fires
        captured.clear()
        assert soft_delete_photos(conn, photo_ids) == 0
        assert captured == []


class TestRestoreHook:
    def test_fires_with_restore_kind(self, conn, photo_ids, captured):
        soft_delete_photos(conn, photo_ids)
        captured.clear()
        restore_photos(conn, photo_ids)
        assert len(captured) == 1
        ids, kind = captured[0]
        assert sorted(ids) == sorted(photo_ids)
        assert kind == "restore"


class TestPermanentDeleteHook:
    def test_fires_with_permanent_kind(self, conn, photo_ids, captured):
        soft_delete_photos(conn, photo_ids)  # must be in trash first
        captured.clear()
        permanent_delete_photos(conn, photo_ids)
        assert len(captured) == 1
        ids, kind = captured[0]
        assert sorted(ids) == sorted(photo_ids)
        assert kind == "permanent"

    def test_no_fire_when_not_in_trash(self, conn, photo_ids, captured):
        """permanent_delete requires deleted_at IS NOT NULL.  If photos
        aren't in trash, the function returns [] and skips dispatch."""
        filepaths = permanent_delete_photos(conn, photo_ids)
        assert filepaths == []
        # No hook fire — nothing was deleted.
        assert captured == []


class TestMultipleHooks:
    def test_all_hooks_called(self, conn, photo_ids):
        calls_a: list[str] = []
        calls_b: list[str] = []

        def hook_a(_c, _ids, kind):
            calls_a.append(kind)

        def hook_b(_c, _ids, kind):
            calls_b.append(kind)

        register_photo_deletion_hook(hook_a)
        register_photo_deletion_hook(hook_b)
        try:
            soft_delete_photos(conn, photo_ids)
            assert calls_a == ["soft"]
            assert calls_b == ["soft"]
        finally:
            unregister_photo_deletion_hook(hook_a)
            unregister_photo_deletion_hook(hook_b)

    def test_one_hook_failing_doesnt_block_others(self, conn, photo_ids):
        ok_calls: list[str] = []

        def bad_hook(_c, _ids, _k):
            raise RuntimeError("nope")

        def good_hook(_c, _ids, kind):
            ok_calls.append(kind)

        register_photo_deletion_hook(bad_hook)
        register_photo_deletion_hook(good_hook)
        try:
            soft_delete_photos(conn, photo_ids)
            # good_hook still ran despite bad_hook raising.
            assert ok_calls == ["soft"]
        finally:
            unregister_photo_deletion_hook(bad_hook)
            unregister_photo_deletion_hook(good_hook)


def test_unregister_unknown_hook_returns_false():
    def never_registered(_c, _ids, _k):
        pass

    assert unregister_photo_deletion_hook(never_registered) is False
