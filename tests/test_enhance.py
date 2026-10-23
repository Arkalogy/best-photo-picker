"""TDD tests for photo editing: auto-enhance, manual adjustments, crop, rotate, flip — part
1 of 4."""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import json
import os

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
        """When `auto_enhance` raises, the API response must
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
