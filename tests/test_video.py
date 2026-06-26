"""TDD tests for P0-6: Video support — detection, thumbnails, playback API."""

from __future__ import annotations

import os

import pytest
from PIL import Image


def _make_photo(tmp_path, name="photo.jpg"):
    path = str(tmp_path / name)
    Image.new("RGB", (10, 10)).save(path, "JPEG")
    return path


def _make_fake_video(tmp_path, name="video.mp4"):
    """Create a fake video file (not valid but has the right extension)."""
    path = str(tmp_path / name)
    with open(path, "wb") as f:
        f.write(b"\x00" * 1024)
    return path


def _make_test_video(tmp_path, name="test.mp4", frames=10, size=(64, 48)):
    """Create a real short video using OpenCV's VideoWriter."""
    import cv2
    import numpy as np

    path = str(tmp_path / name)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(path, fourcc, 10.0, size)
    for i in range(frames):
        frame = np.zeros((size[1], size[0], 3), dtype=np.uint8)
        frame[:] = (i * 20 % 256, 100, 200)  # varying blue channel
        writer.write(frame)
    writer.release()
    return path


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


# ── V3: DB columns for video metadata ──


class TestVideoMetadataDB:
    """Tests for video_width, video_height, video_fps, video_codec columns."""

    @pytest.fixture()
    def db(self, tmp_path):
        from bpp.db.connection import init_db

        return init_db(str(tmp_path / "test.db"))

    def test_video_metadata_columns_exist(self, db):
        cols = {row[1] for row in db.execute("PRAGMA table_info(photos)").fetchall()}
        assert "video_width" in cols
        assert "video_height" in cols
        assert "video_fps" in cols
        assert "video_codec" in cols

    def test_video_metadata_stored_via_upsert(self, db, tmp_path):
        from bpp.db.photos import get_photo, upsert_photo

        path = _make_fake_video(tmp_path, "clip.mp4")
        pid = upsert_photo(
            db,
            {
                "filepath": path,
                "is_video": 1,
                "video_duration": 10.5,
                "video_width": 1920,
                "video_height": 1080,
                "video_fps": 30.0,
                "video_codec": "h264",
            },
        )
        photo = get_photo(db, pid)
        assert photo["video_width"] == 1920
        assert photo["video_height"] == 1080
        assert photo["video_fps"] == 30.0
        assert photo["video_codec"] == "h264"

    def test_video_metadata_stored_via_bulk_upsert(self, db, tmp_path):
        from bpp.db.photos import bulk_upsert_photos

        path = _make_fake_video(tmp_path, "clip.mp4")
        bulk_upsert_photos(
            db,
            [
                {
                    "filepath": path,
                    "is_video": 1,
                    "video_duration": 10.5,
                    "video_width": 1280,
                    "video_height": 720,
                    "video_fps": 24.0,
                    "video_codec": "mp4v",
                }
            ],
        )
        row = db.execute("SELECT * FROM photos").fetchone()
        assert row["video_width"] == 1280
        assert row["video_height"] == 720
        assert row["video_fps"] == 24.0
        assert row["video_codec"] == "mp4v"


# ── V4: Video export — copy raw file, skip PIL ──


class TestVideoExport:
    """Tests that video files are exported correctly (copied, not PIL-processed)."""

    def test_export_video_copy_mode(self, tmp_path):
        """Videos should be copied as-is in copy mode, even with format conversion."""
        from bpp.output.export import export_selected

        video_path = _make_fake_video(tmp_path, "clip.mp4")
        outdir = str(tmp_path / "export")
        selected = [{"filepath": video_path, "is_video": True, "aggregate_score": 0.5}]
        export_selected(selected, [], outdir, mode="copy", fmt="jpeg", max_size=800)
        # Video should be copied, not converted to JPEG
        exported_files = os.listdir(os.path.join(outdir, "selected"))
        assert len(exported_files) == 1
        assert exported_files[0].endswith(".mp4")

    def test_export_video_symlink_mode(self, tmp_path):
        """Videos should be symlinked correctly."""
        from bpp.output.export import export_selected

        video_path = _make_fake_video(tmp_path, "clip.mp4")
        outdir = str(tmp_path / "export")
        selected = [{"filepath": video_path, "is_video": True, "aggregate_score": 0.5}]
        export_selected(selected, [], outdir, mode="symlink")
        exported = os.listdir(os.path.join(outdir, "selected"))
        assert len(exported) == 1
        assert os.path.islink(os.path.join(outdir, "selected", exported[0]))

    def test_export_mixed_photos_and_videos(self, tmp_path):
        """Exporting both photos and videos should handle each type correctly."""
        from bpp.output.export import export_selected

        photo_path = _make_photo(tmp_path, "photo.jpg")
        video_path = _make_fake_video(tmp_path, "clip.mp4")
        outdir = str(tmp_path / "export")
        selected = [
            {"filepath": photo_path, "aggregate_score": 0.5},
            {"filepath": video_path, "is_video": True, "aggregate_score": 0.4},
        ]
        export_selected(selected, [], outdir, mode="copy", fmt="jpeg", max_size=800)
        exported = sorted(os.listdir(os.path.join(outdir, "selected")))
        assert len(exported) == 2
        # Photo should be JPEG-processed, video should be raw copy
        assert any(f.endswith(".jpg") for f in exported)
        assert any(f.endswith(".mp4") for f in exported)


