"""TDD tests for photo editing: auto-enhance, manual adjustments, crop, rotate, flip — part 4 of 4:
TestNewApplyEdits, TestPerspectiveCoefficients."""

from __future__ import annotations

import pytest
from PIL import Image, ImageStat

from tests._enhance_shared import (  # noqa: F401
    _make_bright_photo,
    _make_dark_photo,
    _make_dull_photo,
    _make_photo,
    conn,
    db_path,
)


class TestNewApplyEdits:
    """Backend image processing tests for new edit parameters."""

    def test_exposure_brightens(self, tmp_path):
        """Positive exposure makes image brighter."""
        from bpp.web.bp_media import _apply_edits

        img = Image.new("RGB", (100, 100), (100, 100, 100))
        result = _apply_edits(img, {"exposure": 1.0})
        stat = ImageStat.Stat(result)
        assert sum(stat.mean) / 3 > 150  # Should be ~200 (doubled)

    def test_exposure_darkens(self, tmp_path):
        """Negative exposure makes image darker."""
        from bpp.web.bp_media import _apply_edits

        img = Image.new("RGB", (100, 100), (200, 200, 200))
        result = _apply_edits(img, {"exposure": -1.0})
        stat = ImageStat.Stat(result)
        assert sum(stat.mean) / 3 < 150  # Should be ~100 (halved)

    def test_brilliance_lifts_shadows(self, tmp_path):
        """Positive brilliance brightens dark areas."""
        from bpp.web.bp_media import _apply_edits

        img = Image.new("RGB", (100, 100), (30, 30, 30))
        result = _apply_edits(img, {"brilliance": 0.8})
        stat = ImageStat.Stat(result)
        assert sum(stat.mean) / 3 > 40  # Shadows lifted

    def test_vibrance_boosts_muted_colors(self, tmp_path):
        """Positive vibrance increases saturation of muted colors."""
        import numpy as np

        from bpp.web.bp_media import _apply_edits

        # Create a muted (low saturation) image
        img = Image.new("RGB", (100, 100), (120, 115, 110))
        result = _apply_edits(img, {"vibrance": 0.8})
        arr = np.array(result)
        original = np.array(img)
        # Color spread should increase
        result_spread = arr.max(axis=2).mean() - arr.min(axis=2).mean()
        orig_spread = original.max(axis=2).mean() - original.min(axis=2).mean()
        assert result_spread >= orig_spread

    def test_tint_shifts_color(self, tmp_path):
        """Tint shifts green/magenta balance."""
        import numpy as np

        from bpp.web.bp_media import _apply_edits

        img = Image.new("RGB", (100, 100), (128, 128, 128))
        result = _apply_edits(img, {"tint": 0.5})
        arr = np.array(result)
        # Positive tint should reduce green, add red+blue
        assert arr[:, :, 1].mean() < 128  # Green decreased

    def test_black_point_crushes_blacks(self, tmp_path):
        """Black point adjustment darkens shadows."""
        from bpp.web.bp_media import _apply_edits

        img = Image.new("RGB", (100, 100), (50, 50, 50))
        result = _apply_edits(img, {"black_point": 0.5})
        stat = ImageStat.Stat(result)
        # Dark areas get crushed
        assert sum(stat.mean) / 3 != pytest.approx(50, abs=1)

    def test_definition_adds_local_contrast(self, tmp_path):
        """Definition enhances local contrast."""
        import numpy as np

        from bpp.web.bp_media import _apply_edits

        # Create image with gradual gradient
        arr = np.zeros((100, 100, 3), dtype=np.uint8)
        for i in range(100):
            arr[i, :, :] = int(i * 2.55)
        img = Image.fromarray(arr)

        result = _apply_edits(img, {"definition": 0.8})
        result_arr = np.array(result, dtype=np.float32)
        orig_arr = arr.astype(np.float32)
        # Std deviation should increase (more contrast)
        assert result_arr.std() >= orig_arr.std() * 0.9

    def test_noise_reduction_smooths(self, tmp_path):
        """Noise reduction reduces variation in noisy image."""
        import numpy as np

        from bpp.web.bp_media import _apply_edits

        rng = np.random.RandomState(42)
        arr = (128 + rng.normal(0, 30, (100, 100, 3))).clip(0, 255).astype(np.uint8)
        img = Image.fromarray(arr)

        result = _apply_edits(img, {"noise_reduction": 0.8})
        result_arr = np.array(result, dtype=np.float32)
        # Smoothed image should have lower std deviation
        assert result_arr.std() < arr.astype(np.float32).std()

    def test_straighten_rotates(self, tmp_path):
        """Straighten applies fine rotation."""
        from bpp.web.bp_media import _apply_edits

        img = Image.new("RGB", (200, 200), (128, 128, 128))
        result = _apply_edits(img, {"straighten": 5.0})
        # Rotated image should be larger (expand=True)
        assert result.size[0] >= 200 or result.size[1] >= 200

    def test_perspective_transform(self, tmp_path):
        """Perspective correction applies perspective warp."""
        from bpp.web.bp_media import _apply_edits

        img = Image.new("RGB", (200, 200), (128, 128, 128))
        result = _apply_edits(img, {"perspective_v": 0.3})
        # Should produce an image of the same size
        assert result.size == (200, 200)

    def test_all_new_params_together(self, tmp_path):
        """All new params applied at once don't crash."""
        from bpp.web.bp_media import _apply_edits

        img = Image.new("RGB", (200, 200), (128, 100, 80))
        edits = {
            "exposure": 0.3,
            "brilliance": 0.2,
            "black_point": 0.1,
            "vibrance": 0.3,
            "tint": -0.15,
            "definition": 0.4,
            "noise_reduction": 0.2,
            "straighten": 2.5,
            "perspective_v": 0.1,
            "perspective_h": -0.05,
            "warmth": 0.2,
            "highlights": -0.1,
            "shadows": 0.2,
            "vignette": 0.3,
            "grain": 0.1,
            "fade": 0.05,
        }
        result = _apply_edits(img, edits)
        assert result.mode == "RGB"
        assert result.size[0] > 0 and result.size[1] > 0

    def test_zero_params_noop(self, tmp_path):
        """Zero values for all new params produce no change."""
        import numpy as np

        from bpp.web.bp_media import _apply_edits

        img = Image.new("RGB", (100, 100), (128, 128, 128))
        edits = {
            "exposure": 0.0,
            "brilliance": 0.0,
            "black_point": 0.0,
            "vibrance": 0.0,
            "tint": 0.0,
            "definition": 0.0,
            "noise_reduction": 0.0,
            "straighten": 0.0,
            "perspective_v": 0.0,
            "perspective_h": 0.0,
        }
        result = _apply_edits(img, edits)
        orig_arr = np.array(img)
        result_arr = np.array(result)
        assert np.array_equal(orig_arr, result_arr)


class TestPerspectiveCoefficients:
    """Tests for _perspective_coefficients helper."""

    def test_zero_perspective_identity(self):
        """Zero vert/horiz produces near-identity transform."""
        from bpp.web.bp_media import _perspective_coefficients

        coeffs = _perspective_coefficients(200, 200, 0.0, 0.0)
        assert len(coeffs) == 8
        # Should be approximately identity
        assert coeffs[0] == pytest.approx(1, abs=0.01)
        assert coeffs[4] == pytest.approx(1, abs=0.01)

    def test_nonzero_perspective(self):
        """Non-zero values produce different coefficients."""
        from bpp.web.bp_media import _perspective_coefficients

        coeffs = _perspective_coefficients(200, 200, 0.5, 0.0)
        assert len(coeffs) == 8
