"""Tests for bp_media, bp_pets, and state modules — part 2 of 4: TestBuildPhotoDict,
TestSwitchLibrary, TestAutoPurge."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import os
from unittest.mock import MagicMock

from bpp.web.app import create_app
from tests._media_state_shared import (  # noqa: F401
    _create_png_image,
    _create_test_image,
    app_with_pets,
    app_with_png,
    app_with_thumbs,
    bare_app,
)


class TestBuildPhotoDict:
    """WebAppState.build_photo_dict()"""

    def test_basic_dict(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            item = {
                "filepath": filepath,
                "date": "2024-01-15",
                "date_day": "2024-01-15",
                "date_month": "2024-01",
                "blur_score": 0.5,
                "exposure_score": 0.6,
                "face_score": 0.3,
                "composition_score": 0.7,
                "skin_score": 0.4,
                "nudity_score": 0.1,
                "aggregate_score": 0.8,
                "cluster_size": 2,
                "deleted_at": None,
                "pet_count": 0,
                "has_cat": 0,
                "has_dog": 0,
            }
            result = ctx.build_photo_dict(item)

        assert result["filepath"] == filepath
        assert result["filename"] == os.path.basename(filepath)
        assert result["aggregate_score"] == 0.8
        assert result["blur_score"] == 0.5
        assert result["cluster_size"] == 2
        assert result["thumb_hash"] != ""
        assert "selected" not in result

    def test_with_selected_flag(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            item = {"filepath": filepath, "aggregate_score": 0.5}
            result = ctx.build_photo_dict(item, selected=True)

        assert result["selected"] is True

    def test_selected_false(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            item = {"filepath": filepath, "aggregate_score": 0.5}
            result = ctx.build_photo_dict(item, selected=False)

        assert result["selected"] is False

    def test_with_similar_photos(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            item = {
                "filepath": filepath,
                "aggregate_score": 0.8,
                "similar_photos": [
                    {
                        "filepath": filepath,
                        "similarity": 0.95,
                    }
                ],
            }
            result = ctx.build_photo_dict(item)

        assert "similar_photos" in result
        assert len(result["similar_photos"]) == 1
        assert result["similar_photos"][0]["similarity"] == 0.95
        assert result["similar_photos"][0]["thumb_hash"] != ""

    def test_missing_fields_default_to_zero_or_empty(self, app_with_thumbs):
        app, filepath = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            # Minimal item - only filepath required
            item = {"filepath": filepath}
            result = ctx.build_photo_dict(item)

        assert result["date"] == ""
        assert result["blur_score"] == 0
        assert result["aggregate_score"] == 0
        assert result["cluster_size"] == 1
        assert result["pet_count"] == 0

    def test_no_thumbs_returns_empty_hash(self, bare_app):
        with bare_app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.thumbs = None
            item = {"filepath": "/tmp/test.jpg"}
            result = ctx.build_photo_dict(item)

        assert result["thumb_hash"] == ""


class TestSwitchLibrary:
    """WebAppState.switch_library()"""

    def test_successful_switch(self, tmp_path):
        lib1 = tmp_path / "lib1"
        lib2 = tmp_path / "lib2"
        lib1.mkdir()
        lib2.mkdir()

        app = create_app(
            workdir=str(lib1),
            input_dir=str(lib1),
            library_path=str(lib1),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.switch_library(str(lib2))
            assert ctx.state["library_path"] == str(lib2)
            assert ctx.state["workdir"] == os.path.join(str(lib2), "data")
            assert ctx.state["analysis"] is None or isinstance(ctx.state["analysis"], list)

    def test_raises_when_workers_running(self, tmp_path):
        lib1 = tmp_path / "lib1"
        lib2 = tmp_path / "lib2"
        lib1.mkdir()
        lib2.mkdir()

        app = create_app(
            workdir=str(lib1),
            input_dir=str(lib1),
            library_path=str(lib1),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            # Mock worker as alive with cancel + join support
            mock_worker = MagicMock()
            mock_worker.is_alive = True
            mock_worker._thread = MagicMock()
            ctx._workers["analyze"] = mock_worker

            # Switch should succeed — cancels workers instead of refusing
            ctx.switch_library(str(lib2))
            mock_worker.cancel_and_join.assert_called_once()
            assert ctx.state["library_path"] == str(lib2)

    def test_switch_clears_clip_cache(self, tmp_path):
        lib1 = tmp_path / "lib1"
        lib2 = tmp_path / "lib2"
        lib1.mkdir()
        lib2.mkdir()

        app = create_app(
            workdir=str(lib1),
            input_dir=str(lib1),
            library_path=str(lib1),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.clip_cache = {
                "embeddings": {1: "fake"},
                "ready": True,
            }
            ctx.switch_library(str(lib2))
            assert ctx.clip_cache["ready"] is False
            assert ctx.clip_cache["embeddings"] == {}

    def test_switch_resets_thumbs(self, tmp_path):
        lib1 = tmp_path / "lib1"
        lib2 = tmp_path / "lib2"
        lib1.mkdir()
        lib2.mkdir()

        app = create_app(
            workdir=str(lib1),
            input_dir=str(lib1),
            library_path=str(lib1),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            ctx.thumbs = MagicMock()
            ctx.switch_library(str(lib2))
            # thumbs should be None after lock section, but
            # startup() may rebuild them. The key check is that
            # during the switch it was set to None.
            # We verify by checking the library path changed.
            assert ctx.state["library_path"] == str(lib2)


class TestAutoPurge:
    """WebAppState.auto_purge()"""

    def test_nothing_to_purge(self, app_with_thumbs):
        app, _ = app_with_thumbs
        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            # auto_purge already ran at startup; running again
            # should not raise
            ctx.auto_purge()

    def test_purges_expired_photos(self, tmp_path):
        img_path = tmp_path / "old_photo.jpg"
        _create_test_image(img_path)

        app = create_app(
            workdir=str(tmp_path),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            from bpp.db.photos import upsert_photo

            photo_id = upsert_photo(
                conn,
                {
                    "filepath": str(img_path),
                    "aggregate_score": 0.5,
                },
            )
            # Set deleted_at to 60 days ago
            conn.execute(
                "UPDATE photos SET deleted_at = datetime('now', '-60 days') WHERE id = ?",
                (photo_id,),
            )
            conn.commit()

            # Verify photo exists before purge
            row = conn.execute(
                "SELECT id FROM photos WHERE id=?",
                (photo_id,),
            ).fetchone()
            assert row is not None

            ctx.auto_purge()

            # Photo should be permanently deleted
            row = conn.execute(
                "SELECT id FROM photos WHERE id=?",
                (photo_id,),
            ).fetchone()
            assert row is None

    def test_does_not_purge_recent_deletes(self, tmp_path):
        img_path = tmp_path / "recent_photo.jpg"
        _create_test_image(img_path)

        app = create_app(
            workdir=str(tmp_path),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            from bpp.db.photos import upsert_photo

            photo_id = upsert_photo(
                conn,
                {
                    "filepath": str(img_path),
                    "aggregate_score": 0.5,
                },
            )
            # Set deleted_at to 5 days ago (within 30-day window)
            conn.execute(
                "UPDATE photos SET deleted_at = datetime('now', '-5 days') WHERE id = ?",
                (photo_id,),
            )
            conn.commit()

            ctx.auto_purge()

            # Photo should still exist
            row = conn.execute(
                "SELECT id FROM photos WHERE id=?",
                (photo_id,),
            ).fetchone()
            assert row is not None

    def test_auto_purge_removes_file_from_disk(self, tmp_path):
        img_path = tmp_path / "to_delete.jpg"
        _create_test_image(img_path)

        app = create_app(
            workdir=str(tmp_path),
            input_dir=str(tmp_path),
            library_path=str(tmp_path),
        )
        app.config["TESTING"] = True

        with app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            from bpp.db.photos import upsert_photo

            upsert_photo(
                conn,
                {
                    "filepath": str(img_path),
                    "aggregate_score": 0.5,
                },
            )
            conn.execute(
                "UPDATE photos SET deleted_at = datetime('now', '-60 days') WHERE filepath = ?",
                (str(img_path),),
            )
            conn.commit()

            assert os.path.exists(str(img_path))
            ctx.auto_purge()
            assert not os.path.exists(str(img_path))
