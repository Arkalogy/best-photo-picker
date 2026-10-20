"""TDD tests for Video support — detection, thumbnails, playback API — part 1 of 2."""

from __future__ import annotations

import os

import pytest
from PIL import Image

from tests._video_shared import _make_fake_video, _make_photo, _make_test_video

# ── Video detection ──


class TestVideoDetection:
    """Tests for is_video_file() utility."""

    def test_mp4_is_video(self):
        from bpp.utils.video import is_video_file

        assert is_video_file("vacation.mp4") is True

    def test_mov_is_video(self):
        from bpp.utils.video import is_video_file

        assert is_video_file("clip.MOV") is True

    def test_avi_is_video(self):
        from bpp.utils.video import is_video_file

        assert is_video_file("old.avi") is True

    def test_mkv_is_video(self):
        from bpp.utils.video import is_video_file

        assert is_video_file("movie.mkv") is True

    def test_webm_is_video(self):
        from bpp.utils.video import is_video_file

        assert is_video_file("clip.webm") is True

    def test_jpg_not_video(self):
        from bpp.utils.video import is_video_file

        assert is_video_file("photo.jpg") is False

    def test_png_not_video(self):
        from bpp.utils.video import is_video_file

        assert is_video_file("screenshot.png") is False

    def test_video_extensions_constant(self):
        from bpp.utils.video import VIDEO_EXTENSIONS

        assert ".mp4" in VIDEO_EXTENSIONS
        assert ".mov" in VIDEO_EXTENSIONS


# ── DB: is_video flag ──


class TestVideoInDB:
    """Tests for video flag in photos table."""

    @pytest.fixture()
    def db(self, tmp_path):
        from bpp.db.connection import init_db

        return init_db(str(tmp_path / "test.db"))

    def test_photo_not_video(self, db, tmp_path):
        from bpp.db.photos import get_photo, upsert_photo

        path = _make_photo(tmp_path, "photo.jpg")
        pid = upsert_photo(db, {"filepath": path, "is_video": 0})
        photo = get_photo(db, pid)
        assert photo["is_video"] == 0

    def test_video_flag_stored(self, db, tmp_path):
        from bpp.db.photos import get_photo, upsert_photo

        path = _make_fake_video(tmp_path, "clip.mp4")
        pid = upsert_photo(db, {"filepath": path, "is_video": 1})
        photo = get_photo(db, pid)
        assert photo["is_video"] == 1


# ── API: video serving ──


class TestVideoAPI:
    """Tests for video serving endpoint."""

    @pytest.fixture()
    def app(self, tmp_path):
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir, exist_ok=True)
        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        return app

    @pytest.fixture()
    def client(self, app):
        return app.test_client()

    def _add_video(self, app, tmp_path):
        with app.app_context():
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx
            from bpp.web.thumbnails import ThumbnailCache

            ctx = get_ctx()
            conn = ctx.get_conn()
            path = _make_fake_video(tmp_path, "clip.mp4")
            pid = upsert_photo(conn, {"filepath": path, "is_video": 1})
            # Ensure thumbnails are initialized so get_hash/get_filepath work
            if ctx.thumbs is None:
                ctx.thumbs = ThumbnailCache(os.path.join(ctx.ensure_workdir(), "web_thumbs"))
            ctx.thumbs.build_map([{"filepath": path, "id": pid}])
            thumb_hash = ctx.thumbs.get_hash(path)
            return pid, path, thumb_hash

    def test_video_endpoint_serves_file(self, client, app, tmp_path):
        _pid, path, thumb_hash = self._add_video(app, tmp_path)
        resp = client.get(f"/video/{thumb_hash}")
        assert resp.status_code == 200
        assert "video" in resp.content_type
        os.unlink(path)

    def test_video_endpoint_missing(self, client):
        resp = client.get("/video/nonexistent")
        assert resp.status_code == 404


# ── Video thumbnail extraction ──


