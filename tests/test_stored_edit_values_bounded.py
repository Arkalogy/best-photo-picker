"""Saved photo edits stay inside the ranges their editor controls have.

Edit values come from the library database. A crop box outside the
picture made the rendered image larger than the photo, and a stored
noise-reduction value sized the blur kernel with no limit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PIL import Image

from bpp.db.edit_limits import EDIT_RANGES, clean_edits

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def conn_and_photo(tmp_path):
    from bpp.db.connection import close_all_connections, get_db, init_db
    from bpp.db.photos import upsert_photo

    db = str(tmp_path / "photopicker.db")
    init_db(db)
    conn = get_db(db)
    path = tmp_path / "a.jpg"
    Image.new("RGB", (40, 30), "red").save(path, "JPEG")
    photo_id = upsert_photo(conn, {"filepath": str(path)})
    conn.commit()
    yield conn, photo_id, str(path)
    close_all_connections(new_epoch=True)


def _plant(conn, photo_id, **cols):
    names = ", ".join(cols)
    marks = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO photo_edits (photo_id, {names}) VALUES (?, {marks})",
        (photo_id, *cols.values()),
    )
    conn.commit()


def test_a_stored_crop_box_is_brought_inside_the_picture(conn_and_photo):
    from bpp.db.edits import get_photo_edits

    conn, photo_id, _ = conn_and_photo
    _plant(conn, photo_id, crop_x=-5.0, crop_y=0.5, crop_w=50.0, crop_h=50.0)

    edits = get_photo_edits(conn, photo_id)

    assert (edits["crop_x"], edits["crop_y"]) == (0.0, 0.5)
    assert edits["crop_x"] + edits["crop_w"] <= 1.0
    assert edits["crop_y"] + edits["crop_h"] <= 1.0


def test_the_rendered_image_is_never_larger_than_the_photo(conn_and_photo, tmp_path):
    from bpp.db.edits import get_photo_edits
    from bpp.web.photo_edits import _apply_edits

    conn, photo_id, path = conn_and_photo
    _plant(conn, photo_id, crop_x=-100.0, crop_y=-100.0, crop_w=300.0, crop_h=300.0)

    with Image.open(path) as img:
        out = _apply_edits(img.convert("RGB"), get_photo_edits(conn, photo_id))

    assert out.size[0] <= 40 and out.size[1] <= 30


def test_stored_slider_values_are_clamped_and_text_becomes_the_default(conn_and_photo):
    from bpp.db.edits import get_photo_edits

    conn, photo_id, _ = conn_and_photo
    _plant(conn, photo_id, noise_reduction=1e9, brightness="abc", definition=-40, rotation=45)

    edits = get_photo_edits(conn, photo_id)

    assert edits["noise_reduction"] == 1.0
    assert edits["brightness"] == 1.0
    assert edits["definition"] == -1.0
    assert edits["rotation"] == 0


def test_values_are_clamped_when_saved_too(conn_and_photo):
    from bpp.db.edits import save_photo_edits

    conn, photo_id, _ = conn_and_photo
    save_photo_edits(
        conn,
        photo_id,
        {"noise_reduction": 500, "crop_x": 0.5, "crop_y": 0, "crop_w": 9, "crop_h": 1},
    )

    row = conn.execute(
        "SELECT noise_reduction, crop_x, crop_w FROM photo_edits WHERE photo_id = ?", (photo_id,)
    ).fetchone()
    assert (row[0], row[1], row[2]) == (1.0, 0.5, 0.5)


@pytest.mark.parametrize(
    "crop",
    [
        {"crop_x": 0.1, "crop_y": 0.1, "crop_w": 0, "crop_h": 0.5},
        {"crop_x": "a", "crop_y": 0.1, "crop_w": 0.5, "crop_h": 0.5},
        {"crop_x": float("nan"), "crop_y": 0.1, "crop_w": 0.5, "crop_h": 0.5},
        {"crop_x": 1.0, "crop_y": 0.0, "crop_w": 0.5, "crop_h": 0.5},
        {"crop_x": 0.1},
    ],
)
def test_an_unusable_crop_is_no_crop(crop):
    cleaned = clean_edits(crop)
    assert [cleaned[k] for k in ("crop_x", "crop_y", "crop_w", "crop_h")] == [None] * 4


def test_values_inside_their_range_are_untouched_and_other_keys_pass_through():
    edits = {
        "brightness": 1.2,
        "crop_x": 0.1,
        "crop_y": 0.2,
        "crop_w": 0.5,
        "crop_h": 0.6,
        "rotation": 90,
        "flip_h": True,
        "filter_name": "vivid",
        "redeye_points": [{"x": 0.5, "y": 0.5}],
        "auto_enhanced": True,
    }
    assert clean_edits(edits) == edits
    assert "contrast" not in clean_edits(edits)


def test_the_ranges_match_the_editors_sliders():
    js = (REPO_ROOT / "bpp/web/static/js/modules/editor-constants.mjs").read_text()
    sliders = {
        key: (float(low), float(high), float(default))
        for key, low, high, default in re.findall(
            r'\{ key: "(\w+)", label: "[^"]+", min: (-?[\d.]+), max: (-?[\d.]+),'
            r" step: [\d.]+, default: (-?[\d.]+)",
            js,
        )
    }
    assert len(sliders) >= 17, sliders
    for key, expected in sliders.items():
        assert EDIT_RANGES.get(key) == expected, (
            f"{key}: editor {expected}, server {EDIT_RANGES.get(key)}"
        )
