"""Clear library removes only batch folders that are inside the library.

Batch names come from the library database, which travels with the
library folder: an absolute name, a ``..`` name, a link, or the name of
one of the library's own folders must never reach ``rmtree``.
"""

from __future__ import annotations

import pytest

from bpp.db.connection import init_db
from bpp.db.library import clear_library, ensure_library_dirs


@pytest.fixture
def lib(tmp_path):
    lib = tmp_path / "library"
    ensure_library_dirs(str(lib))
    return lib


@pytest.fixture
def conn(lib):
    c = init_db(str(lib / "data" / "photopicker.db"))
    yield c
    c.close()


def _photo(conn, n, batch):
    conn.execute(
        "INSERT INTO photos (filepath, sha256, original_filename, file_size, file_mtime, "
        "import_batch) VALUES (?, ?, ?, 1, 0.0, ?)",
        (f"/x/{n}.jpg", f"h{n}", f"{n}.jpg", batch),
    )
    conn.commit()


def _folder(path):
    path.mkdir(parents=True)
    (path / "keep.txt").write_text("owner file")
    return path


def test_batch_names_that_leave_the_library_delete_nothing(conn, lib, tmp_path):
    absolute = _folder(tmp_path / "owner_docs")
    sibling = _folder(tmp_path / "owner_pictures")
    linked = _folder(tmp_path / "owner_music")
    (lib / "photos" / "linked").symlink_to(linked, target_is_directory=True)
    real = _folder(lib / "photos" / "trip")
    for n, batch in enumerate([str(absolute), "../../owner_pictures", "linked", "trip", ".", ""]):
        _photo(conn, n, batch)

    result = clear_library(conn, str(lib))

    for folder in (absolute, sibling, linked):
        assert (folder / "keep.txt").exists(), f"{folder.name} was removed"
    assert not real.exists()
    assert result["folders_removed"] == 1
    assert (lib / "data" / "photopicker.db").exists()


@pytest.mark.parametrize("name", ["data", "logs", "cache", "photos"])
def test_a_batch_named_like_a_library_folder_does_not_remove_it(conn, lib, name):
    (lib / name / "keep.txt").write_text("library file")
    _photo(conn, 1, name)

    clear_library(conn, str(lib))

    assert (lib / name / "keep.txt").exists(), f"the library's own {name}/ folder was removed"


def test_a_legacy_batch_folder_at_the_library_root_is_still_removed(conn, lib):
    legacy = _folder(lib / "summer_2019")
    _photo(conn, 1, "summer_2019")

    result = clear_library(conn, str(lib))

    assert not legacy.exists()
    assert result["folders_removed"] == 1


def test_a_photos_folder_swapped_for_a_link_after_open_deletes_nothing_outside(conn, lib, tmp_path):
    """The check runs on what is on disk when the delete happens, so a
    link put in place of photos/ while the library is open is caught."""
    owner = tmp_path / "owner_pictures"
    batch = _folder(owner / "Trip2024")
    (lib / "photos").rmdir()
    (lib / "photos").symlink_to(owner, target_is_directory=True)
    _photo(conn, 1, "Trip2024")

    result = clear_library(conn, str(lib))

    assert (batch / "keep.txt").exists()
    assert result["folders_removed"] == 0


@pytest.mark.parametrize("name", ["Data", "LOGS", "Cache"])
def test_a_batch_named_like_a_library_folder_in_another_case_does_not_remove_it(conn, lib, name):
    """On a case-insensitive volume (the macOS default) ``Data`` is the
    library's ``data`` folder."""
    (lib / name.lower() / "keep.txt").write_text("library file")
    _photo(conn, 1, name)

    clear_library(conn, str(lib))

    assert (lib / name.lower() / "keep.txt").exists()
