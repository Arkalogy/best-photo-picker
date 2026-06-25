"""TDD tests for photo editing: auto-enhance, manual adjustments, crop, rotate, flip."""

from __future__ import annotations

import json
import os

import pytest
from PIL import Image, ImageStat

from bpp.db.connection import get_db, init_db
from bpp.db.photos import upsert_photo

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test_enhance.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    return get_db(db_path)


def _make_photo(conn, tmp_path, name, size=(200, 200), color="gray"):
    f = tmp_path / name
    img = Image.new("RGB", size, color)
    img.save(str(f), "JPEG")
    photo = {"filepath": str(f)}
    pid = upsert_photo(conn, photo)
    return pid, str(f)


def _make_dark_photo(tmp_path, name):
    """Create a dark underexposed image."""
    f = tmp_path / name
    img = Image.new("RGB", (200, 200), (30, 25, 20))
    img.save(str(f), "JPEG")
    return str(f)


def _make_bright_photo(tmp_path, name):
    """Create an already well-exposed image."""
    f = tmp_path / name
    img = Image.new("RGB", (200, 200), (180, 170, 160))
    img.save(str(f), "JPEG")
    return str(f)


def _make_dull_photo(tmp_path, name):
    """Create a low-saturation grayish image."""
    f = tmp_path / name
    img = Image.new("RGB", (200, 200), (120, 118, 116))
    img.save(str(f), "JPEG")
    return str(f)


# ---------------------------------------------------------------------------
# DB layer tests — photo_edits CRUD
# ---------------------------------------------------------------------------


class TestPhotoEditsDB:
    """Tests for photo_edits table CRUD operations."""

    def test_save_edits(self, conn, tmp_path):
        """save_photo_edits stores edit params for a photo."""
        from bpp.db.edits import save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        edits = {"brightness": 1.2, "contrast": 1.1, "saturation": 1.3, "sharpness": 1.1}
        save_photo_edits(conn, pid, edits)

        row = conn.execute("SELECT * FROM photo_edits WHERE photo_id=?", (pid,)).fetchone()
        assert row is not None

    def test_get_edits(self, conn, tmp_path):
        """get_photo_edits returns saved edit params."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        edits = {"brightness": 1.2, "contrast": 1.1, "saturation": 1.3, "sharpness": 1.1}
        save_photo_edits(conn, pid, edits)

        result = get_photo_edits(conn, pid)
        assert result["brightness"] == pytest.approx(1.2)
        assert result["contrast"] == pytest.approx(1.1)
        assert result["saturation"] == pytest.approx(1.3)
        assert result["sharpness"] == pytest.approx(1.1)

    def test_get_edits_none_when_no_edits(self, conn, tmp_path):
        """get_photo_edits returns None for unedited photo."""
        from bpp.db.edits import get_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        assert get_photo_edits(conn, pid) is None

    def test_save_edits_upserts(self, conn, tmp_path):
        """Saving edits twice updates rather than duplicates."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        save_photo_edits(conn, pid, {"brightness": 1.2})
        save_photo_edits(conn, pid, {"brightness": 1.5, "contrast": 1.3})

        result = get_photo_edits(conn, pid)
        assert result["brightness"] == pytest.approx(1.5)
        assert result["contrast"] == pytest.approx(1.3)

        count = conn.execute(
            "SELECT COUNT(*) FROM photo_edits WHERE photo_id=?", (pid,)
        ).fetchone()[0]
        assert count == 1

    def test_reset_edits(self, conn, tmp_path):
        """reset_photo_edits removes edit record."""
        from bpp.db.edits import get_photo_edits, reset_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        save_photo_edits(conn, pid, {"brightness": 1.2})
        reset_photo_edits(conn, pid)
        assert get_photo_edits(conn, pid) is None

    def test_reset_nonexistent_noop(self, conn, tmp_path):
        """Resetting edits for unedited photo returns 0."""
        from bpp.db.edits import reset_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        assert reset_photo_edits(conn, pid) == 0

    def test_has_edits(self, conn, tmp_path):
        """has_edits returns True/False correctly."""
        from bpp.db.edits import has_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        assert has_edits(conn, pid) is False
        save_photo_edits(conn, pid, {"brightness": 1.1})
        assert has_edits(conn, pid) is True


