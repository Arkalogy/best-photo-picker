"""Second-pass security review (v0.1.3): trim output never collides with
a library file, the exported gallery escapes names, library rename only
moves a registered library, and report.csv can't carry formulas."""

from __future__ import annotations

import csv
import html
import os

import pytest
from PIL import Image


@pytest.fixture()
def app(tmp_path):
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "lib"))
    app.config["TESTING"] = True
    return app


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _video(app, name, content):
    from bpp.db.photos import upsert_photo
    from bpp.web.state import get_ctx

    with app.app_context():
        ctx = get_ctx()
        d = os.path.join(ctx.state["library_path"], "photos")
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, name)
        with open(fp, "wb") as f:
            f.write(content)
        conn = ctx.get_conn()
        upsert_photo(conn, {"filepath": fp, "is_video": 1})
        conn.commit()
        return fp


def test_trim_never_touches_a_neighbouring_library_file(app, monkeypatch):
    fp = _video(app, "clip.mov", b"original" * 10)
    neighbour = _video(app, "clip_trimmed.mov", b"someone else's clip")
    seen = {}

    def fake_trim(src, out, *, start, end):
        seen["out"] = out
        with open(out, "wb") as f:
            f.write(b"trimmed")
        return {"ok": True}

    monkeypatch.setattr("bpp.utils.video.ffmpeg_available", lambda: True)
    monkeypatch.setattr("bpp.utils.video.trim_video", fake_trim)
    monkeypatch.setattr("bpp.utils.video.extract_video_metadata", lambda p: {"duration": 1.0})
    with app.test_client() as c:
        assert (
            c.post("/api/v1/video/trim", json={"filepath": fp, "start": 0, "end": 1}).status_code
            == 200
        )
    assert seen["out"] != neighbour
    assert _read(neighbour) == b"someone else's clip"
    assert _read(fp) == b"trimmed"
    assert not os.path.exists(seen["out"])


def test_a_failed_trim_leaves_no_partial_file(app, monkeypatch):
    fp = _video(app, "clip.mov", b"original" * 10)
    seen = {}

    def failing_trim(src, out, *, start, end):
        seen["out"] = out
        with open(out, "wb") as f:
            f.write(b"half")
        return {"ok": False, "error": "ffmpeg failed: boom"}

    monkeypatch.setattr("bpp.utils.video.ffmpeg_available", lambda: True)
    monkeypatch.setattr("bpp.utils.video.trim_video", failing_trim)
    with app.test_client() as c:
        assert (
            c.post("/api/v1/video/trim", json={"filepath": fp, "start": 0, "end": 1}).status_code
            == 400
        )
    assert not os.path.exists(seen["out"])
    assert _read(fp) == b"original" * 10


def test_gallery_escapes_file_names(tmp_path):
    from bpp.output.gallery import generate_gallery

    name = 'x" onerror="alert(1)" <b>.jpg'
    src = tmp_path / name
    Image.new("RGB", (8, 8)).save(str(src), "JPEG")
    out = tmp_path / "out"
    out.mkdir()
    generate_gallery(
        [{"filepath": str(src), "aggregate_score": 0.5, "selection_reason": "<s>"}], str(out)
    )
    page = (out / "gallery.html").read_text()
    assert name not in page
    assert html.escape(name, quote=True) in page
    assert "<s>" not in page and "&lt;s&gt;" in page
    assert 'src="thumbnails/001_x%22%20onerror' in page


def test_library_rename_refuses_a_folder_that_is_not_a_library(app, tmp_path):
    other = tmp_path / "Documents"
    other.mkdir()
    with app.test_client() as c:
        r = c.put(
            "/api/v1/libraries/rename",
            json={"path": str(other), "name": "Renamed", "rename_folder": True},
        )
    assert r.status_code in (400, 404), r.get_data(as_text=True)
    assert other.exists() and not (tmp_path / "Renamed").exists()
    from bpp.db.registry import list_libraries

    assert all(lib["path"] != str(other) for lib in list_libraries())


def test_csv_report_neutralizes_formula_cells(tmp_path):
    from bpp.output.export_reports import _write_reports

    item = {
        "filepath": '=HYPERLINK("http://x").jpg',
        "aggregate_score": 0.5,
        "date": "2024-01-01",
    }
    _write_reports(str(tmp_path), [item], [item], {}, [], "/lib", True)
    with open(tmp_path / "report.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["filepath"].startswith("'=")
