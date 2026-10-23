"""A tab left open across a library switch cannot act on the other
library: every request carries the id of the library its page was
rendered for, and a mutation with a stale id is refused."""

from __future__ import annotations

import os


def _library(tmp_path, name):
    lib = tmp_path / name
    (lib / "photos").mkdir(parents=True)
    return lib


def test_a_stale_tab_cannot_delete_the_other_librarys_album(tmp_path):
    from bpp.db.albums import create_album, get_album
    from bpp.web.app import create_app

    a = _library(tmp_path, "A")
    b = _library(tmp_path, "B")
    app = create_app(workdir=str(a / "data"), library_path=str(a))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    id_a = ctx.library_id
    with app.app_context():
        create_album(ctx.get_conn(), "A's album")
        ctx.get_conn().commit()
    # The page rendered for A carries A's id.
    page = app.test_client().get("/")
    assert f'<meta name="library-id" content="{id_a}">' in page.get_data(as_text=True)
    ctx.switch_library(str(b))
    assert ctx.library_id != id_a
    with app.app_context():
        conn = ctx.get_conn()
        album_id = create_album(conn, "B's album")
        conn.commit()
    stale = app.test_client().delete(f"/api/v1/albums/{album_id}", headers={"X-Library-Id": id_a})
    assert stale.status_code == 409 and stale.get_json()["code"] == "library_changed"
    with app.app_context():
        assert get_album(ctx.get_conn(), album_id) is not None
    fresh = app.test_client().delete(
        f"/api/v1/albums/{album_id}", headers={"X-Library-Id": ctx.library_id}
    )
    assert fresh.status_code == 200


def test_reads_and_requests_without_the_header_are_unaffected(tmp_path):
    from bpp.web.app import create_app

    a = _library(tmp_path, "A")
    app = create_app(workdir=str(a / "data"), library_path=str(a))
    app.config["TESTING"] = True
    assert (
        app.test_client().get("/api/v1/status", headers={"X-Library-Id": "stale"}).status_code
        == 200
    )
    assert app.test_client().get("/api/v1/albums").status_code == 200
    assert os.path.isdir(a)
