"""The photo and video routes send a file only when it is that kind of media.

Both routes look the file up by a path stored in the library database, and
a library prepared by someone else can store any path there. A paired
device asking for that row must not be sent a document.
"""

from __future__ import annotations

import pytest

from bpp.web.app import create_app

NOTE = b"owner's private note, not media"


@pytest.fixture
def app_with_rows(tmp_path):
    from bpp.db.photos import upsert_photo
    from bpp.web.state import get_ctx

    paths = {}
    for name in ("note.txt", "note.gif", "clip.mp4"):
        path = tmp_path / name
        path.write_bytes(NOTE)
        paths[name] = str(path)
    app = create_app(workdir=str(tmp_path), input_dir=str(tmp_path), library_path=str(tmp_path))
    app.config["TESTING"] = True
    with app.app_context():
        ctx = get_ctx()
        conn = ctx.get_conn()
        for path in paths.values():
            upsert_photo(conn, {"filepath": path, "aggregate_score": 0.5, "is_video": 1})
        conn.commit()
        ctx.invalidate_analysis()
        ctx.load_analysis_if_needed()
        hashes = {name: ctx.thumbs.get_hash(path) for name, path in paths.items()}
    return app, hashes


def test_the_video_route_does_not_send_a_file_that_is_not_a_video(app_with_rows):
    app, hashes = app_with_rows
    with app.test_client() as c:
        r = c.get(f"/video/{hashes['note.txt']}")
    assert r.status_code == 404
    assert NOTE not in r.data


def test_the_video_route_still_sends_a_video(app_with_rows):
    app, hashes = app_with_rows
    with app.test_client() as c:
        r = c.get(f"/video/{hashes['clip.mp4']}")
    assert r.status_code == 200
    assert r.mimetype == "video/mp4"


@pytest.mark.parametrize("name", ["note.txt", "note.gif", "clip.mp4"])
def test_the_photo_route_does_not_send_a_file_that_is_not_an_image(app_with_rows, name):
    app, hashes = app_with_rows
    with app.test_client() as c:
        r = c.get(f"/photo/{hashes[name]}")
    assert r.status_code == 404
    assert NOTE not in r.data


def test_the_photo_route_still_sends_an_image_of_another_type(tmp_path):
    from PIL import Image

    from bpp.db.photos import upsert_photo
    from bpp.web.state import get_ctx

    path = tmp_path / "anim.gif"
    Image.new("RGB", (8, 8), "red").save(path, "GIF")
    app = create_app(workdir=str(tmp_path), input_dir=str(tmp_path), library_path=str(tmp_path))
    app.config["TESTING"] = True
    with app.app_context():
        ctx = get_ctx()
        upsert_photo(ctx.get_conn(), {"filepath": str(path), "aggregate_score": 0.5})
        ctx.get_conn().commit()
        ctx.invalidate_analysis()
        ctx.load_analysis_if_needed()
        h = ctx.thumbs.get_hash(str(path))
    with app.test_client() as c:
        r = c.get(f"/photo/{h}")
    assert r.status_code == 200
