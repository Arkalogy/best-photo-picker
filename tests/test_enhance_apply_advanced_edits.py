"""TDD tests for photo editing: auto-enhance, manual adjustments, crop, rotate, flip — part 3 of 4:
TestApplyAdvancedEdits, TestSaveAdvancedEditsAPI, TestNewEditorParamsDB."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import json
import os

import pytest
from PIL import Image

from tests._enhance_shared import (  # noqa: F401
    _make_bright_photo,
    _make_dark_photo,
    _make_dull_photo,
    _make_photo,
    conn,
    db_path,
)

# ---------------------------------------------------------------------------
# Apply edits v2: warmth, highlights, shadows, vignette, grain, fade
# ---------------------------------------------------------------------------


class TestApplyAdvancedEdits:
    """Tests for applying warmth, highlights, shadows, vignette, grain, fade to images."""

    def test_warmth_positive_shifts_warm(self, tmp_path):
        """Positive warmth should increase red channel relative to blue."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (100, 100), (128, 128, 128))
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            result = _apply_edits(src, {"warmth": 0.8})
            px = result.getpixel((50, 50))
            # Red should be boosted, blue reduced
            assert px[0] > px[2]

    def test_warmth_negative_shifts_cool(self, tmp_path):
        """Negative warmth should increase blue relative to red."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (100, 100), (128, 128, 128))
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            result = _apply_edits(src, {"warmth": -0.8})
            px = result.getpixel((50, 50))
            assert px[2] > px[0]

    def test_highlights_positive_brightens_brights(self, tmp_path):
        """Positive highlights should brighten the bright regions."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (100, 100), (200, 200, 200))
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            orig_px = src.getpixel((50, 50))
            result = _apply_edits(src, {"highlights": 0.8})
            new_px = result.getpixel((50, 50))
            # Bright pixels should get brighter
            assert sum(new_px) > sum(orig_px)

    def test_shadows_positive_brightens_darks(self, tmp_path):
        """Positive shadows should brighten the dark regions."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (100, 100), (30, 30, 30))
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            orig_px = src.getpixel((50, 50))
            result = _apply_edits(src, {"shadows": 0.8})
            new_px = result.getpixel((50, 50))
            assert sum(new_px) > sum(orig_px)

    def test_vignette_darkens_edges(self, tmp_path):
        """Vignette should darken edges more than center."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (200, 200), (180, 180, 180))
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            result = _apply_edits(src, {"vignette": 0.8})
            center_px = result.getpixel((100, 100))
            corner_px = result.getpixel((5, 5))
            # Center should be brighter than corner
            assert sum(center_px) > sum(corner_px)

    def test_grain_adds_noise(self, tmp_path):
        """Grain should add visible noise (pixel variation)."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (100, 100), (128, 128, 128))
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            result = _apply_edits(src, {"grain": 0.5})
            # Check that pixels aren't all the same anymore
            pixels = set()
            for y in range(0, 100, 10):
                for x in range(0, 100, 10):
                    pixels.add(result.getpixel((x, y)))
            # With grain, there should be variation (more than ~3 JPEG-compression values)
            assert len(pixels) > 5

    def test_fade_lifts_blacks(self, tmp_path):
        """Fade should lift the black point (darkest pixels become lighter)."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (100, 100), (0, 0, 0))
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            result = _apply_edits(src, {"fade": 0.5})
            px = result.getpixel((50, 50))
            # Pure black should now be lifted
            assert sum(px) > 0

    def test_redeye_desaturates_red_pixels(self, tmp_path):
        """Red-eye fix should desaturate red pixels near fix points."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        # Create image with a red spot at center
        img = Image.new("RGB", (100, 100), (60, 60, 60))
        for x in range(40, 60):
            for y in range(40, 60):
                img.putpixel((x, y), (220, 30, 30))
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            points = [{"x": 0.5, "y": 0.5, "radius": 0.15}]
            result = _apply_edits(src, {"redeye_points": points})
            px = result.getpixel((50, 50))
            # Red channel should be reduced significantly
            assert px[0] < 150

    def test_zero_params_identity(self, tmp_path):
        """All new params at 0 should produce no change."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (100, 100), (128, 128, 128))
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            result = _apply_edits(
                src,
                {
                    "warmth": 0.0,
                    "highlights": 0.0,
                    "shadows": 0.0,
                    "vignette": 0.0,
                    "grain": 0.0,
                    "fade": 0.0,
                },
            )
            orig_px = src.getpixel((50, 50))
            new_px = result.getpixel((50, 50))
            for o, n in zip(orig_px, new_px, strict=True):
                assert abs(o - n) < 3  # JPEG compression tolerance


