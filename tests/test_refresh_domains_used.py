"""Every refresh domain a plugin can subscribe to actually fires.

``register_album_domain`` lets a plugin album refresh on a mutation
domain, but ``dedup``, ``edit`` and ``face_extract`` had no caller, and
the Sensitive album was refreshed by name, so a plugin album registered
on those domains never refreshed.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest
from PIL import Image

from bpp.db.smart_album_domains import _DOMAIN_ALBUM_MAP, register_album_domain
from bpp.db.smart_albums import SmartAlbumRegistry

ROOT = pathlib.Path(__file__).resolve().parents[1] / "bpp"


def test_every_domain_has_a_caller():
    used = set()
    for path in ROOT.rglob("*.py"):
        used.update(re.findall(r'get_affected_album_types\("([a-z_]+)"\)', path.read_text()))
    missing = set(_DOMAIN_ALBUM_MAP) - used
    assert not missing, f"domains nothing fires: {sorted(missing)}"
    assert "sensitive" in _DOMAIN_ALBUM_MAP


@pytest.fixture
def plugin_album(tmp_path):
    refreshed: list[str] = []

    def refresh(conn):
        refreshed.append("x")

    SmartAlbumRegistry.register("smart_myplugin", refresh, lambda conn: [])
    yield refreshed
    SmartAlbumRegistry._types.pop("smart_myplugin", None)
    SmartAlbumRegistry._hooks.pop("smart_myplugin", None)
    from bpp.db.smart_album_domains import _reset_album_domain_for_tests

    _reset_album_domain_for_tests()


def _app_with_photo(tmp_path):
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
        upsert_photo(conn, {"filepath": photo, "date": "2024-01-01T00:00:00"})
        conn.commit()
        ctx.invalidate_analysis()
    return app, photo


def test_an_album_on_the_edit_domain_refreshes_when_edits_are_saved(tmp_path, plugin_album):
    register_album_domain("edit", ("smart_myplugin",), extend=True)
    app, photo = _app_with_photo(tmp_path)
    plugin_album.clear()
    with app.test_client() as c:
        r = c.post("/api/v1/photos/save-edits", json={"filepath": photo, "edits": {"rotation": 90}})
    assert r.status_code == 200, r.get_json()
    assert plugin_album, "the edit domain did not refresh the plugin album"


def test_an_album_on_the_sensitive_domain_refreshes_with_the_override(tmp_path, plugin_album):
    register_album_domain("sensitive", ("smart_myplugin",), extend=True)
    app, photo = _app_with_photo(tmp_path)
    plugin_album.clear()
    with app.test_client() as c:
        r = c.post("/api/v1/photos/sensitive", json={"filepath": photo, "override": 0})
    assert r.status_code == 200, r.get_json()
    assert plugin_album, "the sensitive domain did not refresh the plugin album"
