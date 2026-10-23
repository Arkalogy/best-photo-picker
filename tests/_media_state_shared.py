"""Shared fixtures and helpers of the test_media_state.py suite (split for the 500-LOC cap)."""

from __future__ import annotations

import pytest
from PIL import Image

from bpp.web.app import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_test_image(path, color="green", size=(200, 200)):
    """Create a minimal JPEG image for testing."""
    Image.new("RGB", size, color).save(str(path), "JPEG")


def _create_png_image(path, color="blue", size=(200, 200)):
    """Create a minimal PNG image for testing."""
    Image.new("RGB", size, color).save(str(path), "PNG")


@pytest.fixture()
def bare_app(tmp_path):
    """App with NO analysis data (no thumbnails loaded)."""
    app = create_app(
        workdir=str(tmp_path),
        input_dir=str(tmp_path),
        library_path=str(tmp_path),
    )
    app.config["TESTING"] = True
    return app


@pytest.fixture()
def app_with_thumbs(tmp_path):
    """App with a real image upserted and thumbnails loaded."""
    img_path = tmp_path / "photo.jpg"
    _create_test_image(img_path)

    app = create_app(
        workdir=str(tmp_path),
        input_dir=str(tmp_path),
        library_path=str(tmp_path),
    )
    app.config["TESTING"] = True

    with app.app_context():
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()
        from bpp.db.photos import upsert_photo

        upsert_photo(
            conn,
            {
                "filepath": str(img_path),
                "aggregate_score": 0.8,
            },
        )
        conn.commit()
        ctx.invalidate_analysis()
        ctx.load_analysis_if_needed()

    return app, str(img_path)


@pytest.fixture()
def app_with_png(tmp_path):
    """App with a PNG image upserted and thumbnails loaded."""
    img_path = tmp_path / "photo.png"
    _create_png_image(img_path)

    app = create_app(
        workdir=str(tmp_path),
        input_dir=str(tmp_path),
        library_path=str(tmp_path),
    )
    app.config["TESTING"] = True

    with app.app_context():
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()
        from bpp.db.photos import upsert_photo

        upsert_photo(
            conn,
            {
                "filepath": str(img_path),
                "aggregate_score": 0.6,
            },
        )
        conn.commit()
        ctx.invalidate_analysis()
        ctx.load_analysis_if_needed()

    return app, str(img_path)


@pytest.fixture()
def app_with_pets(tmp_path):
    """App with a photo that has pet detections seeded."""
    img_path = tmp_path / "pet_photo.jpg"
    _create_test_image(img_path, color="red", size=(400, 400))

    app = create_app(
        workdir=str(tmp_path),
        input_dir=str(tmp_path),
        library_path=str(tmp_path),
    )
    app.config["TESTING"] = True

    with app.app_context():
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        conn = ctx.get_conn()
        from bpp.db.photos import upsert_photo

        photo_id = upsert_photo(
            conn,
            {
                "filepath": str(img_path),
                "aggregate_score": 0.9,
                "pet_count": 1,
                "has_cat": 1,
            },
        )
        conn.execute(
            "INSERT INTO pet_detections "
            "(photo_id, detection_index, class, confidence, "
            "bbox_x, bbox_y, bbox_w, bbox_h, cluster_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (photo_id, 0, "cat", 0.95, 10, 10, 100, 100, 0),
        )
        conn.commit()
        ctx.invalidate_analysis()
        ctx.load_analysis_if_needed()

    return app, str(img_path), photo_id