# ---------------------------------------------------------------------------
# API: save-edits with new params
# ---------------------------------------------------------------------------


class TestSaveAdvancedEditsAPI:
    """Tests for saving warmth/highlights/shadows/vignette/grain/fade via API."""

    @pytest.fixture
    def client(self, tmp_path):
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)

        analysis = []
        for i in range(2):
            fpath = str(tmp_path / f"img_{i}.jpg")
            img = Image.new("RGB", (200, 200), (100 + i * 30, 80 + i * 20, 60 + i * 10))
            img.save(fpath, "JPEG")
            analysis.append(
                {
                    "filepath": fpath,
                    "original_filename": f"img_{i}.jpg",
                    "date": f"2024-01-{i + 1:02d}T12:00:00",
                    "date_day": f"2024-01-{i + 1:02d}",
                    "date_month": "2024-01",
                    "file_size": 1024,
                    "file_mtime": 1700000000.0 + i,
                    "blur_raw": 100.0,
                    "blur_score": 0.5,
                    "exposure_score": 0.5,
                    "face_score": 0.3,
                    "face_count": 0,
                    "largest_face_ratio": 0.0,
                    "face_center_dist": 0.0,
                    "composition_score": 0.5,
                    "aggregate_score": 0.5,
                }
            )

        import json as json_mod

        with open(os.path.join(workdir, "analysis.json"), "w") as f:
            json_mod.dump(analysis, f)

        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        client = app.test_client()
        client.get("/api/v1/photos")
        return client

    def test_save_warmth_via_api(self, client, tmp_path):
        """POST /api/photos/save-edits stores warmth."""
        filepath = str(tmp_path / "img_0.jpg")
        resp = client.post(
            "/api/v1/photos/save-edits",
            data=json.dumps({"filepath": filepath, "edits": {"warmth": 0.6}}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get(f"/api/v1/photos/edits?filepath={filepath}")
        edits = resp2.get_json()["edits"]
        assert edits["warmth"] == pytest.approx(0.6)

    def test_save_vignette_via_api(self, client, tmp_path):
        """POST /api/photos/save-edits stores vignette."""
        filepath = str(tmp_path / "img_0.jpg")
        resp = client.post(
            "/api/v1/photos/save-edits",
            data=json.dumps({"filepath": filepath, "edits": {"vignette": 0.4}}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get(f"/api/v1/photos/edits?filepath={filepath}")
        edits = resp2.get_json()["edits"]
        assert edits["vignette"] == pytest.approx(0.4)

    def test_save_all_new_params_via_api(self, client, tmp_path):
        """All new edit params save and retrieve correctly via API."""
        filepath = str(tmp_path / "img_0.jpg")
        edits = {
            "warmth": 0.3,
            "highlights": -0.2,
            "shadows": 0.4,
            "vignette": 0.5,
            "grain": 0.1,
            "fade": 0.15,
            "filter_name": "Vintage",
        }
        resp = client.post(
            "/api/v1/photos/save-edits",
            data=json.dumps({"filepath": filepath, "edits": edits}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get(f"/api/v1/photos/edits?filepath={filepath}")
        result = resp2.get_json()["edits"]
        assert result["warmth"] == pytest.approx(0.3)
        assert result["highlights"] == pytest.approx(-0.2)
        assert result["shadows"] == pytest.approx(0.4)
        assert result["vignette"] == pytest.approx(0.5)
        assert result["grain"] == pytest.approx(0.1)
        assert result["fade"] == pytest.approx(0.15)
        assert result["filter_name"] == "Vintage"

    def test_save_redeye_via_api(self, client, tmp_path):
        """POST /api/photos/save-edits stores red-eye fix points."""
        filepath = str(tmp_path / "img_0.jpg")
        points = [{"x": 0.3, "y": 0.4, "radius": 0.02}]
        resp = client.post(
            "/api/v1/photos/save-edits",
            data=json.dumps({"filepath": filepath, "edits": {"redeye_points": points}}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get(f"/api/v1/photos/edits?filepath={filepath}")
        result = resp2.get_json()["edits"]
        assert result["redeye_points"] == points

    def test_has_changes_with_new_params(self, client, tmp_path):
        """New params with non-default values should count as 'has changes'."""
        filepath = str(tmp_path / "img_0.jpg")
        client.post(
            "/api/v1/photos/save-edits",
            data=json.dumps({"filepath": filepath, "edits": {"warmth": 0.3}}),
            content_type="application/json",
        )
        resp = client.get("/api/v1/photos")
        photos = resp.get_json()["photos"]
        match = [p for p in photos if p["filepath"] == filepath]
        assert match[0]["_enhanced"] is True


# ---------------------------------------------------------------------------
# New editor params tests (exposure, brilliance, black_point, vibrance,
# tint, definition, noise_reduction, straighten, perspective)
# ---------------------------------------------------------------------------


class TestNewEditorParamsDB:
    """DB CRUD tests for new editor parameters."""

    def test_save_and_get_new_params(self, conn, tmp_path):
        """New params round-trip through save/get correctly."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "new.jpg")
        edits = {
            "exposure": 0.5,
            "brilliance": 0.3,
            "black_point": -0.2,
            "vibrance": 0.4,
            "tint": -0.1,
            "definition": 0.6,
            "noise_reduction": 0.3,
            "straighten": 5.0,
            "perspective_v": 0.15,
            "perspective_h": -0.1,
        }
        save_photo_edits(conn, pid, edits)

        result = get_photo_edits(conn, pid)
        assert result["exposure"] == pytest.approx(0.5)
        assert result["brilliance"] == pytest.approx(0.3)
        assert result["black_point"] == pytest.approx(-0.2)
        assert result["vibrance"] == pytest.approx(0.4)
        assert result["tint"] == pytest.approx(-0.1)
        assert result["definition"] == pytest.approx(0.6)
        assert result["noise_reduction"] == pytest.approx(0.3)
        assert result["straighten"] == pytest.approx(5.0)
        assert result["perspective_v"] == pytest.approx(0.15)
        assert result["perspective_h"] == pytest.approx(-0.1)

    def test_new_params_default_zero(self, conn, tmp_path):
        """New params default to 0.0 when not set."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "defaults.jpg")
        save_photo_edits(conn, pid, {"brightness": 1.2})

        result = get_photo_edits(conn, pid)
        assert result["exposure"] == pytest.approx(0.0)
        assert result["brilliance"] == pytest.approx(0.0)
        assert result["black_point"] == pytest.approx(0.0)
        assert result["vibrance"] == pytest.approx(0.0)
        assert result["tint"] == pytest.approx(0.0)
        assert result["definition"] == pytest.approx(0.0)
        assert result["noise_reduction"] == pytest.approx(0.0)
        assert result["straighten"] == pytest.approx(0.0)
        assert result["perspective_v"] == pytest.approx(0.0)
        assert result["perspective_h"] == pytest.approx(0.0)

    def test_upsert_new_params(self, conn, tmp_path):
        """Upserting preserves new params correctly."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "upsert.jpg")
        save_photo_edits(conn, pid, {"exposure": 0.5})
        save_photo_edits(conn, pid, {"exposure": -0.3, "vibrance": 0.7})

        result = get_photo_edits(conn, pid)
        assert result["exposure"] == pytest.approx(-0.3)
        assert result["vibrance"] == pytest.approx(0.7)
