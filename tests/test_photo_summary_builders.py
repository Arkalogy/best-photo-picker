"""Lightweight photo projections come from one builder each.

Calendar, On This Day, Memories, the Duplicates siblings and the review
groups each hand-built a small photo dict. Memories put the file's
content hash where every other surface puts the thumbnail hash, so a
memory's photos that were not already on the page got no thumbnail.
"""

from __future__ import annotations

import os

from PIL import Image


class _Thumbs:
    def get_hash(self, fp):
        return "th-" + os.path.basename(fp)


def test_brief_and_similar_entry_shapes():
    from bpp.web.photo_dict import build_photo_brief, build_similar_entry

    item = {
        "id": 7,
        "filepath": "/p/a.jpg",
        "date": "2024-01-02T03:04:05",
        "aggregate_score": 0.5,
        "blur_score": 0.1,
        "date_day": "2024-01-02",
        "original_filename": "orig.jpg",
    }
    assert build_photo_brief(item, _Thumbs()) == {
        "id": 7,
        "filepath": "/p/a.jpg",
        "filename": "orig.jpg",
        "hash": "th-a.jpg",
        "date": "2024-01-02T03:04:05",
        "score": 0.5,
    }
    sim = build_similar_entry(item, _Thumbs(), similarity=None)
    assert sim["thumb_hash"] == "th-a.jpg"
    assert sim["similarity"] is None
    assert sim["blur_score"] == 0.1 and sim["exposure_score"] == 0
    assert sim["filename"] == "orig.jpg"
    assert build_photo_brief({"id": 1, "filepath": "/p/b.jpg"}, None)["filename"] == "b.jpg"


def _library_with_one_photo(tmp_path, when="2023-04-25T10:00:00"):
    from bpp.db.photos import upsert_photo
    from bpp.web.app import create_app

    lib = str(tmp_path / "lib")
    os.makedirs(os.path.join(lib, "photos"))
    app = create_app(library_path=lib)
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    photo = os.path.join(lib, "photos", "a.jpg")
    Image.new("RGB", (8, 8), "gray").save(photo, "JPEG")
    with app.app_context():
        conn = ctx.get_conn()
        upsert_photo(
            conn,
            {"filepath": photo, "date": when, "aggregate_score": 0.4, "sha256": "ff" * 32},
        )
        conn.commit()
    return app, ctx, photo


def test_on_this_day_and_calendar_carry_the_thumbnail_hash(tmp_path):
    app, ctx, photo = _library_with_one_photo(tmp_path)
    with app.test_client() as c:
        years = c.get("/api/v1/on-this-day?month=4&day=25").get_json()["years"]
        day = c.get("/api/v1/calendar/photos?date=2023-04-25").get_json()["photos"]
    assert years[0]["photos"][0]["hash"] == ctx.thumbs.get_hash(photo)
    assert years[0]["hero_hash"] == ctx.thumbs.get_hash(photo)
    assert day[0]["hash"] == ctx.thumbs.get_hash(photo)
    assert set(day[0]) == {"id", "filepath", "filename", "hash", "date", "score"}


def test_a_memory_lists_its_photos_with_the_thumbnail_hash(tmp_path):
    app, ctx, photo = _library_with_one_photo(tmp_path)
    with app.app_context():
        conn = ctx.get_conn()
        pid = conn.execute("SELECT id FROM photos").fetchone()[0]
        conn.execute(
            "INSERT INTO memories (title, date_start, date_end, hero_photo_id, photo_count, score)"
            " VALUES ('Spring', '2023-04-25', '2023-04-25', ?, 1, 0.5)",
            (pid,),
        )
        mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute("INSERT INTO memory_photos (memory_id, photo_id) VALUES (?, ?)", (mid, pid))
        conn.commit()
    with app.test_client() as c:
        m = c.get(f"/api/v1/memories/{mid}").get_json()
    assert m["photos"][0]["hash"] == ctx.thumbs.get_hash(photo), "not the content hash"
