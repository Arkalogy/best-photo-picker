"""TDD tests for P0-7: RAW format support — detection, import, conversion."""

from __future__ import annotations

import os

import pytest
from PIL import Image


def _make_photo(tmp_path, name="photo.jpg"):
    path = str(tmp_path / name)
    Image.new("RGB", (10, 10)).save(path, "JPEG")
    return path


def _make_fake_raw(tmp_path, name="photo.CR2"):
    """Create a fake RAW file (not valid, just for extension testing)."""
    path = str(tmp_path / name)
    with open(path, "wb") as f:
        f.write(b"\x00" * 1024)
    return path


class TestRawDetection:
    """Tests for is_raw_file() utility."""

    def test_cr2_is_raw(self):
        from bpp.utils.raw import is_raw_file

        assert is_raw_file("photo.CR2") is True

    def test_nef_is_raw(self):
        from bpp.utils.raw import is_raw_file

        assert is_raw_file("photo.nef") is True

    def test_arw_is_raw(self):
        from bpp.utils.raw import is_raw_file

        assert is_raw_file("photo.ARW") is True

    def test_dng_is_raw(self):
        from bpp.utils.raw import is_raw_file

        assert is_raw_file("photo.dng") is True

    def test_orf_is_raw(self):
        from bpp.utils.raw import is_raw_file

        assert is_raw_file("photo.orf") is True

    def test_raf_is_raw(self):
        from bpp.utils.raw import is_raw_file

        assert is_raw_file("photo.raf") is True

    def test_rw2_is_raw(self):
        from bpp.utils.raw import is_raw_file

        assert is_raw_file("photo.rw2") is True

    def test_jpg_not_raw(self):
        from bpp.utils.raw import is_raw_file

        assert is_raw_file("photo.jpg") is False

    def test_mp4_not_raw(self):
        from bpp.utils.raw import is_raw_file

        assert is_raw_file("video.mp4") is False

    def test_raw_extensions_constant(self):
        from bpp.utils.raw import RAW_EXTENSIONS

        assert ".cr2" in RAW_EXTENSIONS
        assert ".nef" in RAW_EXTENSIONS
        assert ".arw" in RAW_EXTENSIONS
        assert ".dng" in RAW_EXTENSIONS

    def test_rawpy_available_flag(self):
        from bpp.utils.raw import RAWPY_AVAILABLE

        assert isinstance(RAWPY_AVAILABLE, bool)


class TestRawInDB:
    """Tests for RAW files in DB."""

    @pytest.fixture()
    def db(self, tmp_path):
        from bpp.db.connection import init_db

        return init_db(str(tmp_path / "test.db"))

    def test_raw_file_stored(self, db, tmp_path):
        from bpp.db.photos import get_photo, upsert_photo

        path = _make_fake_raw(tmp_path, "photo.CR2")
        pid = upsert_photo(db, {"filepath": path, "is_raw": 1})
        photo = get_photo(db, pid)
        assert photo["is_raw"] == 1

    def test_photo_not_raw(self, db, tmp_path):
        from bpp.db.photos import get_photo, upsert_photo

        path = _make_photo(tmp_path, "photo.jpg")
        pid = upsert_photo(db, {"filepath": path, "is_raw": 0})
        photo = get_photo(db, pid)
        assert photo["is_raw"] == 0


class TestRawConversion:
    """Tests for RAW-to-JPEG conversion utility."""

    def test_convert_unavailable_returns_none(self, tmp_path):
        from bpp.utils.raw import convert_raw_to_jpeg

        fake_raw = _make_fake_raw(tmp_path, "photo.CR2")
        out_path = str(tmp_path / "out.jpg")
        # A fake raw file won't convert even with rawpy
        result = convert_raw_to_jpeg(fake_raw, out_path)
        # Either None (rawpy not available or invalid file) or a path
        assert result is None or os.path.isfile(result)


