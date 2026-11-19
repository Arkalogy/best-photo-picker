"""Folders analyzed in place stay exportable: across restarts and
library switches, and through links the owner had followed."""

from __future__ import annotations

import os

import pytest

# ── folders analyzed in place stay exportable ────────────────────────────


def test_folders_analyzed_in_place_are_remembered_per_library(tmp_path):
    from bpp.web import export_sources

    lib_a, lib_b = str(tmp_path / "a"), str(tmp_path / "b")
    export_sources.remember(lib_a, "/Volumes/Trip", followed=False)
    export_sources.remember(lib_a, "/Volumes/Linked", followed=True)
    export_sources.remember(lib_b, "/Volumes/Other", followed=False)

    assert export_sources.remembered(lib_a) == (
        {"/Volumes/Trip", "/Volumes/Linked"},
        {"/Volumes/Linked"},
    )
    assert export_sources.remembered(lib_b) == ({"/Volumes/Other"}, set())
    assert export_sources.remembered(str(tmp_path / "never-opened")) == (set(), set())
    assert export_sources.remembered(None) == (set(), set())


def test_the_remembered_folders_are_bounded_and_a_damaged_file_is_ignored(tmp_path, monkeypatch):
    from bpp.web import export_sources

    lib = str(tmp_path / "a")
    monkeypatch.setattr(export_sources, "MAX_FOLDERS", 3)
    for i in range(5):
        export_sources.remember(lib, f"/f{i}", followed=False)
    assert export_sources.remembered(lib)[0] == {"/f2", "/f3", "/f4"}

    with open(export_sources._file(), "w") as f:
        f.write('{"' + os.path.realpath(lib) + '": ["not", "a", "mapping"], "x": 3}')
    assert export_sources.remembered(lib) == (set(), set())


def test_another_library_put_at_the_same_path_does_not_inherit_the_folders(tmp_path):
    from bpp.web import export_sources

    lib = tmp_path / "lib"
    (lib / "data").mkdir(parents=True)
    db = lib / "data" / "photopicker.db"
    db.write_bytes(b"the owner's library")
    export_sources.remember(str(lib), "/Volumes/Trip", followed=True)
    assert export_sources.remembered(str(lib))[0] == {"/Volumes/Trip"}

    (lib / "data" / "theirs.db").write_bytes(b"someone else's library")
    os.replace(lib / "data" / "theirs.db", db)

    assert export_sources.remembered(str(lib)) == (set(), set())


def test_a_new_run_and_a_library_switch_pick_up_the_remembered_folders(tmp_path):
    from bpp.db.library import ensure_library_dirs
    from bpp.web import export_sources
    from bpp.web.state import WebAppState

    lib = str(tmp_path / "library")
    ensure_library_dirs(lib)
    export_sources.remember(lib, "/Volumes/Trip", followed=True)

    ctx = WebAppState(input_dir="", workdir="", library_path=lib)

    assert ctx.export_source_dirs == {"/Volumes/Trip"}
    assert ctx.export_followed_dirs == {"/Volumes/Trip"}


def test_a_photo_reached_through_a_followed_link_is_inside_only_the_owners_folder(tmp_path):
    from bpp.utils.path_validation import FolderSet

    trip, elsewhere, library = tmp_path / "Trip", tmp_path / "Ext", tmp_path / "lib"
    for folder in (trip, elsewhere / "raw", library):
        folder.mkdir(parents=True)
    (elsewhere / "raw" / "a.jpg").write_bytes(b"x")
    (trip / "raw").symlink_to(elsewhere / "raw", target_is_directory=True)
    (library / "raw").symlink_to(elsewhere / "raw", target_is_directory=True)
    through_trip, through_library = str(trip / "raw" / "a.jpg"), str(library / "raw" / "a.jpg")

    not_followed = FolderSet([str(trip), str(library)])
    followed = FolderSet([str(trip), str(library)], followed=[str(trip)])

    assert not not_followed.holds(through_trip)
    assert followed.holds(through_trip)
    assert not followed.holds(through_library), "a link inside the library is not the owner's"
    assert not followed.holds(str(trip / ".." / "Ext" / "raw" / "a.jpg"))
    (elsewhere / "secret.jpg").write_bytes(b"x")
    assert not followed.holds(str(trip / "raw" / ".." / "secret.jpg")), "'..' after a link"
    assert not followed.holds(str(tmp_path / "Trip2" / "a.jpg"))


def test_a_switch_that_fails_keeps_the_open_librarys_folders(tmp_path, monkeypatch):
    from bpp.web.app import create_app

    app = create_app(library_path=str(tmp_path / "library"))
    ctx = app.extensions["bpp"]
    ctx.export_source_dirs.add("/Volumes/Trip")
    ctx.export_followed_dirs.add("/Volumes/Trip")
    other = tmp_path / "other"
    other.mkdir()
    real, calls = ctx.startup, []

    def startup():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the other library refuses to open")
        real()

    monkeypatch.setattr(ctx, "startup", startup)
    with app.app_context(), pytest.raises(RuntimeError):
        ctx.switch_library(str(other))

    assert ctx.export_source_dirs == {"/Volumes/Trip"}
    assert ctx.export_followed_dirs == {"/Volumes/Trip"}


def test_roots_are_looked_at_only_until_the_file_is_found(tmp_path, monkeypatch):
    from bpp.utils import path_validation
    from bpp.utils.path_validation import FolderSet

    library, gone_share = tmp_path / "lib", tmp_path / "share-that-went-away"
    (library / "photos").mkdir(parents=True)
    gone_share.mkdir()
    looked_at = []
    real = os.path.realpath

    def realpath(path, *a, **k):
        looked_at.append(str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(path_validation.os.path, "realpath", realpath)
    roots = FolderSet([str(library), str(gone_share)])

    assert roots.holds(str(library / "photos" / "a.jpg"))
    assert str(gone_share) not in looked_at, "a photo inside the library never touches it"
    assert not roots.holds(str(tmp_path / "elsewhere" / "a.jpg"))
    assert str(gone_share) in looked_at
    assert roots.holds(str(gone_share / "a.jpg"))
