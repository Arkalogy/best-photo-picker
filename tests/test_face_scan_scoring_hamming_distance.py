"""Comprehensive tests for face scoring, face embedding, face clustering, — part 3 of 3:
TestHammingDistance, TestDualHashDistance, TestLoadImage."""

from __future__ import annotations

import datetime
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from PIL import Image

from bpp.dedupe.phash import (
    _load_image,
    compute_dhash,
    compute_dhash_from_file,
    compute_hashes_from_file,
    dual_hash_distance,
    hamming_distance,
)
from bpp.exif_utils import extract_exif_date, get_date, get_file_date
from tests._face_scan_scoring_shared import (
    _blank_bgr,
    _random_bgr,
    _save_jpeg,
    _save_png,
)


class TestHammingDistance:
    """Tests for hamming_distance."""

    def test_zero_distance(self):
        assert hamming_distance(0xABCD, 0xABCD) == 0

    def test_known_values(self):
        assert hamming_distance(0b1111, 0b0000) == 4
        assert hamming_distance(0b1000, 0b0000) == 1

    def test_symmetric(self):
        a, b = 0x1234, 0x5678
        assert hamming_distance(a, b) == hamming_distance(b, a)

    def test_max_distance_8bit(self):
        assert hamming_distance(0b11111111, 0b00000000) == 8

    def test_large_hashes(self):
        h = hamming_distance(0xFFFFFFFFFFFFFFFF, 0x0)
        assert h == 64


class TestDualHashDistance:
    """Tests for dual_hash_distance."""

    def test_identical(self):
        assert dual_hash_distance(10, 20, 10, 20) == 0

    def test_takes_minimum(self):
        d1 = hamming_distance(0xFF, 0x00)  # 8
        d2 = hamming_distance(0x01, 0x00)  # 1
        result = dual_hash_distance(0xFF, 0x01, 0x00, 0x00)
        assert result == min(d1, d2)

    def test_symmetric(self):
        assert dual_hash_distance(0xAA, 0xBB, 0xCC, 0xDD) == dual_hash_distance(
            0xCC, 0xDD, 0xAA, 0xBB
        )


class TestLoadImage:
    """Tests for _load_image."""

    def test_loads_jpeg(self, tmp_path):
        p = tmp_path / "test.jpg"
        _save_jpeg(p)
        img = _load_image(str(p))
        assert img is not None
        assert len(img.shape) == 2  # grayscale

    def test_loads_png(self, tmp_path):
        p = tmp_path / "test.png"
        _save_png(p)
        img = _load_image(str(p))
        assert img is not None

    def test_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(OSError):
            _load_image(str(tmp_path / "nope.jpg"))

    def test_corrupt_file_falls_back_to_pil(self, tmp_path):
        """If cv2 cannot read the file, _load_image falls back to PIL."""
        p = tmp_path / "test.jpg"
        _save_jpeg(p)
        with patch("cv2.imread", return_value=None):
            img = _load_image(str(p))
        assert img is not None
        assert len(img.shape) == 2


class TestComputeHashesFromFile:
    """Tests for compute_hashes_from_file."""

    def test_returns_tuple(self, tmp_path):
        p = tmp_path / "img.jpg"
        _save_jpeg(p, color="green")
        result = compute_hashes_from_file(str(p))
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_both_ints(self, tmp_path):
        p = tmp_path / "img.jpg"
        _save_jpeg(p, color="green")
        dhash, ahash = compute_hashes_from_file(str(p))
        assert isinstance(dhash, int)
        assert isinstance(ahash, int)

    def test_corrupt_returns_none_none(self, tmp_path):
        p = tmp_path / "bad.jpg"
        p.write_bytes(b"\x00\x00\x00")
        dhash, ahash = compute_hashes_from_file(str(p))
        assert dhash is None
        assert ahash is None


class TestComputeDhashFromFile:
    """Tests for compute_dhash_from_file."""

    def test_returns_int(self, tmp_path):
        p = tmp_path / "img.jpg"
        _save_jpeg(p)
        h = compute_dhash_from_file(str(p))
        assert isinstance(h, int)

    def test_corrupt_returns_none(self, tmp_path):
        p = tmp_path / "bad.jpg"
        p.write_bytes(b"\xff\xd8\xff")
        # Should not raise, just return None
        h = compute_dhash_from_file(str(p))
        assert h is None


class TestPhashSimilarity:
    """Integration: verify similar images hash close, different hash far."""

    def test_blurred_copy_close(self, tmp_path):
        img = _random_bgr(seed=10, h=200, w=200)
        blurred = cv2.GaussianBlur(img, (3, 3), 1)
        d1 = compute_dhash(img)
        d2 = compute_dhash(blurred)
        assert hamming_distance(d1, d2) < 15

    def test_color_shift_close(self):
        img = _random_bgr(seed=20, h=200, w=200)
        shifted = np.clip(img.astype(int) + 10, 0, 255).astype(np.uint8)
        d1 = compute_dhash(img)
        d2 = compute_dhash(shifted)
        assert hamming_distance(d1, d2) < 10

    def test_very_different_images_far(self):
        d1 = compute_dhash(_blank_bgr(200, 200, (0, 0, 0)))
        d2 = compute_dhash(_random_bgr(seed=77, h=200, w=200))
        # Black vs random noise should differ significantly
        assert hamming_distance(d1, d2) > 5


