"""P4 — ModelCache collaborator unit tests.

The three caches that ``ModelCache`` bundles (face cluster map,
enhanced ids, CLIP embeddings) have independent invalidation triggers,
so each gets its own test class.

Heavy DB-backed loads (``get_face_cluster_map`` and
``EnhancedIdsCache.load``) are exercised in the broader integration
tests; here we use sqlite3 ``:memory:`` with the minimal schema each
loader needs.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from bpp.web.model_cache import (
    EnhancedIdsCache,
    FaceClusterMapCache,
    ModelCache,
)

# ── FaceClusterMapCache ──


class TestFaceClusterMapCache:
    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        # Minimal schema for load_face_cluster_map. It selects from a
        # photos JOIN face_embeddings query.
        c.execute(
            "CREATE TABLE photos (id INTEGER PRIMARY KEY, filepath TEXT, missing INTEGER DEFAULT 0)"
        )
        c.execute(
            "CREATE TABLE face_embeddings ("
            " id INTEGER PRIMARY KEY,"
            " photo_id INTEGER, face_index INTEGER, cluster_id INTEGER,"
            " bbox_x INTEGER, bbox_y INTEGER, bbox_w INTEGER, bbox_h INTEGER,"
            " embedding BLOB, quality REAL"
            ")"
        )
        c.execute("INSERT INTO photos (id, filepath) VALUES (1, '/a.jpg'), (2, '/b.jpg')")
        c.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, cluster_id)"
            " VALUES (1, 0, 5), (1, 1, 7), (2, 0, 5)"
        )
        c.commit()
        yield c
        c.close()

    def test_first_get_loads_from_db(self, conn):
        cache = FaceClusterMapCache()
        result = cache.get(conn)
        # Map keys are filepaths; values are cluster lists.
        assert "/a.jpg" in result
        assert sorted(result["/a.jpg"]) == [5, 7]
        assert result["/b.jpg"] == [5]

    def test_second_get_hits_cache(self, conn):
        """After load, the second call must return the same dict
        identity — no re-query. The contract is "invalidate forces
        reload," so the same object proves we cached."""
        cache = FaceClusterMapCache()
        first = cache.get(conn)
        # Mutate DB after load — if cache is stale (good), we still
        # see the old result.
        conn.execute("DELETE FROM face_embeddings")
        conn.commit()
        second = cache.get(conn)
        assert second is first

    def test_invalidate_forces_reload(self, conn):
        cache = FaceClusterMapCache()
        cache.get(conn)
        conn.execute("DELETE FROM face_embeddings")
        conn.commit()
        cache.invalidate()
        result = cache.get(conn)
        # After invalidation and a real DB change, the reload reflects it.
        assert result == {}

    def test_concurrent_invalidate_during_get_doesnt_deadlock(self, monkeypatch):
        """The cache loads outside the lock so an invalidate fired
        concurrently can't deadlock. Worst case: the reader's
        post-load assignment races with the invalidate, and the
        reader's result wins. This test confirms no hang.

        Uses a stubbed loader (no SQLite) because sqlite3 connections
        are bound to their creating thread — the test's reader runs in
        a separate thread for the race-condition exercise.
        """
        import bpp.web.model_cache as model_cache_mod

        # Stub the loader to return a deterministic dict without touching
        # SQLite (which would refuse cross-thread access). The lock-
        # discipline contract is what we're verifying, not the query.
        def _fake_load(_conn):
            return {"/x.jpg": [1]}

        monkeypatch.setattr("bpp.db.face_queries.load_face_cluster_map", _fake_load)
        _ = model_cache_mod  # silence unused-import lint

        cache = FaceClusterMapCache()
        done = threading.Event()

        def _read():
            cache.get(None)  # conn unused after stub
            done.set()

        # Pre-fire invalidate, then start reader, then invalidate again.
        cache.invalidate()
        t = threading.Thread(target=_read, daemon=True)
        t.start()
        for _ in range(5):
            cache.invalidate()
        assert done.wait(5), "reader deadlocked under concurrent invalidate"


# ── EnhancedIdsCache ──


