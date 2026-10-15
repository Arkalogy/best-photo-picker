"""Red-eye edit points: bad points (non-numeric, NaN, out of the image,
huge) are rejected on save, never stored, and can't crash a render."""

from __future__ import annotations

import json

import pytest


def test_redeye_points_need_numeric_coordinates(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.edits import get_photo_edits, save_photo_edits

    conn = init_db(str(tmp_path / "photopicker.db"))
    pid = conn.execute(
        "INSERT INTO photos (filepath, original_filename, file_size, file_mtime) "
        "VALUES ('/p/a.jpg', 'a.jpg', 1, 1.0)"
    ).lastrowid
    save_photo_edits(conn, pid, {"brightness": 1.0})
    bad = json.dumps(
        [1, {"y": 0.1}, {"x": "a", "y": 0.2}, {"x": True, "y": 0.1}, {"x": 0.5, "y": 0.5}]
    )
    conn.execute("UPDATE photo_edits SET redeye_json = ? WHERE photo_id = ?", (bad, pid))
    conn.commit()
    assert get_photo_edits(conn, pid)["redeye_points"] == [{"x": 0.5, "y": 0.5}]


@pytest.mark.parametrize(
    "point",
    [
        {"x": 0.5, "y": 0.5, "radius": "a"},
        {"x": float("nan"), "y": 0.5},
        {"x": 1e999, "y": 0.5},
        {"x": 0.5, "y": 0.5, "radius": True},
    ],
)
def test_redeye_points_need_finite_coordinates_and_radius(tmp_path, point):
    from bpp.db.connection import init_db
    from bpp.db.edits import get_photo_edits, save_photo_edits

    conn = init_db(str(tmp_path / "photopicker.db"))
    pid = conn.execute(
        "INSERT INTO photos (filepath, original_filename, file_size, file_mtime) "
        "VALUES ('/p/a.jpg', 'a.jpg', 1, 1.0)"
    ).lastrowid
    save_photo_edits(conn, pid, {"brightness": 1.0})
    good = {"x": 0.1, "y": 0.2, "radius": 0.03}
    conn.execute(
        "UPDATE photo_edits SET redeye_json = ? WHERE photo_id = ?",
        (json.dumps([point, good]), pid),
    )
    conn.commit()
    assert get_photo_edits(conn, pid)["redeye_points"] == [good]


def test_save_edits_rejects_bad_redeye_points(tmp_path):
    from bpp.web.app import create_app

    workdir = tmp_path / "w"
    workdir.mkdir()
    app = create_app(workdir=str(workdir))
    app.config["TESTING"] = True
    resp = app.test_client().post(
        "/api/v1/photos/save-edits",
        json={"filepath": "/p/a.jpg", "edits": {"redeye_points": [{"x": "a", "y": 0.5}]}},
    )
    assert resp.status_code == 400
    assert "redeye_points" in resp.get_data(as_text=True)


def test_bad_redeye_points_are_never_stored_or_rendered(tmp_path):
    from PIL import Image

    from bpp.db.connection import init_db
    from bpp.db.edits import get_photo_edits, save_photo_edits
    from bpp.web.photo_edits import _apply_redeye

    conn = init_db(str(tmp_path / "photopicker.db"))
    pid = conn.execute(
        "INSERT INTO photos (filepath, original_filename, file_size, file_mtime) "
        "VALUES ('/p/a.jpg', 'a.jpg', 1, 1.0)"
    ).lastrowid
    save_photo_edits(conn, pid, {"redeye_points": [{"x": float("nan"), "y": 0.5}]})
    raw = conn.execute("SELECT redeye_json FROM photo_edits WHERE photo_id = ?", (pid,)).fetchone()
    assert raw[0] is None
    assert get_photo_edits(conn, pid)["redeye_points"] is None
    img = Image.new("RGB", (8, 8))
    assert _apply_redeye(img, {"redeye_points": [{"x": "a"}]}) is img  # used to raise
    assert _apply_redeye(img, {"redeye_points": {"x": 1}}) is img


@pytest.mark.parametrize(
    "point",
    [
        {"x": 10**400, "y": 0.5},
        {"x": 1e306, "y": 0.5},
        {"x": -0.1, "y": 0.5},
        {"x": 0.5, "y": 0.5, "radius": 0},
    ],
)
def test_out_of_range_redeye_points_are_dropped(point):
    from bpp.db.edits import valid_redeye_points

    assert valid_redeye_points([point, {"x": 0.0, "y": 1.0, "radius": 0.03}]) == [
        {"x": 0.0, "y": 1.0, "radius": 0.03}
    ]
