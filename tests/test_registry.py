"""Tests for the library registry (db/registry.py)."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def registry_dir(tmp_path, monkeypatch):
    """Point registry to a temp config directory."""
    config_dir = str(tmp_path / "config" / "bpp")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return config_dir


class TestRegistryPath:
    def test_get_registry_path_uses_xdg(self, registry_dir):
        from bpp.db.registry import get_registry_path

        path = get_registry_path()
        assert path == os.path.join(registry_dir, "libraries.json")

    def test_get_registry_path_default_home(self, tmp_path, monkeypatch):
        from bpp.db.registry import get_registry_path

        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        path = get_registry_path()
        assert ".config/bpp/libraries.json" in path


class TestLoadSave:
    def test_load_empty_returns_default(self, registry_dir):
        from bpp.db.registry import load_registry

        data = load_registry()
        assert data == {"libraries": [], "active": None}

    def test_save_and_load_roundtrip(self, registry_dir):
        from bpp.db.registry import load_registry, save_registry

        data = {"libraries": [{"path": "/tmp/lib1", "name": "Lib1"}], "active": "/tmp/lib1"}
        save_registry(data)
        loaded = load_registry()
        assert loaded == data

    def test_save_creates_directory(self, registry_dir):
        from bpp.db.registry import get_registry_path, save_registry

        assert not os.path.exists(registry_dir)
        save_registry({"libraries": [], "active": None})
        assert os.path.exists(get_registry_path())

    def test_load_corrupted_returns_default(self, registry_dir):
        from bpp.db.registry import get_registry_path, load_registry

        os.makedirs(registry_dir, exist_ok=True)
        with open(get_registry_path(), "w") as f:
            f.write("not json{{{")
        data = load_registry()
        assert data == {"libraries": [], "active": None}


class TestAddLibrary:
    def test_add_library(self, registry_dir, tmp_path):
        from bpp.db.registry import add_library, list_libraries

        lib_path = str(tmp_path / "mylib")
        os.makedirs(lib_path)
        entry = add_library(lib_path, "My Library")
        assert entry["path"] == lib_path
        assert entry["name"] == "My Library"
        assert "created_at" in entry
        assert "last_opened" in entry

        libs = list_libraries()
        assert len(libs) == 1
        assert libs[0]["path"] == lib_path

    def test_add_library_default_name(self, registry_dir, tmp_path):
        from bpp.db.registry import add_library

        lib_path = str(tmp_path / "vacation_photos")
        os.makedirs(lib_path)
        entry = add_library(lib_path)
        assert entry["name"] == "vacation_photos"

    def test_add_duplicate_path_updates(self, registry_dir, tmp_path):
        from bpp.db.registry import add_library, list_libraries

        lib_path = str(tmp_path / "mylib")
        os.makedirs(lib_path)
        add_library(lib_path, "Name1")
        add_library(lib_path, "Name2")
        libs = list_libraries()
        assert len(libs) == 1
        assert libs[0]["name"] == "Name2"


class TestRemoveLibrary:
    def test_remove_library(self, registry_dir, tmp_path):
        from bpp.db.registry import add_library, list_libraries, remove_library

        lib_path = str(tmp_path / "mylib")
        os.makedirs(lib_path)
        add_library(lib_path, "MyLib")
        remove_library(lib_path)
        assert list_libraries() == []
        # Directory still exists on disk
        assert os.path.isdir(lib_path)

    def test_remove_nonexistent_is_noop(self, registry_dir):
        from bpp.db.registry import remove_library

        remove_library("/nonexistent/path")  # Should not raise


class TestRenameLibrary:
    def test_rename_library(self, registry_dir, tmp_path):
        from bpp.db.registry import add_library, list_libraries, rename_library

        lib_path = str(tmp_path / "mylib")
        os.makedirs(lib_path)
        add_library(lib_path, "OldName")
        rename_library(lib_path, "NewName")
        libs = list_libraries()
        assert libs[0]["name"] == "NewName"

    def test_rename_nonexistent_is_noop(self, registry_dir):
        from bpp.db.registry import rename_library

        rename_library("/nonexistent", "Name")  # Should not raise


class TestActiveLibrary:
    def test_get_active_default_none(self, registry_dir):
        from bpp.db.registry import get_active_library

        assert get_active_library() is None

    def test_set_and_get_active(self, registry_dir, tmp_path):
        from bpp.db.registry import (
            add_library,
            get_active_library,
            set_active_library,
        )

        lib1 = str(tmp_path / "lib1")
        lib2 = str(tmp_path / "lib2")
        os.makedirs(lib1)
        os.makedirs(lib2)
        add_library(lib1, "Lib1")
        add_library(lib2, "Lib2")
        set_active_library(lib1)
        assert get_active_library() == lib1
        set_active_library(lib2)
        assert get_active_library() == lib2

    def test_set_active_updates_last_opened(self, registry_dir, tmp_path):
        from bpp.db.registry import add_library, list_libraries, set_active_library

        lib_path = str(tmp_path / "mylib")
        os.makedirs(lib_path)
        entry = add_library(lib_path, "MyLib")
        old_opened = entry["last_opened"]
        set_active_library(lib_path)
        libs = list_libraries()
        assert libs[0]["last_opened"] >= old_opened


class TestListLibraries:
    def test_sorted_by_last_opened(self, registry_dir, tmp_path):
        from bpp.db.registry import add_library, list_libraries, set_active_library

        lib1 = str(tmp_path / "lib1")
        lib2 = str(tmp_path / "lib2")
        lib3 = str(tmp_path / "lib3")
        for p in [lib1, lib2, lib3]:
            os.makedirs(p)
        add_library(lib1, "Lib1")
        add_library(lib2, "Lib2")
        add_library(lib3, "Lib3")
        # Make lib2 most recent
        set_active_library(lib2)
        libs = list_libraries()
        assert libs[0]["path"] == lib2