class TestVideoThumbnail:
    """Tests for extract_video_frame() and _make_thumbnail video path."""

    def test_extract_frame_returns_image(self, tmp_path):
        """extract_video_frame should return a PIL Image from a real video."""
        from bpp.utils.video import extract_video_frame

        video_path = _make_test_video(tmp_path, "test.mp4")
        img = extract_video_frame(video_path)
        assert img is not None
        assert img.size[0] > 0 and img.size[1] > 0

    def test_extract_frame_from_invalid_file(self, tmp_path):
        """extract_video_frame should return None for non-video data."""
        from bpp.utils.video import extract_video_frame

        bad = str(tmp_path / "bad.mp4")
        with open(bad, "wb") as f:
            f.write(b"\x00" * 512)
        assert extract_video_frame(bad) is None

    def test_extract_frame_missing_file(self):
        """extract_video_frame should return None for nonexistent file."""
        from bpp.utils.video import extract_video_frame

        assert extract_video_frame("/nonexistent/video.mp4") is None

    def test_make_thumbnail_for_video(self, tmp_path):
        """_make_thumbnail should generate a JPEG for a video file."""
        from bpp.output.gallery import _make_thumbnail

        video_path = _make_test_video(tmp_path, "clip.mp4")
        thumb_path = str(tmp_path / "thumb.jpg")
        result = _make_thumbnail(video_path, thumb_path, size=200)
        assert result is True
        assert os.path.exists(thumb_path)
        img = Image.open(thumb_path)
        assert max(img.size) <= 200

    def test_thumbnail_cache_serves_video_thumb(self, tmp_path):
        """ThumbnailCache.get_thumbnail should work for video files."""
        from bpp.web.thumbnails import ThumbnailCache

        video_path = _make_test_video(tmp_path, "vid.mp4")
        cache = ThumbnailCache(str(tmp_path / "cache"))
        cache.build_map([{"filepath": video_path}])
        h = cache.get_hash(video_path)
        thumb = cache.get_thumbnail(h)
        assert thumb is not None
        assert os.path.exists(thumb)


# ── Video metadata extraction ──


class TestVideoMetadata:
    """Tests for extract_video_metadata()."""

    def test_metadata_from_video(self, tmp_path):
        """Should extract duration, resolution, fps from a video."""
        from bpp.utils.video import extract_video_metadata

        video_path = _make_test_video(tmp_path, "meta.mp4")
        meta = extract_video_metadata(video_path)
        assert meta is not None
        assert "duration" in meta
        assert "width" in meta
        assert "height" in meta
        assert "fps" in meta
        assert meta["duration"] > 0
        assert meta["width"] > 0
        assert meta["height"] > 0

    def test_metadata_from_invalid_file(self, tmp_path):
        """Should return None for non-video data."""
        from bpp.utils.video import extract_video_metadata

        bad = str(tmp_path / "bad.mp4")
        with open(bad, "wb") as f:
            f.write(b"\x00" * 512)
        assert extract_video_metadata(bad) is None

    def test_metadata_missing_file(self):
        """Should return None for nonexistent file."""
        from bpp.utils.video import extract_video_metadata

        assert extract_video_metadata("/nonexistent.mp4") is None


class TestFormatDuration:
    """Tests for format_duration()."""

    def test_seconds(self):
        from bpp.utils.video import format_duration

        assert format_duration(5) == "0:05"

    def test_minutes(self):
        from bpp.utils.video import format_duration

        assert format_duration(83) == "1:23"

    def test_hours(self):
        from bpp.utils.video import format_duration

        assert format_duration(3735) == "1:02:15"

    def test_zero(self):
        from bpp.utils.video import format_duration

        assert format_duration(0) == "0:00"


# ── DB: video_duration column ──


class TestVideoDurationDB:
    """Tests for video_duration column in schema."""

    @pytest.fixture()
    def db(self, tmp_path):
        from bpp.db.connection import init_db

        return init_db(str(tmp_path / "test.db"))

    def test_video_duration_column_exists(self, db):
        cols = {row[1] for row in db.execute("PRAGMA table_info(photos)").fetchall()}
        assert "video_duration" in cols

    def test_video_duration_stored(self, db, tmp_path):
        from bpp.db.photos import get_photo, upsert_photo

        path = _make_fake_video(tmp_path, "clip.mp4")
        pid = upsert_photo(db, {"filepath": path, "is_video": 1, "video_duration": 12.5})
        photo = get_photo(db, pid)
        assert photo["video_duration"] == 12.5

    def test_video_duration_in_build_photo_dict(self, db, tmp_path):
        """build_photo_dict should include video_duration."""
        from bpp.web.state import WebAppState

        path = _make_fake_video(tmp_path, "clip.mp4")
        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir, exist_ok=True)
        state = WebAppState(workdir)
        state.state["analysis"] = [
            {
                "filepath": path,
                "is_video": 1,
                "video_duration": 15.0,
                "aggregate_score": 0.5,
            }
        ]
        d = state.build_photo_dict(state.state["analysis"][0])
        assert d["video_duration"] == 15.0


