"""Tests for the library management API blueprint."""

from __future__ import annotations

import json
import os

import pytest


@pytest.fixture
def lib_client(tmp_path, monkeypatch):
    """Flask test client with registry pointed to tmp dir."""
    from bpp.web.app import create_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    lib = tmp_path / "testlib"
    lib.mkdir()
    # Seed with analysis so the app has photos
    analysis = [
        {
            "filepath": f"{lib}/img_{i}.jpg",
            "date": f"2024-01-{i + 1:02d}T12:00:00",
            "date_day": f"2024-01-{i + 1:02d}",
            "date_month": "2024-01",
            "file_size": 1024,
            "file_mtime": 1700000000.0,
            "blur_raw": 100.0,
            "blur_score": 0.5,
            "exposure_score": 0.5,
            "face_score": 0.3,
            "face_count": 0,
            "largest_face_ratio": 0.0,
            "face_center_dist": 0.0,
            "composition_score": 0.5,
            "aggregate_score": 0.5,
        }
        for i in range(3)
    ]
    with open(lib / "analysis.json", "w") as f:
        json.dump(analysis, f)

    app = create_app(workdir=str(lib), library_path=str(lib))
    app.config["TESTING"] = True
    client = app.test_client()
    # Trigger DB init
    client.get("/api/v1/photos")
    return client, str(lib), tmp_path


class TestListLibraries:
    def test_list_empty(self, lib_client):
        client, _, _ = lib_client
        resp = client.get("/api/v1/libraries")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "libraries" in data
        assert "active" in data
        assert isinstance(data["libraries"], list)

    def test_list_after_add(self, lib_client):
        client, _lib_path, tmp_path = lib_client
        new_lib = str(tmp_path / "newlib")
        os.makedirs(new_lib)
        client.post("/api/v1/libraries", json={"path": new_lib, "name": "New"})
        resp = client.get("/api/v1/libraries")
        data = resp.get_json()
        assert len(data["libraries"]) >= 1
        assert any(lib["name"] == "New" for lib in data["libraries"])


class TestAddLibrary:
    def test_add_new(self, lib_client):
        client, _, tmp_path = lib_client
        new_lib = str(tmp_path / "fresh")
        os.makedirs(new_lib)
        resp = client.post("/api/v1/libraries", json={"path": new_lib, "name": "Fresh"})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "Fresh"
        assert data["path"] == new_lib

    def test_add_creates_directory(self, lib_client):
        client, _, tmp_path = lib_client
        new_lib = str(tmp_path / "brand_new")
        resp = client.post("/api/v1/libraries", json={"path": new_lib, "name": "Brand New"})
        assert resp.status_code == 201
        assert os.path.isdir(new_lib)

    def test_add_missing_path(self, lib_client):
        client, _, _ = lib_client
        resp = client.post("/api/v1/libraries", json={"name": "NoPath"})
        assert resp.status_code == 400

    def test_add_default_name(self, lib_client):
        client, _, tmp_path = lib_client
        new_lib = str(tmp_path / "vacation_2024")
        os.makedirs(new_lib)
        resp = client.post("/api/v1/libraries", json={"path": new_lib})
        assert resp.status_code == 201
        data = resp.get_json()
        assert data["name"] == "vacation_2024"


class TestRemoveLibrary:
    def test_remove(self, lib_client):
        client, _, tmp_path = lib_client
        new_lib = str(tmp_path / "removeme")
        os.makedirs(new_lib)
        client.post("/api/v1/libraries", json={"path": new_lib, "name": "Remove"})
        resp = client.delete("/api/v1/libraries", json={"path": new_lib})
        assert resp.status_code == 200
        # Still exists on disk
        assert os.path.isdir(new_lib)
        # Gone from registry
        libs = client.get("/api/v1/libraries").get_json()["libraries"]
        assert not any(lib["path"] == new_lib for lib in libs)

    def test_remove_missing_path(self, lib_client):
        client, _, _ = lib_client
        resp = client.delete("/api/v1/libraries", json={})
        assert resp.status_code == 400


