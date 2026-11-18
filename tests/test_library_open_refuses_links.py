"""Opening a library refuses one whose own folders or fixed files are
links that lead somewhere else.

Startup backs up the database, rotates the previous backup, clears stale
caches and writes logs by name. A library folder prepared by someone
else can make any of those names a link to a file or folder of the
owner's; the app would then overwrite or delete through it.
"""

from __future__ import annotations

import os

import pytest

from bpp.db.library import ensure_library_dirs, get_library_dirs
from bpp.errors import ValidationError


@pytest.fixture
def lib(tmp_path):
    lib = tmp_path / "library"
    ensure_library_dirs(str(lib))
    return lib


@pytest.fixture
def outside(tmp_path):
    folder = tmp_path / "owner_docs"
    folder.mkdir()
    (folder / "notes.txt").write_text("owner file")
    return folder


@pytest.mark.parametrize("name", ["data", "cache", "logs", "cache/web_thumbs", "cache/face_crops"])
def test_a_library_folder_that_is_a_link_is_refused(lib, outside, name):
    target = lib / name
    target.rmdir() if not any(target.iterdir()) else __import__("shutil").rmtree(target)
    target.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValidationError, match="link"):
        ensure_library_dirs(str(lib))

    assert sorted(os.listdir(outside)) == ["notes.txt"]


@pytest.mark.parametrize(
    "name",
    [
        "data/photopicker.db.backup",
        "data/photopicker.db.backup.prev",
        "data/photopicker.db",
        "logs/server.log",
        "logs/server.log.1",
    ],
)
def test_a_fixed_file_that_is_a_link_is_refused(lib, outside, name):
    (lib / name).symlink_to(outside / "notes.txt")

    with pytest.raises(ValidationError, match="link"):
        ensure_library_dirs(str(lib))

    assert (outside / "notes.txt").read_text() == "owner file"


def test_a_missing_folder_behind_a_dangling_link_is_refused(lib, tmp_path):
    (lib / "logs").rmdir()
    (lib / "logs").symlink_to(tmp_path / "not_there_yet", target_is_directory=True)

    with pytest.raises(ValidationError):
        ensure_library_dirs(str(lib))

    assert not (tmp_path / "not_there_yet").exists()


def test_a_library_reached_through_a_link_still_opens(lib, tmp_path):
    """The library root itself may be a link (an external drive, a
    shortcut in the home folder): only links *inside* it are refused."""
    alias = tmp_path / "alias"
    alias.symlink_to(lib, target_is_directory=True)

    dirs = ensure_library_dirs(str(alias))

    assert os.path.isdir(dirs["data"])


def test_an_ordinary_library_opens_and_a_new_one_is_created(lib, tmp_path):
    (lib / "data" / "photopicker.db").write_bytes(b"")
    (lib / "logs" / "server.log").write_text("")
    assert ensure_library_dirs(str(lib)) == get_library_dirs(str(lib))

    fresh = tmp_path / "fresh"
    assert os.path.isdir(ensure_library_dirs(str(fresh))["thumbs"])


def test_the_switch_endpoint_reports_the_refusal(tmp_path, outside):
    from bpp.web.app import create_app

    home = tmp_path / "home_lib"
    ensure_library_dirs(str(home))
    crafted = tmp_path / "crafted"
    ensure_library_dirs(str(crafted))
    (crafted / "cache" / "web_thumbs").rmdir()
    (crafted / "cache" / "web_thumbs").symlink_to(outside, target_is_directory=True)
    app = create_app(workdir=str(home / "data"), library_path=str(home))
    app.config["TESTING"] = True

    r = app.test_client().post("/api/v1/libraries/switch", json={"path": str(crafted)})

    assert r.status_code == 400
    assert "link" in str(r.get_json()), r.get_json()
    assert app.extensions["bpp"].state["library_path"] == str(home)
    assert sorted(os.listdir(outside)) == ["notes.txt"]


def test_a_backup_copy_replaces_a_link_and_never_writes_through_it(tmp_path, outside):
    from bpp.db.backup import _copy_db_with_sidecars

    src = tmp_path / "photopicker.db"
    src.write_bytes(b"database")
    dst = tmp_path / "photopicker.db.backup"
    dst.symlink_to(outside / "notes.txt")
    (tmp_path / "photopicker.db.backup-wal").symlink_to(outside / "notes.txt")

    _copy_db_with_sidecars(str(src), str(dst))

    assert (outside / "notes.txt").read_text() == "owner file"
    assert not dst.is_symlink() and dst.read_bytes() == b"database"
    assert not (tmp_path / "photopicker.db.backup-wal").exists()


def test_a_linked_photos_folder_is_refused(lib, outside):
    (lib / "photos").rmdir()
    (lib / "photos").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValidationError, match="link"):
        ensure_library_dirs(str(lib))


def test_a_folder_that_cannot_be_read_is_refused_plainly(lib, monkeypatch):
    def denied(path):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(os, "scandir", denied)

    with pytest.raises(ValidationError, match="can't be read"):
        ensure_library_dirs(str(lib))


def test_the_cache_version_file_is_replaced_and_never_written_through(lib, outside):
    from bpp.web.thumbnails import ThumbnailCache

    version = lib / "cache" / "web_thumbs" / ".version"
    version.symlink_to(outside / "notes.txt")

    ThumbnailCache(str(lib / "cache" / "web_thumbs"))

    assert (outside / "notes.txt").read_text() == "owner file"
    assert not version.is_symlink() and version.read_text().strip()


def test_renaming_a_library_that_is_not_open_checks_it_first(tmp_path, outside):
    from bpp.db.registry import add_library
    from bpp.web.app import create_app

    home = tmp_path / "home_lib"
    ensure_library_dirs(str(home))
    other = tmp_path / "other"
    ensure_library_dirs(str(other))
    (other / "cache" / "web_thumbs").rmdir()
    (other / "cache" / "web_thumbs").symlink_to(outside, target_is_directory=True)
    app = create_app(workdir=str(home / "data"), library_path=str(home))
    app.config["TESTING"] = True
    add_library(str(other))

    r = app.test_client().put(
        "/api/v1/libraries/rename",
        json={"path": str(other), "name": "renamed", "rename_folder": True},
    )

    assert r.status_code == 400, r.get_json()
    assert "link" in str(r.get_json())
    assert other.exists() and not (tmp_path / "renamed").exists()
