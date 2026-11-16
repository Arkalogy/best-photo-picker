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


def test_calendar_month_and_memories_carry_the_thumbnail_hash(tmp_path):
    """The day's top photo and a memory's hero are shown with /thumb/<hash>:
    that is the thumbnail hash, not the file's content hash."""
    app, ctx, photo = _library_with_one_photo(tmp_path)
    with app.app_context():
        conn = ctx.get_conn()
        (photo_id,) = conn.execute("SELECT id FROM photos").fetchone()
        conn.execute(
            "INSERT INTO memories (title, date_start, date_end, hero_photo_id, photo_count, score)"
            " VALUES ('Trip', '2023-04-25', '2023-04-25', ?, 1, 1.0)",
            (photo_id,),
        )
        memory_id = conn.execute("SELECT id FROM memories").fetchone()[0]
        conn.commit()
    with app.test_client() as c:
        days = c.get("/api/v1/calendar/days?year=2023&month=4").get_json()["days"]
        memories = c.get("/api/v1/memories").get_json()["memories"]
        memory = c.get(f"/api/v1/memories/{memory_id}").get_json()
    want = ctx.thumbs.get_hash(photo)
    assert days == [{"day": 25, "count": 1, "top_hash": want}]
    assert memories[0]["hero_hash"] == want
    assert "hero_path" not in memories[0]
    assert memory["hero_hash"] == want and "hero_path" not in memory


def test_memory_refresh_answers_with_the_cards_the_page_shows(tmp_path, monkeypatch):
    """The page renders the refresh answer directly: it has to be the list
    shape (thumbnail hash, no path), not the generator's work records."""
    app, ctx, photo = _library_with_one_photo(tmp_path)
    with app.app_context():
        conn = ctx.get_conn()
        (photo_id,) = conn.execute("SELECT id FROM photos").fetchone()

    def fake_generate(conn):
        conn.execute(
            "INSERT INTO memories (title, date_start, date_end, hero_photo_id, photo_count, score)"
            " VALUES ('Trip', '2023-04-25', '2023-04-25', ?, 1, 1.0)",
            (photo_id,),
        )
        conn.commit()
        return [{"title": "Trip", "photo_ids": [photo_id]}]

    monkeypatch.setattr("bpp.web.bp_memories.generate_memories", fake_generate)
    with app.test_client() as c:
        body = c.post("/api/v1/memories/refresh").get_json()
    assert body["count"] == 1
    assert body["memories"][0]["hero_hash"] == ctx.thumbs.get_hash(photo)
    assert "hero_path" not in body["memories"][0] and "photo_ids" not in body["memories"][0]


def test_a_hidden_hero_photo_is_not_shown_on_its_memory(tmp_path):
    app, ctx, _photo = _library_with_one_photo(tmp_path)
    with app.app_context():
        conn = ctx.get_conn()
        (photo_id,) = conn.execute("SELECT id FROM photos").fetchone()
        conn.execute(
            "INSERT INTO memories (title, date_start, date_end, hero_photo_id, photo_count, score)"
            " VALUES ('Trip', '2023-04-25', '2023-04-25', ?, 1, 1.0)",
            (photo_id,),
        )
        conn.execute("UPDATE photos SET hidden_at = datetime('now') WHERE id = ?", (photo_id,))
        conn.commit()
    with app.test_client() as c:
        memories = c.get("/api/v1/memories").get_json()["memories"]
    assert memories[0]["hero_hash"] is None