class TestRenameLibrary:
    def test_rename(self, lib_client):
        client, _, tmp_path = lib_client
        lib = str(tmp_path / "renameme")
        os.makedirs(lib)
        client.post("/api/v1/libraries", json={"path": lib, "name": "OldName"})
        resp = client.put("/api/v1/libraries/rename", json={"path": lib, "name": "NewName"})
        assert resp.status_code == 200
        libs = client.get("/api/v1/libraries").get_json()["libraries"]
        assert any(lib["name"] == "NewName" for lib in libs)

    def test_rename_with_non_normalized_path(self, lib_client):
        """Path with trailing slash or double-slash must still rename correctly."""
        client, _, tmp_path = lib_client
        lib = str(tmp_path / "normtest")
        os.makedirs(lib)
        client.post("/api/v1/libraries", json={"path": lib, "name": "OldName"})
        # Send path with trailing slash — should be normalized server-side
        resp = client.put(
            "/api/v1/libraries/rename", json={"path": lib + "/", "name": "NormedName"}
        )
        assert resp.status_code == 200
        libs = client.get("/api/v1/libraries").get_json()["libraries"]
        assert any(entry["name"] == "NormedName" for entry in libs), (
            "rename must work with trailing slash"
        )

    def test_rename_folder(self, lib_client):
        """rename_folder=True moves the directory on disk and updates registry path."""
        client, _, tmp_path = lib_client
        lib = str(tmp_path / "before")
        os.makedirs(lib)
        client.post("/api/v1/libraries", json={"path": lib, "name": "Before"})
        resp = client.put(
            "/api/v1/libraries/rename",
            json={"path": lib, "name": "after", "rename_folder": True},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        new_path = data["new_path"]
        assert os.path.isdir(new_path), "renamed folder must exist on disk"
        assert not os.path.isdir(lib), "old folder must be gone"
        libs = client.get("/api/v1/libraries").get_json()["libraries"]
        assert any(entry["name"] == "after" for entry in libs)

    def test_rename_folder_conflict_leaves_registry_clean(self, lib_client):
        """When rename_folder=True fails (name clash), registry must not be dirtied."""
        client, _, tmp_path = lib_client
        lib = str(tmp_path / "src")
        conflict = str(tmp_path / "conflict")
        os.makedirs(lib)
        os.makedirs(conflict)
        # Put a file in the conflict dir so os.rename raises OSError on all platforms
        (tmp_path / "conflict" / "file.txt").write_text("x")
        client.post("/api/v1/libraries", json={"path": lib, "name": "Src"})
        resp = client.put(
            "/api/v1/libraries/rename",
            json={"path": lib, "name": "conflict", "rename_folder": True},
        )
        assert resp.status_code == 400
        libs = client.get("/api/v1/libraries").get_json()["libraries"]
        src_lib = next((entry for entry in libs if entry["path"] == lib), None)
        assert src_lib is not None, "library must still be registered at original path"
        assert src_lib["name"] == "Src", "display name must not be dirtied on failed folder rename"


class TestSwitchLibrary:
    def test_switch(self, lib_client):
        client, _lib1, tmp_path = lib_client
        lib2 = str(tmp_path / "lib2")
        os.makedirs(lib2)
        # Seed lib2
        analysis = [
            {
                "filepath": f"{lib2}/p_{i}.jpg",
                "date": f"2024-06-{i + 1:02d}T12:00:00",
                "date_day": f"2024-06-{i + 1:02d}",
                "date_month": "2024-06",
                "file_size": 2048,
                "file_mtime": 1700000000.0,
                "blur_raw": 200.0,
                "blur_score": 0.8,
                "exposure_score": 0.7,
                "face_score": 0.4,
                "face_count": 0,
                "largest_face_ratio": 0.0,
                "face_center_dist": 0.0,
                "composition_score": 0.6,
                "aggregate_score": 0.7,
            }
            for i in range(7)
        ]
        with open(os.path.join(lib2, "analysis.json"), "w") as f:
            json.dump(analysis, f)

        resp = client.post("/api/v1/libraries/switch", json={"path": lib2})
        assert resp.status_code == 200

        # Now photos should be from lib2
        resp = client.get("/api/v1/photos")
        data = resp.get_json()
        assert len(data["photos"]) == 7

    def test_switch_missing_path(self, lib_client):
        client, _, _ = lib_client
        resp = client.post("/api/v1/libraries/switch", json={})
        assert resp.status_code == 400

    def test_switch_nonexistent_dir(self, lib_client):
        client, _, _ = lib_client
        resp = client.post("/api/v1/libraries/switch", json={"path": "/nonexistent/dir"})
        assert resp.status_code == 400


class TestGetActive:
    def test_get_active(self, lib_client):
        client, lib_path, _ = lib_client
        resp = client.get("/api/v1/libraries/active")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "path" in data
        assert "name" in data
        assert data["path"] == lib_path


class TestRenamingTheOpenLibrary:
    def test_the_session_follows_the_renamed_folder(self, lib_client):
        """Renaming the open library's folder used to leave the running
        session, its photo rows and its threads on the old path."""
        from PIL import Image

        from bpp.db.photos import get_photo_by_path, upsert_photo

        client, lib, _tmp_path = lib_client
        app = client.application
        ctx = app.extensions["bpp"]
        assert ctx.library_path == lib
        photos = os.path.join(lib, "photos")
        os.makedirs(photos, exist_ok=True)
        old_file = os.path.join(photos, "k.jpg")
        Image.new("RGB", (8, 8), "gray").save(old_file, "JPEG")
        with app.app_context():
            upsert_photo(ctx.get_conn(), {"filepath": old_file, "date": "2024-01-01T00:00:00"})
            ctx.get_conn().commit()
        # Cached derivatives are named by a hash of the path: they must follow.
        from bpp.web.thumbnails import ThumbnailCache

        old_hash = ThumbnailCache._path_hash(old_file)
        dirs = dict(ctx.dirs)
        for folder in ("thumbs", "face_crops", "pet_crops"):
            os.makedirs(dirs[folder], exist_ok=True)
        open(os.path.join(dirs["thumbs"], f"{old_hash}.jpg"), "wb").close()
        open(os.path.join(dirs["thumbs"], f"{old_hash}_full.jpg"), "wb").close()
        open(os.path.join(dirs["face_crops"], f"{old_hash}_0_abcd.jpg"), "wb").close()
        open(os.path.join(dirs["pet_crops"], f"pet_{old_hash}_0_abcd.jpg"), "wb").close()

        resp = client.put(
            "/api/v1/libraries/rename",
            json={"path": lib, "name": "renamed", "rename_folder": True},
        )
        assert resp.status_code == 200, resp.get_json()
        new_path = resp.get_json()["new_path"]
        assert ctx.library_path == new_path
        assert ctx.workdir.startswith(new_path)
        new_file = os.path.join(new_path, "photos", "k.jpg")
        assert os.path.isfile(new_file)
        with app.app_context():
            assert get_photo_by_path(ctx.get_conn(), new_file) is not None
            assert get_photo_by_path(ctx.get_conn(), old_file) is None
        assert client.get("/api/v1/photos").status_code == 200
        new_hash = ThumbnailCache._path_hash(new_file)
        new_dirs = dict(ctx.dirs)
        assert os.path.isfile(os.path.join(new_dirs["thumbs"], f"{new_hash}.jpg"))
        assert os.path.isfile(os.path.join(new_dirs["thumbs"], f"{new_hash}_full.jpg"))
        assert os.path.isfile(os.path.join(new_dirs["face_crops"], f"{new_hash}_0_abcd.jpg"))
        assert os.path.isfile(os.path.join(new_dirs["pet_crops"], f"pet_{new_hash}_0_abcd.jpg"))
        assert not os.path.exists(os.path.join(new_dirs["thumbs"], f"{old_hash}.jpg"))

    def test_a_failed_move_leaves_the_open_library_running(self, lib_client, monkeypatch):
        client, lib, _tmp_path = lib_client
        ctx = client.application.extensions["bpp"]

        def refuse(src, dst):
            raise PermissionError("read-only volume")

        monkeypatch.setattr(os, "rename", refuse)
        resp = client.put(
            "/api/v1/libraries/rename",
            json={"path": lib, "name": "renamed", "rename_folder": True},
        )
        assert resp.status_code == 400
        assert ctx.library_path == lib
        assert client.get("/api/v1/photos").status_code == 200

    def test_a_busy_library_is_not_renamed(self, lib_client, monkeypatch):
        client, lib, _tmp_path = lib_client
        ctx = client.application.extensions["bpp"]
        worker = ctx.workers["import"]
        monkeypatch.setattr(type(worker), "is_alive", property(lambda self: True))
        resp = client.put(
            "/api/v1/libraries/rename",
            json={"path": lib, "name": "renamed", "rename_folder": True},
        )
        assert resp.status_code == 409
        assert os.path.isdir(lib)
        assert ctx.library_path == lib

    def test_a_move_whose_paths_could_not_follow_says_so(self, lib_client, monkeypatch):
        client, lib, _tmp_path = lib_client
        ctx = client.application.extensions["bpp"]

        def broken(*a, **kw):
            raise RuntimeError("database is locked")

        monkeypatch.setattr("bpp.web.state_lifecycle._rewrite_photo_paths", broken)
        resp = client.put(
            "/api/v1/libraries/rename",
            json={"path": lib, "name": "renamed", "rename_folder": True},
        )
        assert resp.status_code == 500
        assert "could not follow" in resp.get_json()["error"]
        new_path = os.path.join(os.path.dirname(lib), "renamed")
        assert ctx.library_path == new_path  # the session is on the moved folder
        libs = client.get("/api/v1/libraries").get_json()["libraries"]
        assert any(entry["path"] == new_path for entry in libs)