class TestEnhancedIdsCache:
    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        # Tables exercised by get_edited_photo_ids + get_auto_enhanced_photo_ids.
        c.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY)")
        c.execute(
            "CREATE TABLE photo_edits ("
            " photo_id INTEGER PRIMARY KEY,"
            " auto_enhanced INTEGER DEFAULT 0,"
            " enhanced INTEGER DEFAULT 0"
            ")"
        )
        c.commit()
        yield c
        c.close()

    def test_both_loaded_starts_false(self):
        cache = EnhancedIdsCache()
        assert cache.both_loaded() is False
        assert cache.edited is None
        assert cache.auto_enhanced is None

    def test_load_populates_both_sets(self, conn):
        conn.execute("INSERT INTO photos VALUES (1), (2), (3)")
        conn.execute(
            "INSERT INTO photo_edits (photo_id, auto_enhanced, enhanced)"
            " VALUES (1, 1, 0), (2, 0, 1), (3, 1, 1)"
        )
        conn.commit()
        cache = EnhancedIdsCache()
        cache.load(conn)
        assert cache.both_loaded() is True
        # Set contents come from get_*_photo_ids — we don't pin exact
        # contents (the queries may evolve) but both must be sets.
        assert isinstance(cache.edited, set)
        assert isinstance(cache.auto_enhanced, set)

    def test_invalidate_clears_both(self):
        cache = EnhancedIdsCache(edited={1, 2}, auto_enhanced={3})
        cache.invalidate()
        assert cache.edited is None
        assert cache.auto_enhanced is None
        assert cache.both_loaded() is False


# ── ModelCache facade ──


class TestModelCacheFacade:
    def test_defaults_construct_independent_caches(self):
        m1 = ModelCache()
        m2 = ModelCache()
        # Each ModelCache has its own sub-caches — no shared dict
        # leaking between WebAppStates.
        assert m1.face_cluster_map is not m2.face_cluster_map
        assert m1.enhanced_ids is not m2.enhanced_ids
        assert m1.clip_cache is not m2.clip_cache

    def test_clip_cache_default_shape(self):
        m = ModelCache()
        assert m.clip_cache == {"embeddings": {}, "ready": False}

    def test_invalidate_all_resets_every_cache(self):
        m = ModelCache()
        m.face_cluster_map._map = {"/x.jpg": [1]}
        m.enhanced_ids.edited = {1}
        m.enhanced_ids.auto_enhanced = {2}
        m.clip_cache["ready"] = True
        m.clip_cache["embeddings"]["/x.jpg"] = b"emb"

        m.invalidate_all()

        assert m.face_cluster_map._map is None
        assert m.enhanced_ids.edited is None
        assert m.enhanced_ids.auto_enhanced is None
        assert m.clip_cache == {"embeddings": {}, "ready": False}


# ── WebAppState legacy access (back-compat) ──


class TestWebAppStateLegacyAccess:
    """The bare-attribute access sites (``ctx.clip_cache``,
    ``ctx._edited_ids``, ``ctx._face_cluster_map``,
    ``ctx._face_cluster_map_lock``, ``ctx._auto_enhanced_ids``) read
    + write through to the underlying ModelCache. The audit found
    ~3,800 such sites; they all keep working unchanged."""

    def test_legacy_attributes_route_through_cache(self):
        from types import SimpleNamespace

        from bpp.web.state import WebAppState

        cache = ModelCache()
        fake_ctx = SimpleNamespace(caches=cache)

        # clip_cache delegate reads through.
        clip = WebAppState.clip_cache.fget(fake_ctx)
        assert clip is cache.clip_cache

        # _face_cluster_map setter routes write through to the inner field.
        WebAppState._face_cluster_map.fset(fake_ctx, {"/a.jpg": [1]})
        assert cache.face_cluster_map._map == {"/a.jpg": [1]}

        # _edited_ids setter routes through.
        WebAppState._edited_ids.fset(fake_ctx, {7})
        assert cache.enhanced_ids.edited == {7}

        # _auto_enhanced_ids setter routes through.
        WebAppState._auto_enhanced_ids.fset(fake_ctx, {9})
        assert cache.enhanced_ids.auto_enhanced == {9}

        # _face_cluster_map_lock returns the underlying lock object.
        lock = WebAppState._face_cluster_map_lock.fget(fake_ctx)
        assert lock is cache.face_cluster_map._lock
