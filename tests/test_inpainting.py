"""Tests for AI object removal (inpainting) module and API endpoint."""

from __future__ import annotations

import base64
import json
import os
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from bpp.ai.inpainting import inpaint, inpaint_from_bytes


def _make_test_image(w=64, h=64, color="red", fmt="PNG") -> bytes:
    """Create a test image as bytes."""
    img = Image.new("RGB", (w, h), color)
    buf = BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _make_mask(w=64, h=64, painted=True) -> bytes:
    """Create a test mask as PNG bytes."""
    if painted:
        mask = Image.new("L", (w, h), 0)
        # Paint a white stripe in the middle
        for x in range(w // 4, 3 * w // 4):
            for y in range(h // 4, 3 * h // 4):
                mask.putpixel((x, y), 255)
    else:
        mask = Image.new("L", (w, h), 0)
    buf = BytesIO()
    mask.save(buf, format="PNG")
    return buf.getvalue()


# ── Unit tests for inpainting module ──


class TestIsAvailable:
    def test_returns_false_without_dep(self):
        with patch.dict("sys.modules", {"simple_lama_inpainting": None}):
            # Reimport to force fresh check
            import importlib

            from bpp.ai import inpainting

            importlib.reload(inpainting)
            assert inpainting.is_available() is False

    def test_returns_true_with_dep(self):
        mock_module = MagicMock()
        with patch.dict("sys.modules", {"simple_lama_inpainting": mock_module}):
            import importlib

            from bpp.ai import inpainting

            importlib.reload(inpainting)
            assert inpainting.is_available() is True


class TestInpaintFunction:
    def test_size_mismatch_raises(self):
        image = Image.new("RGB", (100, 100), "red")
        mask = Image.new("L", (50, 50), 255)
        with pytest.raises(ValueError, match="doesn't match mask size"):
            inpaint(image, mask)

    def test_converts_modes(self):
        """RGBA image and binary mask should be auto-converted."""
        image = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
        mask = Image.new("1", (64, 64), 1)

        fake_result = Image.new("RGB", (64, 64), "blue")
        mock_model = MagicMock(return_value=fake_result)

        with patch("bpp.ai.inpainting._get_model", return_value=mock_model):
            result = inpaint(image, mask)
            assert result.mode == "RGB"
            assert result.size == (64, 64)
            # Verify model was called with RGB and L images
            call_args = mock_model.call_args[0]
            assert call_args[0].mode == "RGB"
            assert call_args[1].mode == "L"

    def test_successful_inpaint(self):
        image = Image.new("RGB", (64, 64), "red")
        mask = Image.new("L", (64, 64), 255)
        fake_result = Image.new("RGB", (64, 64), "green")
        mock_model = MagicMock(return_value=fake_result)

        with patch("bpp.ai.inpainting._get_model", return_value=mock_model):
            result = inpaint(image, mask)
            assert result.size == (64, 64)
            mock_model.assert_called_once()


class TestInpaintFromBytes:
    def test_roundtrip(self):
        image_bytes = _make_test_image()
        mask_bytes = _make_mask()
        fake_result = Image.new("RGB", (64, 64), "blue")
        mock_model = MagicMock(return_value=fake_result)

        with patch("bpp.ai.inpainting._get_model", return_value=mock_model):
            result_bytes = inpaint_from_bytes(image_bytes, mask_bytes)
            assert isinstance(result_bytes, bytes)
            # Verify result is valid PNG
            result_img = Image.open(BytesIO(result_bytes))
            assert result_img.mode == "RGB"


# ── API endpoint tests ──


@pytest.fixture
def inpaint_app(tmp_path):
    """Create a Flask app with a real photo in the DB for inpaint testing."""
    from bpp.web.app import create_app

    lib = tmp_path / "library"
    lib.mkdir()
    # Create a test photo on disk
    photo_path = lib / "test.jpg"
    img = Image.new("RGB", (64, 64), "red")
    img.save(str(photo_path), "JPEG")

    app = create_app(workdir=str(lib), library_path=str(lib))
    app.config["TESTING"] = True

    # Insert a photo row into the DB
    with app.app_context():
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, sha256, file_size, file_mtime)"
            " VALUES (?, ?, ?, ?, ?)",
            (str(photo_path), "test.jpg", "abc123", 1024, 1700000000.0),
        )
        conn.commit()

    return app


@pytest.fixture
def inpaint_client(inpaint_app):
    return inpaint_app.test_client()