# ── V5: Video scoring via frame sampling ──


class TestVideoScoring:
    """Tests for scoring videos by sampling frames."""

    def test_analyze_single_video_returns_scores(self, tmp_path):
        """analyze_single_video should return a result dict with scores."""
        from bpp.scoring.aggregate import analyze_single_video

        video_path = _make_test_video(tmp_path, "clip.mp4", frames=30, size=(128, 96))
        result = analyze_single_video(video_path)
        assert result is not None
        assert "blur_raw" in result
        assert "exposure_score" in result
        assert "face_score" in result
        assert "composition_score" in result
        assert result["filepath"] == video_path
        assert result.get("is_video") == 1

    def test_analyze_single_video_invalid_file(self, tmp_path):
        """analyze_single_video should return None for invalid video."""
        from bpp.scoring.aggregate import analyze_single_video

        bad = str(tmp_path / "bad.mp4")
        with open(bad, "wb") as f:
            f.write(b"\x00" * 512)
        assert analyze_single_video(bad) is None

    def test_analyze_single_video_includes_duration(self, tmp_path):
        """analyze_single_video should include video_duration."""
        from bpp.scoring.aggregate import analyze_single_video

        video_path = _make_test_video(tmp_path, "clip.mp4", frames=20, size=(64, 48))
        result = analyze_single_video(video_path)
        assert result is not None
        assert result["video_duration"] is not None
        assert result["video_duration"] > 0

    def test_process_one_routes_video(self, tmp_path):
        """process_one should route video files to analyze_single_video."""
        from bpp.scoring.aggregate import init_analysis_db, process_one

        db_path = str(tmp_path / "cache.db")
        init_analysis_db(db_path)
        video_path = _make_test_video(tmp_path, "clip.mp4", frames=10, size=(64, 48))
        result = process_one((video_path, 1024, db_path))
        assert result is not None
        assert result.get("is_video") == 1


# ── V6: Video in slideshow ──


class TestVideoSlideshow:
    """Tests that slideshow API supports video items."""

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

    def test_video_endpoint_serves_for_slideshow(self, client, app, tmp_path):
        """Video endpoint should serve video files (used by slideshow)."""
        with app.app_context():
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx
            from bpp.web.thumbnails import ThumbnailCache

            ctx = get_ctx()
            conn = ctx.get_conn()
            path = _make_fake_video(tmp_path, "slide.mp4")
            pid = upsert_photo(conn, {"filepath": path, "is_video": 1})
            if ctx.thumbs is None:
                ctx.thumbs = ThumbnailCache(os.path.join(ctx.ensure_workdir(), "web_thumbs"))
            ctx.thumbs.build_map([{"filepath": path, "id": pid}])
            thumb_hash = ctx.thumbs.get_hash(path)

        resp = client.get(f"/video/{thumb_hash}")
        assert resp.status_code == 200
        assert "video" in resp.content_type


# ── V7: Video trimming ──


