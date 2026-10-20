"""TDD tests for Video support — detection, thumbnails, playback API — part 2 of 2:
TestVideoMetadataDB, TestVideoExport, TestVideoScoring."""

from __future__ import annotations

import os

import pytest

from tests._models import skip_unless_scoring_models
from tests._video_shared import _make_fake_video, _make_photo, _make_test_video

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
    """Tests for scoring videos by sampling frames (the real scoring
    pipeline: skipped where its weights aren't on disk)."""

    @pytest.fixture(autouse=True)
    def _real_models_on_disk(self):
        skip_unless_scoring_models()

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