class TestInpaintStatusEndpoint:
    def test_status_available(self, inpaint_client):
        with patch("bpp.ai.inpainting.is_available", return_value=True):
            resp = inpaint_client.get("/api/v1/inpaint/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["available"] is True

    def test_status_unavailable(self, inpaint_client):
        with patch("bpp.ai.inpainting.is_available", return_value=False):
            resp = inpaint_client.get("/api/v1/inpaint/status")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["available"] is False


class TestInpaintEndpoint:
    def test_not_available_returns_501(self, inpaint_client):
        mask_b64 = base64.b64encode(_make_mask()).decode()
        with patch("bpp.ai.inpainting.is_available", return_value=False):
            resp = inpaint_client.post(
                "/api/v1/photos/1/inpaint",
                data=json.dumps({"mask": mask_b64}),
                content_type="application/json",
            )
            assert resp.status_code == 501

    def test_photo_not_found(self, inpaint_client):
        mask_b64 = base64.b64encode(_make_mask()).decode()
        with patch("bpp.ai.inpainting.is_available", return_value=True):
            resp = inpaint_client.post(
                "/api/v1/photos/999/inpaint",
                data=json.dumps({"mask": mask_b64}),
                content_type="application/json",
            )
            assert resp.status_code == 404

    def test_missing_mask_returns_400(self, inpaint_client):
        with patch("bpp.ai.inpainting.is_available", return_value=True):
            resp = inpaint_client.post(
                "/api/v1/photos/1/inpaint",
                data=json.dumps({}),
                content_type="application/json",
            )
            assert resp.status_code == 400
            assert "mask" in resp.get_json()["error"].lower()

    def test_invalid_base64_returns_400(self, inpaint_client):
        with patch("bpp.ai.inpainting.is_available", return_value=True):
            resp = inpaint_client.post(
                "/api/v1/photos/1/inpaint",
                data=json.dumps({"mask": "not-valid-base64!!!"}),
                content_type="application/json",
            )
            assert resp.status_code == 400

    def test_successful_inpaint(self, inpaint_app, inpaint_client):
        mask_b64 = base64.b64encode(_make_mask()).decode()
        fake_result = Image.new("RGB", (64, 64), "blue")

        with (
            patch("bpp.ai.inpainting.is_available", return_value=True),
            patch("bpp.ai.inpainting.inpaint", return_value=fake_result),
        ):
            resp = inpaint_client.post(
                "/api/v1/photos/1/inpaint",
                data=json.dumps({"mask": mask_b64}),
                content_type="application/json",
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert "image" in data
            assert data["photo_id"] == 1
            # Verify the returned base64 is valid
            img_bytes = base64.b64decode(data["image"])
            result_img = Image.open(BytesIO(img_bytes))
            assert result_img.mode == "RGB"

    def test_source_file_missing(self, inpaint_app, inpaint_client):
        """If the file was deleted from disk, return 404."""
        # Remove the test image
        with inpaint_app.app_context():
            from bpp.web.state import get_ctx

            ctx = get_ctx()
            conn = ctx.get_conn()
            row = conn.execute("SELECT filepath FROM photos WHERE id = 1").fetchone()
            os.remove(row["filepath"])

        mask_b64 = base64.b64encode(_make_mask()).decode()
        with patch("bpp.ai.inpainting.is_available", return_value=True):
            resp = inpaint_client.post(
                "/api/v1/photos/1/inpaint",
                data=json.dumps({"mask": mask_b64}),
                content_type="application/json",
            )
            assert resp.status_code == 404
            assert "not found on disk" in resp.get_json()["error"].lower()


class TestLamaCatalogObservability:
    """The catalog trio (ensure/remove) must not fail silently — a stuck
    or failed object-removal download/uninstall has to leave a log trail
    (project convention: nothing should be silent)."""

    def test_ensure_logs_start_and_finish(self, caplog):
        from bpp.ai import inpainting

        with (
            patch.object(inpainting._LAMA, "ensure_model", return_value="/tmp/big-lama.pt"),
            caplog.at_level("INFO", logger="bpp.ai.inpainting"),
        ):
            path = inpainting.ensure_lama_model()
        assert path == "/tmp/big-lama.pt"
        msgs = " ".join(r.message for r in caplog.records)
        assert "Ensuring LaMa" in msgs, f"no start log; got {msgs!r}"
        assert "ready" in msgs.lower(), f"no finish log; got {msgs!r}"

    def test_ensure_failure_raises_with_context(self):
        from bpp.ai import inpainting

        with (
            patch.object(inpainting._LAMA, "ensure_model", return_value=None),
            pytest.raises(RuntimeError) as exc,
        ):
            inpainting.ensure_lama_model()
        # The error must name the model and source so a multi-model
        # endpoint can tell which download failed.
        assert "lama_inpaint_research" in str(exc.value)
        assert "source=" in str(exc.value)

    def test_remove_logs_warning_on_unlink_failure(self, caplog, tmp_path):
        from bpp.ai import inpainting

        fake = tmp_path / "big-lama.pt"
        fake.write_bytes(b"x" * 100)

        def _boom(*_a, **_k):
            raise OSError("permission denied")

        with (
            patch.object(inpainting, "_LAMA_MODEL_PATH", fake),
            patch.object(type(fake), "unlink", _boom),
            caplog.at_level("WARNING", logger="bpp.ai.inpainting"),
        ):
            freed = inpainting.remove_local_weights()
        # Delete failed → report 0 bytes freed (not the file size) and
        # log a WARNING so the failure is diagnosable.
        assert freed == 0
        assert any("Failed to delete LaMa" in r.message for r in caplog.records), (
            "expected a WARNING when unlink fails"
        )

    def test_remove_idempotent_when_absent(self, tmp_path):
        from bpp.ai import inpainting

        missing = tmp_path / "nope.pt"
        with (
            patch.object(inpainting, "_LAMA_MODEL_PATH", missing),
            patch.object(inpainting._LAMA, "reset"),
        ):
            assert inpainting.remove_local_weights() == 0
