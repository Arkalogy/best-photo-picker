"""Applying a batch rename refreshes what the UI reads next (analysis
rows, thumbnail hash map) and only renames the row the id names."""

from __future__ import annotations

import os

import pytest
from PIL import Image


@pytest.fixture()
def app(tmp_path):
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "lib"))
    app.config["TESTING"] = True
    return app


def _photo(app, name):
    from bpp.db.photos import upsert_photo
    from bpp.web.state import get_ctx

    with app.app_context():
        ctx = get_ctx()
        d = os.path.join(ctx.state["library_path"], "photos")
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, name)
        Image.new("RGB", (8, 8)).save(fp, "JPEG")
        conn = ctx.get_conn()
        pid = upsert_photo(conn, {"filepath": fp})
        conn.commit()
        ctx.invalidate_analysis()
        ctx.load_analysis_if_needed()
        return pid, fp


def test_apply_refreshes_analysis_and_thumbnail_map(app):
    from bpp.web.state import get_ctx

    pid, fp = _photo(app, "a.jpg")
    new_fp = os.path.join(os.path.dirname(fp), "renamed.jpg")
    with app.test_client() as c:
        r = c.post(
            "/api/v1/batch/rename/apply",
            json={
                "mapping": [
                    {"id": pid, "old_filepath": fp, "new_filepath": new_fp, "changed": True}
                ]
            },
        )
    assert r.status_code == 200 and r.get_json()["results"][0]["success"]
    with app.app_context():
        ctx = get_ctx()
        paths = {a["filepath"] for a in ctx.load_analysis_if_needed()}
        assert paths == {new_fp}
        assert ctx.thumbs.get_filepath(ctx.thumbs.get_hash(new_fp)) == new_fp


def test_an_entry_whose_path_is_not_the_rows_path_is_refused(app):
    from bpp.web.state import get_ctx

    pid_a, fp_a = _photo(app, "a.jpg")
    _pid_b, fp_b = _photo(app, "b.jpg")
    new_fp = os.path.join(os.path.dirname(fp_b), "stolen.jpg")
    with app.test_client() as c:
        r = c.post(
            "/api/v1/batch/rename/apply",
            json={
                "mapping": [
                    {"id": pid_a, "old_filepath": fp_b, "new_filepath": new_fp, "changed": True}
                ]
            },
        )
    assert r.status_code == 200
    assert r.get_json()["results"][0]["success"] is False
    assert os.path.exists(fp_b) and not os.path.exists(new_fp)
    with app.app_context():
        conn = get_ctx().get_conn()
        assert (
            conn.execute("SELECT filepath FROM photos WHERE id=?", (pid_a,)).fetchone()[0] == fp_a
        )
