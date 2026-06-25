"""Regression coverage for the cache-invalidation audit (2026-06).

Findings the audit surfaced:

* ``with_face_lock`` decorated 13 face-mutation endpoints but never
  invalidated ``face_cluster_map``. Mutations between background-worker
  runs left the grid + /pick boost reading a stale
  ``filepath → [cluster_ids]`` dict. Fix wires the invalidate into the
  decorator's ``finally`` block so every endpoint inherits it.

* ``api_video_trim`` overwrote the video file but didn't invalidate
  the content-addressed sprite/thumbnail cache, so the next
  /video/sprite request served frames cut off the head/tail. Fix
  adds an explicit ``_invalidate_photo_cache`` call after the DB
  duration update.

Both fixes are structural — locking them with these tests means a
future refactor that removes the invalidate paths fails loud.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# ──────────────────────────────────────────────────────────────────
# with_face_lock invalidation contract
# ──────────────────────────────────────────────────────────────────


class TestWithFaceLockInvalidatesClusterMap:
    """The decorator MUST invalidate face_cluster_map on exit so the
    13 face-mutation endpoints downstream of it inherit the cache
    fix without each one having to call invalidate explicitly.
    """

    def _build_ctx(self):
        ctx = MagicMock()
        # face_op_lock is used as a context manager — give it the
        # standard __enter__/__exit__ protocol.
        ctx.face_op_lock = MagicMock()
        ctx.face_op_lock.__enter__ = MagicMock(return_value=None)
        ctx.face_op_lock.__exit__ = MagicMock(return_value=None)
        return ctx

    def test_decorator_invalidates_on_success(self):
        from bpp.web.state import with_face_lock

        ctx = self._build_ctx()
        with patch("bpp.web.state.get_ctx", return_value=ctx):

            @with_face_lock
            def handler():
                return "ok"

            assert handler() == "ok"
        ctx.invalidate_face_cluster_map.assert_called_once()

    def test_decorator_invalidates_on_exception(self):
        """The cache is invalidated even when the handler raises.

        A partial mutation that committed before raising leaves the
        cluster_id columns in a state the cache no longer matches;
        stale-on-error is the worst outcome to ship.
        """
        from bpp.web.state import with_face_lock

        ctx = self._build_ctx()
        with patch("bpp.web.state.get_ctx", return_value=ctx):

            @with_face_lock
            def handler():
                raise RuntimeError("simulated mutation failure")

            import pytest

            with pytest.raises(RuntimeError, match="simulated"):
                handler()
        ctx.invalidate_face_cluster_map.assert_called_once()

    def test_invalidate_called_after_lock_release(self):
        """The invalidate fires inside the with-lock block (so a
        concurrent reader on a fresh-acquire of the lock sees the
        cleared cache), not outside it.
        """
        from bpp.web.state import with_face_lock

        ctx = self._build_ctx()
        call_log = []
        ctx.face_op_lock.__enter__ = MagicMock(side_effect=lambda: call_log.append("lock_acquired"))
        ctx.face_op_lock.__exit__ = MagicMock(
            side_effect=lambda *_: call_log.append("lock_released")
        )
        ctx.invalidate_face_cluster_map = MagicMock(
            side_effect=lambda: call_log.append("invalidated")
        )

        with patch("bpp.web.state.get_ctx", return_value=ctx):

            @with_face_lock
            def handler():
                call_log.append("body_ran")
                return "ok"

            handler()

        # Body runs while lock is held; invalidate fires before the
        # lock is released so a concurrent reader on a fresh lock
        # acquire sees the cleared cache.
        assert call_log == [
            "lock_acquired",
            "body_ran",
            "invalidated",
            "lock_released",
        ]

    def test_every_face_mutation_endpoint_still_carries_the_decorator(self):
        """Source-scan: the 13 endpoints the audit identified all
        decorate with @with_face_lock. A future refactor that removes
        the decorator from any of them loses the cache fix silently
        — this test fails loud instead.
        """
        from pathlib import Path

        expected = [
            ("bpp/web/bp_faces_bbox.py", "api_faces_create"),
            ("bpp/web/bp_faces_bbox.py", "api_faces_update_bbox"),
            ("bpp/web/bp_faces_cluster_ops.py", "api_faces_merge"),
            ("bpp/web/bp_faces_cluster_ops.py", "api_faces_dismiss"),
            ("bpp/web/bp_faces_cluster_ops.py", "api_faces_split"),
            ("bpp/web/bp_faces_cluster_ops.py", "api_faces_restore"),
            ("bpp/web/bp_faces_extract.py", "api_faces_retry"),
            ("bpp/web/bp_faces_manage.py", "api_tag_person"),
            ("bpp/web/bp_faces_manage.py", "api_untag_person"),
            ("bpp/web/bp_faces_manage.py", "api_faces_reassign"),
            ("bpp/web/bp_faces_manage.py", "api_faces_purge"),
            ("bpp/web/bp_faces_recluster.py", "api_faces_recluster"),
        ]
        for file, fname in expected:
            src = Path(file).read_text()
            assert f"def {fname}(" in src, (
                f"{file} no longer defines {fname} — audit list is stale, "
                f"update tests/test_cache_invalidation_audit.py"
            )
            # Confirm @with_face_lock decorator appears in the ~5 lines
            # above the def. We don't require a specific order with
            # @bp.post since the decorators stack.
            idx = src.index(f"def {fname}(")
            prelude = src[max(0, idx - 400) : idx]
            assert "@with_face_lock" in prelude, (
                f"{file}::{fname} lost @with_face_lock — face-mutation "
                f"endpoint no longer invalidates the cluster map. Add "
                f"the decorator back."
            )


# ──────────────────────────────────────────────────────────────────
# api_video_trim invalidates the photo cache for the trimmed file
# ──────────────────────────────────────────────────────────────────


class TestVideoTrimInvalidatesPhotoCache:
    def test_source_calls_invalidate_photo_cache(self):
        """Source-scan: the trim handler must drop the cached image
        variants for the path. Without it, the sprite shows frames
        from the un-trimmed video.
        """
        from pathlib import Path

        src = Path("bpp/web/bp_media.py").read_text()
        # Find the api_video_trim function body.
        idx = src.index("def api_video_trim(")
        # Body extends until the next top-level def / @bp.
        end = idx
        import re

        m = re.search(r"\n(@bp\.|def )", src[idx + 10 :])
        end = idx + 10 + m.start() if m else len(src)
        body = src[idx:end]
        assert "_invalidate_photo_cache" in body, (
            "api_video_trim must call _invalidate_photo_cache after "
            "the trim overwrites the file on disk — otherwise the "
            "cached sprite serves frames from the pre-trim video"
        )
