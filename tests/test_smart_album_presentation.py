"""How a smart album looks in the sidebar comes from its registration.

Icon, tooltip and sort position lived in three maps in the JavaScript,
keyed by the built-in album types; a plugin album type got the folder
icon, no tooltip and a middle position with no way to say otherwise.
The registry now carries them and the albums list sends them.
"""

from __future__ import annotations

import pytest

from bpp.db.smart_albums import SmartAlbumRegistry


@pytest.fixture
def plugin_album_type():
    SmartAlbumRegistry.register(
        "smart_myplugin",
        lambda conn: None,
        lambda conn: [],
        icon="star",
        tooltip="Photos my plugin likes",
        order=3.5,
    )
    yield "smart_myplugin"
    SmartAlbumRegistry._types.pop("smart_myplugin", None)
    SmartAlbumRegistry._hooks.pop("smart_myplugin", None)


def test_presentation_is_stored_and_read_back(plugin_album_type):
    assert SmartAlbumRegistry.presentation(plugin_album_type) == {
        "icon": "star",
        "tooltip": "Photos my plugin likes",
        "sort_order": 3.5,
    }


def test_builtins_keep_their_icons_tooltips_and_order():
    dup = SmartAlbumRegistry.presentation("smart_duplicates")
    assert dup["icon"] == "duplicate" and dup["sort_order"] == 8
    assert SmartAlbumRegistry.presentation("smart_score")["tooltip"] == "Top 10% by BPP score"
    assert SmartAlbumRegistry.presentation("smart_person")["sort_order"] == 15
    assert SmartAlbumRegistry.presentation("manual") == {
        "icon": "",
        "tooltip": "",
        "sort_order": 1,
    }


def test_the_albums_list_sends_the_presentation(tmp_path, plugin_album_type):
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "lib"))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    with app.app_context():
        from bpp.db.albums import create_album

        create_album(ctx.get_conn(), "Plugin picks", album_type="smart_myplugin")
    with app.test_client() as c:
        albums = c.get("/api/v1/albums").get_json()["albums"]
    by_type = {a["album_type"]: a for a in albums}
    assert by_type["smart_myplugin"]["icon"] == "star"
    assert by_type["smart_myplugin"]["tooltip"] == "Photos my plugin likes"
    assert by_type["smart_myplugin"]["sort_order"] == 3.5
