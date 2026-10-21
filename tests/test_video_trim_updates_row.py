"""Trimming a video rewrites the file; the row must describe the new
file (hash, size, mtime, duration), or the untrimmed original is later
treated as a duplicate and the file can't be re-found by hash."""

from __future__ import annotations

import hashlib
import os

import pytest


@pytest.fixture()
def app(tmp_path):
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "lib"))
    app.config["TESTING"] = True
    return app


def test_trim_refreshes_hash_size_mtime_and_duration(app, monkeypatch):
    from bpp.db.photos import upsert_photo
    from bpp.web.state import get_ctx

    with app.app_context():
        ctx = get_ctx()
        d = os.path.join(ctx.state["library_path"], "photos")
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, "clip.mov")
        with open(fp, "wb") as f:
            f.write(b"old video bytes" * 10)
        conn = ctx.get_conn()
        upsert_photo(
            conn,
            {
                "filepath": fp,
                "sha256": "0" * 64,
                "file_size": 150,
                "is_video": 1,
                "video_duration": 10.0,
            },
        )
        conn.commit()

    def fake_trim(src, out, *, start, end):
        with open(out, "wb") as f:
            f.write(b"trimmed")
        return {"ok": True}

    monkeypatch.setattr("bpp.utils.video.ffmpeg_available", lambda: True)
    monkeypatch.setattr("bpp.utils.video.trim_video", fake_trim)
    monkeypatch.setattr(
        "bpp.utils.video.extract_video_metadata",
        lambda p: {"duration": 2.5, "width": 640, "height": 480, "fps": 30.0, "codec": "h264"},
    )
    with app.test_client() as c:
        r = c.post("/api/v1/video/trim", json={"filepath": fp, "start": 0, "end": 2.5})
    assert r.status_code == 200, r.get_data(as_text=True)
    with app.app_context():
        row = (
            get_ctx()
            .get_conn()
            .execute(
                "SELECT sha256, file_size, file_mtime, video_duration FROM photos WHERE filepath=?",
                (fp,),
            )
            .fetchone()
        )
    assert row["sha256"] == hashlib.sha256(b"trimmed").hexdigest()
    assert row["file_size"] == len(b"trimmed")
    assert row["file_mtime"] == pytest.approx(os.path.getmtime(fp), abs=1)
    assert row["video_duration"] == 2.5
