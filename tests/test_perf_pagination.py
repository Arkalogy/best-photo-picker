"""Tag photos, calendar photos and duplicate groups accept limit/offset
so a 20k-photo tag or month isn't one response."""

from __future__ import annotations

import os

import pytest
from PIL import Image


@pytest.fixture()
def app(tmp_path):
    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir, exist_ok=True)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True
    return app


def _photos(app, tmp_path, n):
    from bpp.db.photos import upsert_photo
    from bpp.web.state import get_ctx

    ids = []
    with app.app_context():
        conn = get_ctx().get_conn()
        for i in range(n):
            fp = str(tmp_path / f"p{i}.jpg")
            Image.new("RGB", (10, 10)).save(fp, "JPEG")
            ids.append(
                upsert_photo(
                    conn,
                    {
                        "filepath": fp,
                        "date": f"2024-06-15T10:00:{i:02d}",
                        "aggregate_score": i / 10,
                        "phash": 1234,
                    },
                )
            )
        conn.commit()
    return ids


def test_tag_photos_paginate(app, tmp_path):
    from bpp.db.tags import add_tag_to_photo, create_tag
    from bpp.web.state import get_ctx

    ids = _photos(app, tmp_path, 5)
    with app.app_context():
        conn = get_ctx().get_conn()
        tid = create_tag(conn, "trip")
        for pid in ids:
            add_tag_to_photo(conn, pid, tid)
        conn.commit()
    c = app.test_client()
    whole = c.get(f"/api/v1/tags/{tid}/photos").get_json()
    assert whole["count"] == 5 and whole["total"] == 5 and whole["has_more"] is False
    page = c.get(f"/api/v1/tags/{tid}/photos?limit=2&offset=2").get_json()
    assert [p["filepath"] for p in page["photos"]] == [p["filepath"] for p in whole["photos"][2:4]]
    assert page["total"] == 5 and page["has_more"] is True
    last = c.get(f"/api/v1/tags/{tid}/photos?limit=2&offset=4").get_json()
    assert len(last["photos"]) == 1 and last["has_more"] is False


def test_calendar_photos_paginate(app, tmp_path):
    _photos(app, tmp_path, 5)
    c = app.test_client()
    whole = c.get("/api/v1/calendar/photos?date=2024-06-15").get_json()
    assert len(whole["photos"]) == 5 and whole["has_more"] is False
    page = c.get("/api/v1/calendar/photos?date=2024-06-15&limit=2&offset=1").get_json()
    assert [p["filepath"] for p in page["photos"]] == [p["filepath"] for p in whole["photos"][1:3]]
    assert page["has_more"] is True and page["total"] == 5


def test_duplicate_groups_paginate(app, tmp_path):
    from bpp.web.state import get_ctx

    ids = _photos(app, tmp_path, 6)
    with app.app_context():
        conn = get_ctx().get_conn()
        for i, pid in enumerate(ids):
            conn.execute(
                "UPDATE photos SET dup_cluster_id=?, cluster_size=2 WHERE id=?", (1 + i // 2, pid)
            )
        conn.commit()
    c = app.test_client()
    whole = c.get("/api/v1/duplicates/groups").get_json()
    assert whole["total"] == 3 and len(whole["groups"]) == 3 and whole["has_more"] is False
    page = c.get("/api/v1/duplicates/groups?limit=2&offset=2").get_json()
    assert page["total"] == 3 and len(page["groups"]) == 1 and page["has_more"] is False
    first = c.get("/api/v1/duplicates/groups?limit=2").get_json()
    assert len(first["groups"]) == 2 and first["has_more"] is True
    assert first["groups"][0]["best_filepath"] == whole["groups"][0]["best_filepath"]
