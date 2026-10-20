"""TDD tests for photo editing: auto-enhance, manual adjustments, crop, rotate, flip — part 2 of 4:
TestApplyEdits, TestSaveEditsAPI, TestAdvancedEditsDB."""

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
# Apply edits: crop, rotation, flip on image
# ---------------------------------------------------------------------------


class TestApplyEdits:
    """Tests for applying crop/rotation/flip to images."""

    def test_crop_changes_dimensions(self, tmp_path):
        """Cropping should produce a smaller image."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (400, 300), "blue")
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            edits = {"crop_x": 0.25, "crop_y": 0.25, "crop_w": 0.5, "crop_h": 0.5}
            result = _apply_edits(src, edits)
            # 50% of 400x300 = 200x150
            assert result.size == (200, 150)

    def test_rotation_90(self, tmp_path):
        """90° rotation swaps width and height."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (400, 200), "red")
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            result = _apply_edits(src, {"rotation": 90})
            assert result.size == (200, 400)

    def test_rotation_180(self, tmp_path):
        """180° rotation preserves dimensions."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (400, 200), "red")
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            result = _apply_edits(src, {"rotation": 180})
            assert result.size == (400, 200)

    def test_flip_horizontal(self, tmp_path):
        """Horizontal flip preserves dimensions but mirrors content."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        # Left half red, right half blue
        img = Image.new("RGB", (100, 50), "red")
        right = Image.new("RGB", (50, 50), "blue")
        img.paste(right, (50, 0))
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            result = _apply_edits(src, {"flip_h": True})
            assert result.size == (100, 50)
            # After flip, left side should be blue-ish, right side red-ish
            left_px = result.getpixel((10, 25))
            right_px = result.getpixel((90, 25))
            assert left_px[2] > left_px[0]  # blue > red on left (was right)
            assert right_px[0] > right_px[2]  # red > blue on right (was left)

    def test_flip_vertical(self, tmp_path):
        """Vertical flip preserves dimensions."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (100, 100), "green")
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            result = _apply_edits(src, {"flip_v": True})
            assert result.size == (100, 100)

    def test_crop_then_rotate(self, tmp_path):
        """Crop is applied before rotation."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (400, 200), "red")
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            edits = {"crop_x": 0.0, "crop_y": 0.0, "crop_w": 0.5, "crop_h": 1.0, "rotation": 90}
            result = _apply_edits(src, edits)
            # Crop to 200x200, then rotate 90 -> 200x200
            assert result.size == (200, 200)

    def test_all_transforms_combined(self, tmp_path):
        """All edit types applied together in correct order."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (400, 300), (100, 100, 100))
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            edits = {
                "brightness": 1.5,
                "contrast": 1.2,
                "saturation": 1.3,
                "sharpness": 1.1,
                "crop_x": 0.0,
                "crop_y": 0.0,
                "crop_w": 0.5,
                "crop_h": 0.5,
                "rotation": 0,
                "flip_h": False,
                "flip_v": False,
            }
            result = _apply_edits(src, edits)
            # Cropped to 200x150
            assert result.size == (200, 150)

    def test_identity_edits_no_change(self, tmp_path):
        """Default params produce no visible change."""
        from bpp.web.bp_media import _apply_edits

        f = tmp_path / "img.jpg"
        img = Image.new("RGB", (200, 200), (128, 128, 128))
        img.save(str(f), "JPEG")

        with Image.open(str(f)) as src:
            result = _apply_edits(
                src,
                {
                    "brightness": 1.0,
                    "contrast": 1.0,
                    "saturation": 1.0,
                    "sharpness": 1.0,
                },
            )
            assert result.size == (200, 200)


# ---------------------------------------------------------------------------
# API: save manual edits
# ---------------------------------------------------------------------------


class TestSaveEditsAPI:
    """Tests for the POST /api/photos/save-edits endpoint."""

    @pytest.fixture
    def client(self, tmp_path):
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)

        analysis = []
        for i in range(3):
            fpath = str(tmp_path / f"img_{i}.jpg")
            img = Image.new("RGB", (400, 300), (100 + i * 30, 80 + i * 20, 60 + i * 10))
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

    def test_save_manual_sliders(self, client, tmp_path):
        """POST /api/photos/save-edits stores manual slider values."""
        filepath = str(tmp_path / "img_0.jpg")
        resp = client.post(
            "/api/v1/photos/save-edits",
            data=json.dumps(
                {
                    "filepath": filepath,
                    "edits": {
                        "brightness": 1.3,
                        "contrast": 1.1,
                        "saturation": 0.9,
                        "sharpness": 1.2,
                    },
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

        # Verify via get
        resp2 = client.get(f"/api/v1/photos/edits?filepath={filepath}")
        edits = resp2.get_json()["edits"]
        assert edits["brightness"] == pytest.approx(1.3)

    def test_save_crop(self, client, tmp_path):
        """POST /api/photos/save-edits stores crop params."""
        filepath = str(tmp_path / "img_0.jpg")
        resp = client.post(
            "/api/v1/photos/save-edits",
            data=json.dumps(
                {
                    "filepath": filepath,
                    "edits": {"crop_x": 0.1, "crop_y": 0.2, "crop_w": 0.8, "crop_h": 0.6},
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get(f"/api/v1/photos/edits?filepath={filepath}")
        edits = resp2.get_json()["edits"]
        assert edits["crop_x"] == pytest.approx(0.1)
        assert edits["crop_w"] == pytest.approx(0.8)

    def test_save_rotation(self, client, tmp_path):
        """POST /api/photos/save-edits stores rotation."""
        filepath = str(tmp_path / "img_0.jpg")
        resp = client.post(
            "/api/v1/photos/save-edits",
            data=json.dumps({"filepath": filepath, "edits": {"rotation": 90}}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get(f"/api/v1/photos/edits?filepath={filepath}")
        edits = resp2.get_json()["edits"]
        assert edits["rotation"] == 90

    def test_save_invalid_rotation(self, client, tmp_path):
        """Rotation must be 0, 90, 180, or 270."""
        filepath = str(tmp_path / "img_0.jpg")
        resp = client.post(
            "/api/v1/photos/save-edits",
            data=json.dumps({"filepath": filepath, "edits": {"rotation": 45}}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_save_combined_edits(self, client, tmp_path):
        """All edit types can be saved together."""
        filepath = str(tmp_path / "img_0.jpg")
        edits = {
            "brightness": 1.2,
            "contrast": 1.1,
            "saturation": 1.15,
            "sharpness": 1.05,
            "crop_x": 0.1,
            "crop_y": 0.1,
            "crop_w": 0.8,
            "crop_h": 0.8,
            "rotation": 180,
            "flip_h": True,
            "flip_v": False,
        }
        resp = client.post(
            "/api/v1/photos/save-edits",
            data=json.dumps({"filepath": filepath, "edits": edits}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        resp2 = client.get(f"/api/v1/photos/edits?filepath={filepath}")
        result = resp2.get_json()["edits"]
        assert result["rotation"] == 180
        assert result["flip_h"] is True
        assert result["crop_x"] == pytest.approx(0.1)

    def test_save_edits_marks_enhanced(self, client, tmp_path):
        """After saving edits, photo should appear as enhanced in list."""
        filepath = str(tmp_path / "img_0.jpg")
        client.post(
            "/api/v1/photos/save-edits",
            data=json.dumps({"filepath": filepath, "edits": {"brightness": 1.3}}),
            content_type="application/json",
        )
        resp = client.get("/api/v1/photos")
        photos = resp.get_json()["photos"]
        match = [p for p in photos if p["filepath"] == filepath]
        assert len(match) == 1
        assert match[0]["_enhanced"] is True

    def test_reset_edits_clears_enhanced(self, client, tmp_path):
        """After reset-edits, photo should no longer appear as enhanced."""
        filepath = str(tmp_path / "img_0.jpg")
        # Save edits to mark as enhanced
        client.post(
            "/api/v1/photos/save-edits",
            data=json.dumps({"filepath": filepath, "edits": {"brightness": 1.3}}),
            content_type="application/json",
        )
        # Reset
        resp = client.post(
            "/api/v1/photos/reset-edits",
            data=json.dumps({"filepaths": [filepath]}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        # Verify enhanced flag cleared
        resp = client.get("/api/v1/photos")
        photos = resp.get_json()["photos"]
        match = [p for p in photos if p["filepath"] == filepath]
        assert len(match) == 1
        assert match[0]["_enhanced"] is False
        # Verify edits actually gone
        resp = client.get(f"/api/v1/photos/edits?filepath={filepath}")
        assert resp.get_json()["edits"] is None


# ---------------------------------------------------------------------------
# Extended edits v2: warmth, highlights, shadows, vignette, grain, fade
# ---------------------------------------------------------------------------


class TestAdvancedEditsDB:
    """Tests for warmth, highlights, shadows, vignette, grain, fade DB params."""

    def test_save_warmth(self, conn, tmp_path):
        """save_photo_edits stores warmth parameter."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        save_photo_edits(conn, pid, {"warmth": 0.5})
        result = get_photo_edits(conn, pid)
        assert result["warmth"] == pytest.approx(0.5)

    def test_save_highlights_shadows(self, conn, tmp_path):
        """save_photo_edits stores highlights and shadows."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        save_photo_edits(conn, pid, {"highlights": -0.3, "shadows": 0.4})
        result = get_photo_edits(conn, pid)
        assert result["highlights"] == pytest.approx(-0.3)
        assert result["shadows"] == pytest.approx(0.4)

    def test_save_vignette(self, conn, tmp_path):
        """save_photo_edits stores vignette amount."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        save_photo_edits(conn, pid, {"vignette": 0.6})
        result = get_photo_edits(conn, pid)
        assert result["vignette"] == pytest.approx(0.6)

    def test_save_grain_and_fade(self, conn, tmp_path):
        """save_photo_edits stores grain and fade."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        save_photo_edits(conn, pid, {"grain": 0.3, "fade": 0.2})
        result = get_photo_edits(conn, pid)
        assert result["grain"] == pytest.approx(0.3)
        assert result["fade"] == pytest.approx(0.2)

    def test_defaults_are_zero(self, conn, tmp_path):
        """New params default to 0.0 when not set."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        save_photo_edits(conn, pid, {"brightness": 1.2})
        result = get_photo_edits(conn, pid)
        assert result["warmth"] == pytest.approx(0.0)
        assert result["highlights"] == pytest.approx(0.0)
        assert result["shadows"] == pytest.approx(0.0)
        assert result["vignette"] == pytest.approx(0.0)
        assert result["grain"] == pytest.approx(0.0)
        assert result["fade"] == pytest.approx(0.0)

    def test_combined_all_params(self, conn, tmp_path):
        """All 17 edit params can be saved and retrieved together."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        edits = {
            "brightness": 1.3,
            "contrast": 1.1,
            "saturation": 1.2,
            "sharpness": 1.05,
            "crop_x": 0.1,
            "crop_y": 0.2,
            "crop_w": 0.7,
            "crop_h": 0.6,
            "rotation": 90,
            "flip_h": True,
            "flip_v": False,
            "warmth": 0.4,
            "highlights": -0.2,
            "shadows": 0.3,
            "vignette": 0.5,
            "grain": 0.15,
            "fade": 0.1,
        }
        save_photo_edits(conn, pid, edits)
        result = get_photo_edits(conn, pid)
        assert result["warmth"] == pytest.approx(0.4)
        assert result["vignette"] == pytest.approx(0.5)
        assert result["grain"] == pytest.approx(0.15)
        assert result["rotation"] == 90

    def test_save_redeye_points(self, conn, tmp_path):
        """save_photo_edits stores red-eye fix points as JSON."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        points = [{"x": 0.3, "y": 0.4, "radius": 0.02}, {"x": 0.6, "y": 0.4, "radius": 0.02}]
        save_photo_edits(conn, pid, {"redeye_points": points})
        result = get_photo_edits(conn, pid)
        assert result["redeye_points"] == points

    def test_save_filter_name(self, conn, tmp_path):
        """save_photo_edits stores filter name."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        save_photo_edits(conn, pid, {"filter_name": "Vivid", "contrast": 1.3, "saturation": 1.4})
        result = get_photo_edits(conn, pid)
        assert result["filter_name"] == "Vivid"