# ===================================================================
# EXIF DATE EXTRACTION  (bpp/exif_utils.py)
# ===================================================================


class TestExtractExifDate:
    """Tests for extract_exif_date."""

    def test_no_exif_returns_none(self, tmp_path):
        p = tmp_path / "plain.jpg"
        _save_jpeg(p)
        result = extract_exif_date(str(p))
        # Plain PIL-saved JPEG has no EXIF
        assert result is None

    def test_with_exif_date(self, tmp_path):
        """Create a JPEG with EXIF DateTimeOriginal and verify extraction."""
        from PIL.ExifTags import Base as ExifBase

        p = tmp_path / "dated.jpg"
        img = Image.new("RGB", (100, 100), "green")
        exif = img.getexif()
        exif[ExifBase.DateTimeOriginal] = "2024:06:15 14:30:00"
        img.save(str(p), "JPEG", exif=exif.tobytes())

        result = extract_exif_date(str(p))
        assert result is not None
        assert result.year == 2024
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 14
        assert result.minute == 30

    def test_with_datetime_fallback(self, tmp_path):
        """Falls back to DateTime when DateTimeOriginal is absent."""
        from PIL.ExifTags import Base as ExifBase

        p = tmp_path / "datetime.jpg"
        img = Image.new("RGB", (100, 100), "red")
        exif = img.getexif()
        exif[ExifBase.DateTime] = "2023:01:20 08:00:00"
        img.save(str(p), "JPEG", exif=exif.tobytes())

        result = extract_exif_date(str(p))
        assert result is not None
        assert result.year == 2023
        assert result.month == 1

    def test_corrupt_file_returns_none(self, tmp_path):
        p = tmp_path / "corrupt.jpg"
        p.write_bytes(b"\x00\x01\x02\x03")
        result = extract_exif_date(str(p))
        assert result is None

    def test_nonexistent_file_returns_none(self, tmp_path):
        result = extract_exif_date(str(tmp_path / "ghost.jpg"))
        assert result is None


class TestGetFileDate:
    """Tests for get_file_date."""

    def test_returns_datetime(self, tmp_path):
        p = tmp_path / "img.jpg"
        _save_jpeg(p)
        result = get_file_date(str(p))
        assert isinstance(result, datetime.datetime)

    def test_recent_file(self, tmp_path):
        p = tmp_path / "img.jpg"
        _save_jpeg(p)
        result = get_file_date(str(p))
        now = datetime.datetime.now()
        # Should be within the last minute
        assert (now - result).total_seconds() < 60


class TestGetDate:
    """Tests for get_date (EXIF with mtime fallback)."""

    def test_no_exif_falls_back_to_mtime(self, tmp_path):
        p = tmp_path / "plain.jpg"
        _save_jpeg(p)
        result = get_date(str(p))
        assert isinstance(result, datetime.datetime)

    def test_exif_preferred_over_mtime(self, tmp_path):
        from PIL.ExifTags import Base as ExifBase

        p = tmp_path / "exif.jpg"
        img = Image.new("RGB", (100, 100))
        exif = img.getexif()
        exif[ExifBase.DateTimeOriginal] = "2020:03:14 12:00:00"
        img.save(str(p), "JPEG", exif=exif.tobytes())

        result = get_date(str(p))
        assert result.year == 2020
        assert result.month == 3


# ===================================================================
# PHASH RETRY / TRANSIENT ERROR HANDLING
# ===================================================================


class TestLoadImageRetry:
    """Tests for _load_image retry logic on transient errors."""

    def test_retry_on_transient_error(self, tmp_path):
        """_load_image retries when is_transient returns True."""
        p = tmp_path / "test.jpg"
        _save_jpeg(p)

        call_count = 0
        original_imread = cv2.imread

        def flaky_imread(path, flags):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError(5, "I/O error")  # errno 5 = EIO
            return original_imread(path, flags)

        with patch("cv2.imread", side_effect=flaky_imread), patch("time.sleep"):
            img = _load_image(str(p))
        assert img is not None
        assert call_count == 2

    def test_non_transient_error_not_retried(self, tmp_path):
        """Non-transient OSError should be raised immediately."""
        p = tmp_path / "test.jpg"
        _save_jpeg(p)

        def always_fail(path, flags):
            raise OSError(2, "No such file or directory")

        with (
            patch("cv2.imread", side_effect=always_fail),
            pytest.raises(OSError, match="No such file"),
        ):
            _load_image(str(p))