# ── Video metadata populated on import ──


class TestVideoMetadataOnImport:
    """Test that video metadata is extracted during library import."""

    @pytest.fixture()
    def db(self, tmp_path):
        from bpp.db.connection import init_db

        return init_db(str(tmp_path / "test.db"))

    def test_import_video_extracts_duration(self, db, tmp_path):
        """Importing a video should populate video_duration."""
        from bpp.db.library import import_folder

        src = tmp_path / "source"
        src.mkdir()
        _make_test_video(src, "clip.mp4", frames=20, size=(64, 48))

        lib = tmp_path / "library"
        lib.mkdir()

        result = import_folder(db, str(src), str(lib), batch_name="test_batch")
        assert result.imported == 1

        # Get the photo and check duration was populated
        rows = db.execute("SELECT * FROM photos").fetchall()
        assert len(rows) == 1
        assert rows[0]["is_video"] == 1
        assert rows[0]["video_duration"] is not None
        assert rows[0]["video_duration"] > 0

    def test_import_video_extracts_resolution(self, db, tmp_path):
        """Importing a video should populate video_width and video_height."""
        from bpp.db.library import import_folder

        src = tmp_path / "source"
        src.mkdir()
        _make_test_video(src, "clip.mp4", frames=10, size=(64, 48))

        lib = tmp_path / "library"
        lib.mkdir()

        import_folder(db, str(src), str(lib), batch_name="test_batch")
        row = db.execute("SELECT * FROM photos").fetchone()
        assert row["video_width"] == 64
        assert row["video_height"] == 48

    def test_import_video_extracts_fps(self, db, tmp_path):
        """Importing a video should populate video_fps."""
        from bpp.db.library import import_folder

        src = tmp_path / "source"
        src.mkdir()
        _make_test_video(src, "clip.mp4", frames=10, size=(64, 48))

        lib = tmp_path / "library"
        lib.mkdir()

        import_folder(db, str(src), str(lib), batch_name="test_batch")
        row = db.execute("SELECT * FROM photos").fetchone()
        assert row["video_fps"] is not None
        assert row["video_fps"] > 0

    def test_import_video_extracts_codec(self, db, tmp_path):
        """Importing a video should populate video_codec."""
        from bpp.db.library import import_folder

        src = tmp_path / "source"
        src.mkdir()
        _make_test_video(src, "clip.mp4", frames=10, size=(64, 48))

        lib = tmp_path / "library"
        lib.mkdir()

        import_folder(db, str(src), str(lib), batch_name="test_batch")
        row = db.execute("SELECT * FROM photos").fetchone()
        assert row["video_codec"] is not None
        assert len(row["video_codec"]) > 0


# ── V3: Video metadata in build_photo_dict ──


class TestVideoMetadataInPhotoDict:
    """Tests that video metadata fields are passed to the frontend."""

    def test_build_photo_dict_includes_video_metadata(self, tmp_path):
        from bpp.web.state import WebAppState

        path = _make_fake_video(tmp_path, "clip.mp4")
        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir, exist_ok=True)
        state = WebAppState(workdir)
        state.state["analysis"] = [
            {
                "filepath": path,
                "is_video": 1,
                "video_duration": 15.0,
                "video_width": 1920,
                "video_height": 1080,
                "video_fps": 30.0,
                "video_codec": "h264",
                "aggregate_score": 0.5,
            }
        ]
        d = state.build_photo_dict(state.state["analysis"][0])
        assert d["video_duration"] == 15.0
        assert d["video_width"] == 1920
        assert d["video_height"] == 1080
        assert d["video_fps"] == 30.0
        assert d["video_codec"] == "h264"

    def test_build_photo_dict_video_metadata_defaults_none(self, tmp_path):
        """Non-video photos should have None for video metadata."""
        from bpp.web.state import WebAppState

        path = _make_photo(tmp_path, "photo.jpg")
        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir, exist_ok=True)
        state = WebAppState(workdir)
        state.state["analysis"] = [{"filepath": path, "aggregate_score": 0.5}]
        d = state.build_photo_dict(state.state["analysis"][0])
        assert d.get("video_width") is None
        assert d.get("video_height") is None
        assert d.get("video_fps") is None
        assert d.get("video_codec") is None
