"""TDD tests for P1-1: Album hierarchy — nested albums with parent_id."""

from __future__ import annotations

import os

import pytest


def _init_db(tmp_path):
    from bpp.db.connection import init_db

    return init_db(str(tmp_path / "test.db"))


class TestAlbumParentId:
    """Tests for parent_id column on albums."""

    @pytest.fixture()
    def db(self, tmp_path):
        return _init_db(tmp_path)

    def test_create_album_with_parent(self, db):
        from bpp.db.albums import create_album, get_album

        parent_id = create_album(db, "Travel", album_type="manual")
        child_id = create_album(db, "Italy 2024", album_type="manual", parent_id=parent_id)
        child = get_album(db, child_id)
        assert child["parent_id"] == parent_id

    def test_create_album_without_parent(self, db):
        from bpp.db.albums import create_album, get_album

        aid = create_album(db, "Random", album_type="manual")
        album = get_album(db, aid)
        assert album["parent_id"] is None

    def test_update_album_parent(self, db):
        from bpp.db.albums import create_album, get_album, update_album

        folder = create_album(db, "Folder")
        album = create_album(db, "Album")
        update_album(db, album, parent_id=folder)
        updated = get_album(db, album)
        assert updated["parent_id"] == folder

    def test_clear_album_parent(self, db):
        """Setting parent_id to None moves album to top level."""
        from bpp.db.albums import create_album, get_album, update_album

        folder = create_album(db, "Folder")
        album = create_album(db, "Album", parent_id=folder)
        assert get_album(db, album)["parent_id"] == folder
        update_album(db, album, parent_id=None)
        assert get_album(db, album)["parent_id"] is None

    def test_list_albums_includes_parent_id(self, db):
        from bpp.db.albums import create_album, list_albums

        parent = create_album(db, "Parent")
        create_album(db, "Child", parent_id=parent)
        albums = list_albums(db)
        child = next(a for a in albums if a["name"] == "Child")
        assert child["parent_id"] == parent

    def test_delete_parent_nullifies_children(self, db):
        """When a parent album is deleted, children become top-level."""
        from bpp.db.albums import create_album, delete_album, get_album

        parent = create_album(db, "Parent")
        child = create_album(db, "Child", parent_id=parent)
        delete_album(db, parent)
        updated_child = get_album(db, child)
        assert updated_child["parent_id"] is None

    def test_nested_three_levels(self, db):
        from bpp.db.albums import create_album, get_album

        root = create_album(db, "Root")
        mid = create_album(db, "Mid", parent_id=root)
        leaf = create_album(db, "Leaf", parent_id=mid)
        assert get_album(db, mid)["parent_id"] == root
        assert get_album(db, leaf)["parent_id"] == mid

    def test_cannot_parent_to_self(self, db):
        """Album cannot be its own parent."""
        from bpp.db.albums import create_album, get_album, update_album

        aid = create_album(db, "Album")
        # update_album should reject self-parenting
        update_album(db, aid, parent_id=aid)
        assert get_album(db, aid)["parent_id"] is None

    def test_move_album_to_different_parent(self, db):
        from bpp.db.albums import create_album, get_album, update_album

        folder_a = create_album(db, "Folder A")
        folder_b = create_album(db, "Folder B")
        album = create_album(db, "Album", parent_id=folder_a)
        assert get_album(db, album)["parent_id"] == folder_a
        update_album(db, album, parent_id=folder_b)
        assert get_album(db, album)["parent_id"] == folder_b


class TestAlbumHierarchyAPI:
    """Tests for album hierarchy via API endpoints."""

    @pytest.fixture()
    def app(self, tmp_path):
        from bpp.web.app import create_app

        workdir = str(tmp_path / "workdir")
        os.makedirs(workdir, exist_ok=True)
        app = create_app(workdir=workdir)
        app.config["TESTING"] = True
        return app

    @pytest.fixture()
    def client(self, app):
        return app.test_client()

    def test_create_album_with_parent_api(self, client, app):
        with app.app_context():
            # Create parent
            resp = client.post(
                "/api/v1/albums",
                json={"name": "Travel"},
                content_type="application/json",
            )
            assert resp.status_code == 201
            parent_id = resp.get_json()["id"]

            # Create child
            resp = client.post(
                "/api/v1/albums",
                json={"name": "Italy", "parent_id": parent_id},
                content_type="application/json",
            )
            assert resp.status_code == 201
            child_id = resp.get_json()["id"]

            # Verify via GET
            resp = client.get(f"/api/v1/albums/{child_id}")
            assert resp.get_json()["album"]["parent_id"] == parent_id

    def _post_album(self, client, name, **kwargs):
        return client.post(
            "/api/v1/albums",
            json={"name": name, **kwargs},
            content_type="application/json",
        )

    def test_update_album_parent_api(self, client, app):
        with app.app_context():
            resp = self._post_album(client, "Folder")
            folder_id = resp.get_json()["id"]
            resp = self._post_album(client, "Album")
            album_id = resp.get_json()["id"]

            # Move album into folder
            resp = client.put(
                f"/api/v1/albums/{album_id}",
                json={"parent_id": folder_id},
                content_type="application/json",
            )
            assert resp.status_code == 200

            resp = client.get(f"/api/v1/albums/{album_id}")
            assert resp.get_json()["album"]["parent_id"] == folder_id

    def test_move_album_to_top_level_api(self, client, app):
        with app.app_context():
            resp = self._post_album(client, "Folder")
            folder_id = resp.get_json()["id"]
            resp = self._post_album(client, "Album", parent_id=folder_id)
            album_id = resp.get_json()["id"]

            # Move to top level (parent_id = null)
            resp = client.put(
                f"/api/v1/albums/{album_id}",
                json={"parent_id": None},
                content_type="application/json",
            )
            assert resp.status_code == 200
            resp = client.get(f"/api/v1/albums/{album_id}")
            assert resp.get_json()["album"]["parent_id"] is None

    def test_list_albums_returns_parent_id(self, client, app):
        with app.app_context():
            resp = self._post_album(client, "Parent")
            parent_id = resp.get_json()["id"]
            self._post_album(client, "Child", parent_id=parent_id)

            resp = client.get("/api/v1/albums")
            albums = resp.get_json()["albums"]
            child = next(a for a in albums if a["name"] == "Child")
            assert child["parent_id"] == parent_id