class TestVideoTrim:
    """Tests for video trimming functionality."""

    def test_ffmpeg_available_returns_bool(self):
        """ffmpeg_available() should return a boolean."""
        from bpp.utils.video import ffmpeg_available

        result = ffmpeg_available()
        assert isinstance(result, bool)

    def test_trim_video_no_ffmpeg(self, tmp_path):
        """trim_video should return error when ffmpeg is not available."""
        from unittest.mock import patch

        from bpp.utils.video import trim_video

        video_path = _make_fake_video(tmp_path, "clip.mp4")
        out_path = str(tmp_path / "trimmed.mp4")
        with patch("bpp.utils.video.ffmpeg_available", return_value=False):
            result = trim_video(video_path, out_path, start=1.0, end=5.0)
            assert result["ok"] is False
            assert "ffmpeg" in result["error"].lower()

    def test_trim_video_with_mock_ffmpeg(self, tmp_path):
        """trim_video should call ffmpeg with correct arguments."""
        from unittest.mock import patch

        from bpp.utils.video import trim_video

        video_path = _make_fake_video(tmp_path, "clip.mp4")
        out_path = str(tmp_path / "trimmed.mp4")

        with (
            patch("bpp.utils.video.ffmpeg_available", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = type("Result", (), {"returncode": 0})()
            result = trim_video(video_path, out_path, start=2.0, end=8.0)
            assert result["ok"] is True
            # Verify ffmpeg was called with right args
            call_args = mock_run.call_args[0][0]
            assert "ffmpeg" in call_args[0]
            assert "-ss" in call_args
            assert "-to" in call_args

    def test_trim_video_validates_times(self, tmp_path):
        """trim_video should reject invalid time ranges."""
        from bpp.utils.video import trim_video

        video_path = _make_fake_video(tmp_path, "clip.mp4")
        out_path = str(tmp_path / "trimmed.mp4")
        result = trim_video(video_path, out_path, start=5.0, end=2.0)
        assert result["ok"] is False

    def test_trim_api_endpoint(self, tmp_path):
        """The trim API should exist and validate input."""
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir, exist_ok=True)
        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        client = app.test_client()
        resp = client.post(
            "/api/v1/video/trim",
            json={"filepath": "/nonexistent.mp4", "start": 0, "end": 5},
        )
        # Should return 4xx (not 500) — endpoint exists and validates
        # 403 if filepath is outside library boundary
        assert resp.status_code in (400, 403, 404)

    def test_trim_rejects_symlink_escaping_library(self, tmp_path, monkeypatch):
        """TOCTOU defense: even if a path resolves inside the library at
        the initial check, re-resolving immediately before os.replace
        must catch a swap to point outside the library. Regression for
        the path-traversal hardening in bp_media.py:api_video_trim."""
        from unittest.mock import patch as mock_patch

        from bpp.web.app import create_app

        # Library lives in tmp_path/library; escape target lives in
        # tmp_path/escape_target — both real directories so the resolved
        # path comparison can return either value.
        lib = tmp_path / "library"
        photos_dir = lib / "photos"
        photos_dir.mkdir(parents=True)
        escape_target = tmp_path / "escape_target"
        escape_target.mkdir()
        sentinel = escape_target / "sentinel.txt"
        sentinel.write_text("must not be overwritten")

        app = create_app(workdir=str(lib / "data"), library_path=str(lib))
        app.config["TESTING"] = True

        # Real video file inside the library that we'll claim to trim
        real_video = photos_dir / "clip.mp4"
        real_video.write_bytes(b"fake video bytes")

        # Mock ffmpeg + trim_video to "succeed" so we exercise the
        # destructive os.replace path. trim_video would normally write
        # to out_path; we just need the call to return ok=True so the
        # route reaches the replace.
        from bpp.utils import video as video_mod

        def fake_trim(src, dst, *, start, end):
            (tmp_path / "fake_out.mp4").write_bytes(b"trimmed bytes")
            os.replace(str(tmp_path / "fake_out.mp4"), dst)
            return {"ok": True}

        monkeypatch.setattr(video_mod, "ffmpeg_available", lambda: True)
        monkeypatch.setattr("bpp.utils.video.trim_video", fake_trim)

        # Between the initial validation and the final os.replace, swap
        # the file to a symlink pointing at the escape target. The
        # endpoint MUST detect this and refuse.
        original_realpath = os.path.realpath
        call_count = {"n": 0}

        def racing_realpath(p):
            call_count["n"] += 1
            # Second call to realpath on the user-supplied filepath is
            # the pre-replace re-resolve. By that time, we've swapped
            # the path to a symlink escaping the library.
            if (
                call_count["n"] >= 3
                and str(p) == str(real_video)
                and real_video.exists()
                and not real_video.is_symlink()
            ):
                # Replace real_video with a symlink to escape_target/sentinel.txt
                real_video.unlink()
                real_video.symlink_to(sentinel)
            return original_realpath(p)

        client = app.test_client()
        with mock_patch("bpp.web.bp_media.os.path.realpath", side_effect=racing_realpath):
            # Response code itself isn't load-bearing; the sentinel
            # check below is what actually matters.
            client.post(
                "/api/v1/video/trim",
                json={"filepath": str(real_video), "start": 0, "end": 5},
            )

        # Either 403 (caught the swap) or 200 (no race triggered) is
        # acceptable; what we MUST NOT see is the sentinel overwritten.
        assert sentinel.read_text() == "must not be overwritten", (
            "TOCTOU escape: video trim overwrote a file outside the library"
        )