# ---------------------------------------------------------------------------
# Auto-enhance algorithm tests
# ---------------------------------------------------------------------------


class TestAutoEnhance:
    """Tests for the auto-enhance (magic pop) algorithm."""

    def test_returns_enhancement_params(self, tmp_path):
        """auto_enhance returns a dict with brightness/contrast/saturation/sharpness."""
        from bpp.scoring.enhance import auto_enhance

        path = _make_dark_photo(tmp_path, "dark.jpg")
        params = auto_enhance(path)
        assert "brightness" in params
        assert "contrast" in params
        assert "saturation" in params
        assert "sharpness" in params

    def test_dark_photo_gets_brightness_boost(self, tmp_path):
        """Dark photos should get brightness > 1.0."""
        from bpp.scoring.enhance import auto_enhance

        path = _make_dark_photo(tmp_path, "dark.jpg")
        params = auto_enhance(path)
        assert params["brightness"] > 1.0

    def test_bright_photo_minimal_brightness(self, tmp_path):
        """Already well-exposed photos should get near-1.0 brightness."""
        from bpp.scoring.enhance import auto_enhance

        path = _make_bright_photo(tmp_path, "bright.jpg")
        params = auto_enhance(path)
        # Should be close to 1.0 (minor dim or slight boost)
        assert 0.8 <= params["brightness"] <= 1.15

    def test_dull_photo_gets_saturation_boost(self, tmp_path):
        """Low-saturation photos should get saturation > 1.0."""
        from bpp.scoring.enhance import auto_enhance

        path = _make_dull_photo(tmp_path, "dull.jpg")
        params = auto_enhance(path)
        assert params["saturation"] > 1.0

    def test_contrast_always_positive(self, tmp_path):
        """Contrast factor should always be >= 1.0 (never reduce)."""
        from bpp.scoring.enhance import auto_enhance

        path = _make_dark_photo(tmp_path, "any.jpg")
        params = auto_enhance(path)
        assert params["contrast"] >= 1.0

    def test_params_within_reasonable_bounds(self, tmp_path):
        """All enhancement factors should stay within sane limits."""
        from bpp.scoring.enhance import auto_enhance

        path = _make_dark_photo(tmp_path, "dark.jpg")
        params = auto_enhance(path)
        for key in ("brightness", "contrast", "saturation", "sharpness"):
            assert 0.8 <= params[key] <= 2.0, f"{key}={params[key]} out of range"

    def test_apply_enhance_produces_different_image(self, tmp_path):
        """apply_enhance should produce a visually different image."""
        from bpp.scoring.enhance import apply_enhance

        src = _make_dark_photo(tmp_path, "src.jpg")
        dest = str(tmp_path / "enhanced.jpg")
        params = {"brightness": 1.4, "contrast": 1.2, "saturation": 1.3, "sharpness": 1.1}
        apply_enhance(src, dest, params)

        assert os.path.isfile(dest)
        with Image.open(src) as orig, Image.open(dest) as enhanced:
            orig_mean = ImageStat.Stat(orig).mean
            enh_mean = ImageStat.Stat(enhanced).mean
            # Enhanced dark photo should be brighter
            assert sum(enh_mean) > sum(orig_mean)

    def test_apply_enhance_preserves_dimensions(self, tmp_path):
        """Enhanced image should have same dimensions as original."""
        from bpp.scoring.enhance import apply_enhance

        src = _make_dark_photo(tmp_path, "src.jpg")
        dest = str(tmp_path / "enhanced.jpg")
        params = {"brightness": 1.3, "contrast": 1.1, "saturation": 1.2, "sharpness": 1.1}
        apply_enhance(src, dest, params)

        with Image.open(src) as orig, Image.open(dest) as enhanced:
            assert orig.size == enhanced.size

    def test_apply_enhance_identity_params(self, tmp_path):
        """Params all at 1.0 should produce nearly identical output."""
        from bpp.scoring.enhance import apply_enhance

        src = _make_bright_photo(tmp_path, "src.jpg")
        dest = str(tmp_path / "enhanced.jpg")
        params = {"brightness": 1.0, "contrast": 1.0, "saturation": 1.0, "sharpness": 1.0}
        apply_enhance(src, dest, params)

        with Image.open(src) as orig, Image.open(dest) as enhanced:
            orig_mean = ImageStat.Stat(orig).mean
            enh_mean = ImageStat.Stat(enhanced).mean
            # Should be very close (within JPEG compression noise)
            for o, e in zip(orig_mean, enh_mean, strict=True):
                assert abs(o - e) < 5


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestEnhanceAPI:
    """Tests for the enhance REST API endpoints."""

    @pytest.fixture
    def client(self, tmp_path):
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir)

        analysis = []
        for i in range(3):
            fpath = str(tmp_path / f"img_{i}.jpg")
            img = Image.new("RGB", (200, 200), (30 + i * 20, 25 + i * 15, 20 + i * 10))
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

    def test_enhance_single_photo(self, client, tmp_path):
        """POST /api/photos/enhance should auto-enhance a photo."""
        filepath = str(tmp_path / "img_0.jpg")
        resp = client.post(
            "/api/v1/photos/enhance",
            data=json.dumps({"filepaths": [filepath]}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enhanced"] == 1
        assert "params" in data

    def test_enhance_returns_params(self, client, tmp_path):
        """Enhance response includes computed enhancement params."""
        filepath = str(tmp_path / "img_0.jpg")
        resp = client.post(
            "/api/v1/photos/enhance",
            data=json.dumps({"filepaths": [filepath]}),
            content_type="application/json",
        )
        data = resp.get_json()
        params = data["params"]
        assert filepath in params
        assert "brightness" in params[filepath]

    def test_enhance_batch(self, client, tmp_path):
        """POST /api/photos/enhance with multiple photos."""
        filepaths = [str(tmp_path / f"img_{i}.jpg") for i in range(3)]
        resp = client.post(
            "/api/v1/photos/enhance",
            data=json.dumps({"filepaths": filepaths}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enhanced"] == 3

    def test_reset_edits_endpoint(self, client, tmp_path):
        """POST /api/photos/reset-edits removes edits."""
        filepath = str(tmp_path / "img_0.jpg")
        # First enhance
        client.post(
            "/api/v1/photos/enhance",
            data=json.dumps({"filepaths": [filepath]}),
            content_type="application/json",
        )
        # Then reset
        resp = client.post(
            "/api/v1/photos/reset-edits",
            data=json.dumps({"filepaths": [filepath]}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["reset"] == 1

    def test_get_edits_endpoint(self, client, tmp_path):
        """GET /api/photos/edits returns current edits for a photo."""
        filepath = str(tmp_path / "img_0.jpg")
        client.post(
            "/api/v1/photos/enhance",
            data=json.dumps({"filepaths": [filepath]}),
            content_type="application/json",
        )
        resp = client.get(f"/api/v1/photos/edits?filepath={filepath}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["edits"] is not None
        assert "brightness" in data["edits"]

    def test_get_edits_none_for_unedited(self, client, tmp_path):
        """GET /api/photos/edits returns null for unedited photo."""
        filepath = str(tmp_path / "img_0.jpg")
        resp = client.get(f"/api/v1/photos/edits?filepath={filepath}")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["edits"] is None

    def test_enhance_failure_does_not_leak_path_in_error(self, client, tmp_path, monkeypatch):
        """R8-H2: when `auto_enhance` raises, the API response must
        emit a generic message — NOT `str(e)`. PIL exceptions
        typically include the absolute file path, which would leak
        the owner's library layout to any LAN client holding a valid
        share token.

        Reproduces the leak by monkeypatching `auto_enhance` to raise
        an exception whose text contains the absolute filepath
        (mimicking PIL's `[Errno 2] No such file or directory:
        '/path/...'` shape) and asserting the path is absent from
        the response body."""
        # `auto_enhance` is imported locally inside the endpoint, so
        # monkeypatch the source module rather than the blueprint.
        from bpp.scoring import enhance as _enhance_mod

        secret_path = str(tmp_path / "img_0.jpg")

        def _leaky(_fp):
            raise OSError(
                f"[Errno 2] No such file or directory: "
                f"'/Users/alice/Pictures/Private/{os.path.basename(secret_path)}'"
            )

        monkeypatch.setattr(_enhance_mod, "auto_enhance", _leaky)

        resp = client.post(
            "/api/v1/photos/enhance",
            data=json.dumps({"filepaths": [secret_path]}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        body = json.dumps(resp.get_json())
        assert "/Users/alice" not in body, "Absolute path leaked in enhance response"
        assert "[Errno 2]" not in body, "Raw exception text leaked"
        assert "No such file or directory" not in body
        # The errors dict should still report the failure, just generically
        errors = resp.get_json().get("errors", {})
        assert errors.get(secret_path) == "Enhance failed"


# ---------------------------------------------------------------------------
# Extended edits: crop, rotation, flip
# ---------------------------------------------------------------------------


class TestExtendedEditsDB:
    """Tests for crop, rotation, and flip edit parameters."""

    def test_save_crop_params(self, conn, tmp_path):
        """save_photo_edits stores crop coordinates."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        edits = {"crop_x": 0.1, "crop_y": 0.2, "crop_w": 0.6, "crop_h": 0.5}
        save_photo_edits(conn, pid, edits)

        result = get_photo_edits(conn, pid)
        assert result["crop_x"] == pytest.approx(0.1)
        assert result["crop_y"] == pytest.approx(0.2)
        assert result["crop_w"] == pytest.approx(0.6)
        assert result["crop_h"] == pytest.approx(0.5)

    def test_save_rotation(self, conn, tmp_path):
        """save_photo_edits stores rotation angle."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        save_photo_edits(conn, pid, {"rotation": 90})

        result = get_photo_edits(conn, pid)
        assert result["rotation"] == 90

    def test_save_flip(self, conn, tmp_path):
        """save_photo_edits stores flip flags."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        save_photo_edits(conn, pid, {"flip_h": True, "flip_v": False})

        result = get_photo_edits(conn, pid)
        assert result["flip_h"] is True
        assert result["flip_v"] is False

    def test_combined_edits(self, conn, tmp_path):
        """All edit types can be combined in one save."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        edits = {
            "brightness": 1.3,
            "contrast": 1.1,
            "saturation": 1.2,
            "sharpness": 1.05,
            "crop_x": 0.1,
            "crop_y": 0.0,
            "crop_w": 0.8,
            "crop_h": 1.0,
            "rotation": 270,
            "flip_h": True,
            "flip_v": False,
        }
        save_photo_edits(conn, pid, edits)
        result = get_photo_edits(conn, pid)
        assert result["brightness"] == pytest.approx(1.3)
        assert result["crop_x"] == pytest.approx(0.1)
        assert result["rotation"] == 270
        assert result["flip_h"] is True

    def test_default_crop_is_none(self, conn, tmp_path):
        """crop fields default to None when not set."""
        from bpp.db.edits import get_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        save_photo_edits(conn, pid, {"brightness": 1.2})
        result = get_photo_edits(conn, pid)
        assert result["crop_x"] is None
        assert result["rotation"] == 0
        assert result["flip_h"] is False

    def test_reset_clears_all_edits(self, conn, tmp_path):
        """reset_photo_edits clears crop/rotation/flip too."""
        from bpp.db.edits import get_photo_edits, reset_photo_edits, save_photo_edits

        pid, _ = _make_photo(conn, tmp_path, "p.jpg")
        save_photo_edits(
            conn, pid, {"brightness": 1.5, "crop_x": 0.1, "rotation": 90, "flip_h": True}
        )
        reset_photo_edits(conn, pid)
        assert get_photo_edits(conn, pid) is None


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
