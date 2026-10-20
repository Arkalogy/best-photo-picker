"""Tests for bp_media, bp_pets, and state modules — part 3 of 4: TestCheckDedupFeedback,
TestInvalidateAnalysis, TestEnsureWorkdir."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import os
import threading

import numpy as np

from bpp.web.app import create_app
from tests._media_state_shared import (  # noqa: F401
    _create_png_image,
    _create_test_image,
    app_with_pets,
    app_with_png,
    app_with_thumbs,
    bare_app,
)


class TestCheckDedupFeedback:
    """WebAppState.check_dedup_feedback()"""

    def test_mode_not_include_exclude_returns_false(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            result = ctx.check_dedup_feedback(conn, 1, filepath, "keep", {filepath})
            assert result is False

    def test_none_mode_returns_false(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            result = ctx.check_dedup_feedback(conn, 1, filepath, None, {filepath})
            assert result is False

    def test_no_selected_paths_returns_false(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            result = ctx.check_dedup_feedback(conn, 1, filepath, "include", None)
            assert result is False

    def test_empty_selected_paths_returns_false(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            result = ctx.check_dedup_feedback(conn, 1, filepath, "include", set())
            assert result is False

    def test_no_clip_ready_returns_false(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            ctx.clip_cache = {"embeddings": {}, "ready": False}
            result = ctx.check_dedup_feedback(conn, 1, filepath, "include", {filepath})
            assert result is False

    def test_clip_ready_but_no_embedding_for_photo(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            ctx.clip_cache = {
                "embeddings": {999: np.ones(512)},
                "ready": True,
            }
            result = ctx.check_dedup_feedback(conn, 1, filepath, "exclude", {filepath})
            assert result is False

    def test_records_feedback_for_similar_pair(self, tmp_path):
        """Full feedback recording with two photos and CLIP embeddings."""
        img1 = tmp_path / "img1.jpg"
        img2 = tmp_path / "img2.jpg"
        _create_test_image(img1, "red")
        _create_test_image(img2, "blue")

        app = create_app(
            workdir=str(tmp_path),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            id1 = upsert_photo(
                conn,
                {
                    "filepath": str(img1),
                    "aggregate_score": 0.8,
                },
            )
            id2 = upsert_photo(
                conn,
                {
                    "filepath": str(img2),
                    "aggregate_score": 0.7,
                },
            )
            conn.commit()
            ctx.invalidate_analysis()
            ctx.load_analysis_if_needed()

            # Create near-identical L2-normalized embeddings
            emb = np.random.randn(512).astype(np.float32)
            emb = emb / np.linalg.norm(emb)
            ctx.clip_cache = {
                "embeddings": {id1: emb, id2: emb},
                "ready": True,
            }

            result = ctx.check_dedup_feedback(
                conn,
                id1,
                str(img1),
                "exclude",
                {str(img2)},
            )
            assert result is True

            # Verify feedback was recorded
            row = conn.execute("SELECT verdict FROM dedup_feedback LIMIT 1").fetchone()
            assert row is not None
            assert row[0] == "same"


class TestInvalidateAnalysis:
    """WebAppState.invalidate_analysis()"""

    def test_invalidate_clears_analysis(self, app_with_thumbs):
        app, _ = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            # Should have analysis loaded
            assert ctx.state["analysis"] is not None
            ctx.invalidate_analysis()
            assert ctx.state["analysis"] is None

    def test_invalidate_is_thread_safe(self, app_with_thumbs):
        """Multiple threads invalidating simultaneously should not crash."""
        app, _ = app_with_thumbs

        errors = []

        def _invalidate():
            try:
                with app.app_context():
                    from bpp.web.state import get_ctx

                    ctx = get_ctx()
                    ctx.invalidate_analysis()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=_invalidate) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == []


class TestEnsureWorkdir:
    """WebAppState.ensure_workdir()"""

    def test_creates_tempdir_when_none(self, tmp_path):
        app = create_app(
            workdir=None,
            input_dir=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            # ensure_workdir should have been called during startup
            wd = ctx.ensure_workdir()
            assert wd is not None
            assert os.path.isdir(wd)

    def test_returns_existing_workdir(self, tmp_path):
        wd = tmp_path / "mywork"
        wd.mkdir()

        app = create_app(
            workdir=str(wd),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            result = ctx.ensure_workdir()
            assert result == str(wd)


class TestLoadAnalysisIfNeeded:
    """WebAppState.load_analysis_if_needed()"""

    def test_returns_none_for_empty_db(self, bare_app):
        with bare_app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.invalidate_analysis()
            result = ctx.load_analysis_if_needed()
            # No photos in DB => None or empty
            assert result is None or result == []

    def test_returns_data_after_upsert(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            data = ctx.load_analysis_if_needed()
            assert data is not None
            assert len(data) >= 1
            assert any(d["filepath"] == filepath for d in data)

    def test_caches_result(self, app_with_thumbs):
        """Second call should return same object (cached)."""
        app, _ = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            data1 = ctx.load_analysis_if_needed()
            data2 = ctx.load_analysis_if_needed()
            assert data1 is data2

    def test_builds_thumbs(self, app_with_thumbs):
        app, _ = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            assert ctx.thumbs is not None


class TestClampHelpers:
    """Test clamp_k."""

    def testclamp_k_within_range(self):
        from bpp.web.state import clamp_k

        assert clamp_k(50) == 50

    def testclamp_k_below_min(self):
        from bpp.web.state import clamp_k

        assert clamp_k(0) == 1

    def testclamp_k_above_max(self):
        from bpp.web.state import clamp_k

        assert clamp_k(99999) == 10000

    def testclamp_k_invalid_returns_default(self):
        from bpp.web.state import clamp_k

        assert clamp_k("abc") == 50

    def testclamp_k_none_returns_default(self):
        from bpp.web.state import clamp_k

        assert clamp_k(None) == 50

    def testclamp_k_custom_default(self):
        from bpp.web.state import clamp_k

        assert clamp_k("bad", default=100) == 100


# ---------------------------------------------------------------------------
# Thumbnail verified-cache tests (unit + integration)
# ---------------------------------------------------------------------------


class TestThumbnailVerifiedCache:
    """Unit tests for the in-memory _verified fast path in ThumbnailCache."""

    def test_first_call_populates_verified(self, tmp_path):
        """First get_thumbnail call should add hash to _verified set."""
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        img = tmp_path / "photo.jpg"
        _create_test_image(img)
        cache.build_map_from_paths([str(img)])
        h = cache.get_hash(str(img))

        assert h not in cache._verified
        result = cache.get_thumbnail(h)
        assert result is not None
        assert h in cache._verified

    def test_second_call_uses_fast_path(self, tmp_path):
        """After verification, get_thumbnail should skip stat calls."""
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        img = tmp_path / "photo.jpg"
        _create_test_image(img)
        cache.build_map_from_paths([str(img)])
        h = cache.get_hash(str(img))

        # First call — generates and verifies
        path1 = cache.get_thumbnail(h)
        assert h in cache._verified

        # Delete source file — fast path should still return cached thumb
        os.remove(str(img))
        path2 = cache.get_thumbnail(h)
        assert path2 == path1
        assert os.path.isfile(path2)

    def test_invalidate_forces_recheck(self, tmp_path):
        """After invalidate(), the next call should re-check disk."""
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        img = tmp_path / "photo.jpg"
        _create_test_image(img)
        cache.build_map_from_paths([str(img)])
        h = cache.get_hash(str(img))

        cache.get_thumbnail(h)
        assert h in cache._verified

        cache.invalidate(h)
        assert h not in cache._verified

        # Next call should still work (re-verifies from disk)
        result = cache.get_thumbnail(h)
        assert result is not None
        assert h in cache._verified

    def test_clear_resets_verified(self, tmp_path):
        """clear() should empty the _verified set."""
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        img = tmp_path / "photo.jpg"
        _create_test_image(img)
        cache.build_map_from_paths([str(img)])
        h = cache.get_hash(str(img))

        cache.get_thumbnail(h)
        assert h in cache._verified

        cache.clear()
        assert len(cache._verified) == 0

    def test_invalidate_nonexistent_hash_is_safe(self, tmp_path):
        """Invalidating a hash not in _verified should not raise."""
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        cache.invalidate("does_not_exist")  # should not raise

    def test_verified_survives_source_modification(self, tmp_path):
        """Fast path returns cached thumb even if source file changes."""
        from bpp.web.thumbnails import ThumbnailCache

        cache = ThumbnailCache(str(tmp_path / "thumbs"))
        img = tmp_path / "photo.jpg"
        _create_test_image(img)
        cache.build_map_from_paths([str(img)])
        h = cache.get_hash(str(img))

        path1 = cache.get_thumbnail(h)
        assert h in cache._verified

        # Modify the source (newer mtime)
        _create_test_image(img, color="red")

        # Fast path still returns old cached thumb (no stat check)
        path2 = cache.get_thumbnail(h)
        assert path2 == path1