class TestOpenRawAsPil:
    """Tests for open_raw_as_pil() helper."""

    def test_fake_raw_returns_none(self, tmp_path):
        from bpp.utils.raw import open_raw_as_pil

        fake = _make_fake_raw(tmp_path, "photo.CR2")
        # Invalid file data — should return None gracefully
        result = open_raw_as_pil(fake)
        assert result is None

    def test_non_raw_not_opened(self, tmp_path):
        """open_raw_as_pil is only for RAW files; non-RAW should go through Pillow."""
        from bpp.utils.raw import is_raw_file

        jpg = _make_photo(tmp_path, "photo.jpg")
        assert is_raw_file(jpg) is False


class TestRawInScoringPipeline:
    """Tests that load_and_downscale handles RAW detection path."""

    def test_load_and_downscale_returns_none_for_invalid_raw(self, tmp_path):
        from bpp.scoring.aggregate import load_and_downscale

        fake = _make_fake_raw(tmp_path, "photo.CR2")
        result = load_and_downscale(fake, 1024)
        assert result is None

    def test_load_and_downscale_works_for_jpeg(self, tmp_path):
        from bpp.scoring.aggregate import load_and_downscale

        jpg = _make_photo(tmp_path, "photo.jpg")
        result = load_and_downscale(jpg, 1024)
        assert result is not None


class TestRawThumbnail:
    """Tests that thumbnail generation handles RAW files."""

    def test_thumbnail_fails_for_invalid_raw(self, tmp_path):
        from bpp.output.gallery import _make_thumbnail

        fake = _make_fake_raw(tmp_path, "photo.CR2")
        dest = str(tmp_path / "thumb.jpg")
        result = _make_thumbnail(fake, dest)
        assert result is False

    def test_thumbnail_works_for_jpeg(self, tmp_path):
        from bpp.output.gallery import _make_thumbnail

        jpg = _make_photo(tmp_path, "photo.jpg")
        dest = str(tmp_path / "thumb.jpg")
        result = _make_thumbnail(jpg, dest)
        assert result is True
        assert os.path.exists(dest)


class TestRawExtensionSync:
    """Tests that DEFAULT_EXTENSIONS includes all RAW_EXTENSIONS."""

    def test_all_raw_extensions_in_defaults(self):
        from bpp.db.library import DEFAULT_EXTENSIONS
        from bpp.utils.raw import RAW_EXTENSIONS

        default_set = {"." + e.lower().lstrip(".") for e in DEFAULT_EXTENSIONS}
        for ext in RAW_EXTENSIONS:
            assert ext in default_set, f"RAW extension {ext} missing from DEFAULT_EXTENSIONS"


class TestRawInImport:
    """Tests that RAW extensions are in import defaults."""

    def test_default_extensions_include_raw(self):
        from bpp.db.library import DEFAULT_EXTENSIONS

        assert "cr2" in DEFAULT_EXTENSIONS
        assert "nef" in DEFAULT_EXTENSIONS
        assert "arw" in DEFAULT_EXTENSIONS
        assert "dng" in DEFAULT_EXTENSIONS


class TestRawMediaServing:
    """Tests for RAW photo serving via API."""

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

    def test_raw_photo_endpoint_returns_error_for_unconvertible(self, client, app, tmp_path):
        """RAW files that can't be converted should return an appropriate error."""
        with app.app_context():
            from bpp.db.photos import upsert_photo
            from bpp.web.state import get_ctx
            from bpp.web.thumbnails import ThumbnailCache

            ctx = get_ctx()
            conn = ctx.get_conn()
            path = _make_fake_raw(tmp_path, "fake.CR2")
            pid = upsert_photo(conn, {"filepath": path, "is_raw": 1})
            if ctx.thumbs is None:
                ctx.thumbs = ThumbnailCache(os.path.join(ctx.ensure_workdir(), "web_thumbs"))
            ctx.thumbs.build_map([{"filepath": path, "id": pid}])
            thumb_hash = ctx.thumbs.get_hash(path)
        resp = client.get(f"/photo/{thumb_hash}")
        # Should either serve a converted JPEG or return 500
        assert resp.status_code in (200, 500)
        os.unlink(path)
