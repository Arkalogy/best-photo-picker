"""Tests for bp_media, bp_pets, and state modules — part 4 of 4:
TestThumbnailVerifiedIntegration."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

from tests._media_state_shared import (  # noqa: F401
    _create_png_image,
    _create_test_image,
    app_with_pets,
    app_with_png,
    app_with_thumbs,
    bare_app,
)


class TestThumbnailVerifiedIntegration:
    """Integration tests: verified cache through Flask endpoints."""

    def test_repeated_thumb_requests_use_cache(self, app_with_thumbs):
        """Multiple /thumb/ requests should all succeed and return same data."""
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            h = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            r1 = c.get(f"/thumb/{h}")
            assert r1.status_code == 200
            # Hash should now be in verified set
            with app.app_context():
                assert h in get_ctx().thumbs._verified

            r2 = c.get(f"/thumb/{h}")
            assert r2.status_code == 200
            assert r1.data == r2.data

    def test_clear_then_re_request_regenerates(self, app_with_thumbs):
        """After /api/thumbnails/clear, next thumb request regenerates."""
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            h = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            # Generate and verify
            r1 = c.get(f"/thumb/{h}")
            assert r1.status_code == 200
            with app.app_context():
                assert h in get_ctx().thumbs._verified

            # Clear all thumbnails
            c.post("/api/v1/thumbnails/clear")
            with app.app_context():
                assert h not in get_ctx().thumbs._verified

            # Re-request — should regenerate successfully
            r2 = c.get(f"/thumb/{h}")
            assert r2.status_code == 200
            assert len(r2.data) > 0

    def test_enhance_invalidates_verified_thumb(self, app_with_thumbs):
        """Auto-enhance should invalidate the verified cache for affected photo."""
        app, filepath = app_with_thumbs

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            h = ctx.thumbs.get_hash(filepath)

        with app.test_client() as c:
            # Generate thumbnail first
            r1 = c.get(f"/thumb/{h}")
            assert r1.status_code == 200
            with app.app_context():
                assert h in get_ctx().thumbs._verified

            # Enhance the photo
            c.post(
                "/api/v1/photos/enhance",
                json={"filepaths": [filepath]},
                content_type="application/json",
            )

            # Verified set should have been cleared for this hash
            with app.app_context():
                assert h not in get_ctx().thumbs._verified
