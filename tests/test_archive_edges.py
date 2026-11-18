"""Edge cases of unpacking archives."""

from __future__ import annotations

import errno
import io
import os
import tarfile
import zipfile

import pytest


def _tar(path, *files) -> str:
    with tarfile.open(path, "w") as tf:
        for name, data in files:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o644
            tf.addfile(info, io.BytesIO(data))
    return str(path)


def _errors():
    seen: list[str] = []
    return seen, lambda e: seen.append(e["message"]) if e.get("type") == "error" else None


# ── a clash between a file and a folder changes nothing ──────────────────


def test_a_file_where_the_earlier_unpacking_has_a_folder_changes_nothing(tmp_path):
    from bpp.web.analyze_archive import _archive_folder, extract_archive_into_workdir

    work = tmp_path / "data"
    archive = _tar(tmp_path / "a.tar", ("a.jpg", b"NEW"), ("b", b"NEW"))
    folder = work / "extracted" / _archive_folder(archive)
    (folder / "b").mkdir(parents=True)
    (folder / "a.jpg").write_bytes(b"OLD")
    seen, emit = _errors()

    assert extract_archive_into_workdir(archive, str(work), emit) is None

    assert (folder / "a.jpg").read_bytes() == b"OLD", "not replaced before the clash was found"
    assert "'b'" in seen[0] and "Nothing was changed" in seen[0]


# ── room, damaged and locked archives ────────────────────────────────────


def test_an_archive_that_does_not_fit_is_refused_with_the_numbers(tmp_path, monkeypatch):
    import shutil

    from bpp.web.analyze_archive import extract_archive_into_workdir

    archive = _tar(tmp_path / "a.tar", ("a.jpg", b"x" * 5000))
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(1, 1, 10))
    seen, emit = _errors()

    assert extract_archive_into_workdir(archive, str(tmp_path / "data"), emit) is None
    assert "Not enough free disk space" in seen[0]


def test_a_zip_with_damaged_data_is_reported_not_raised(tmp_path):
    from bpp.web.analyze_archive import extract_archive_into_workdir

    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a.jpg", os.urandom(5000))
    raw = bytearray(path.read_bytes())
    start = 30 + len("a.jpg")  # the first entry's data follows its 30-byte header and name
    raw[start : start + 4] = b"\xff\xff\xff\xff"  # not a deflate block: zlib.error
    path.write_bytes(bytes(raw))
    seen, emit = _errors()

    assert extract_archive_into_workdir(str(path), str(tmp_path / "data"), emit) is None
    assert seen and "it is damaged or holds an unsafe entry" in seen[0], seen


@pytest.mark.parametrize(
    ("error", "says"),
    [
        (OSError(errno.ENOSPC, "No space left on device"), "the disk is full"),
        (RuntimeError("File 'a.jpg' is encrypted, password required"), "password-protected"),
        (NotImplementedError("That compression method is not supported"), "compression"),
        (EOFError(), "damaged"),
        (PermissionError(errno.EACCES, "Permission denied"), "Permission denied: check"),
    ],
)
def test_each_kind_of_failure_is_said_plainly(error, says):
    from bpp.web.analyze_archive import _report_unpack_failure

    seen, emit = _errors()
    _report_unpack_failure(error, emit)
    assert says in seen[0]


# ── unfinished unpackings: set aside at open, unless one is in use ───────


def _library_with_leftover(tmp_path):
    from bpp.db.library import ensure_library_dirs

    lib = tmp_path / "library"
    work = ensure_library_dirs(str(lib))["data"]
    staging = os.path.join(work, ".unpacking")
    os.makedirs(os.path.join(staging, "run-left", "x"))
    os.symlink(tmp_path, os.path.join(staging, "run-link"), target_is_directory=True)
    return lib, work, staging


def test_leftover_unpackings_are_removed_after_the_library_opens(tmp_path):
    from bpp.web.app import create_app
    from bpp.web.archive_placement import wait_for_removals

    lib, work, staging = _library_with_leftover(tmp_path)

    create_app(workdir=work, library_path=str(lib))  # as `bpp serve` opens it
    wait_for_removals()

    assert sorted(os.listdir(staging)) == ["run-link"], "a link is not followed or removed"


def test_folders_an_earlier_version_unpacked_are_never_taken_for_leftovers(tmp_path):
    from bpp.db.library import ensure_library_dirs
    from bpp.web.app import create_app
    from bpp.web.archive_placement import wait_for_removals

    lib = tmp_path / "library"
    work = ensure_library_dirs(str(lib))["data"]
    names = (".discarded-duplicates", ".unpacking-old", "run-1", "discarded-2")
    for name in names:  # archives once went straight into extracted/, under any name
        os.makedirs(os.path.join(work, "extracted", name))
        with open(os.path.join(work, "extracted", name, "photo.jpg"), "wb") as f:
            f.write(b"an imported photo")

    create_app(workdir=work, library_path=str(lib))
    wait_for_removals()

    for name in names:
        assert os.path.isfile(os.path.join(work, "extracted", name, "photo.jpg")), name


def test_a_link_in_place_of_the_unpacking_folder_is_never_followed(tmp_path):
    from bpp.web.analyze_archive import extract_archive_into_workdir
    from bpp.web.archive_placement import sweep_unfinished_unpacking

    work = tmp_path / "data"
    work.mkdir()
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "run-x").mkdir(parents=True)
    (work / ".unpacking").symlink_to(elsewhere, target_is_directory=True)

    sweep_unfinished_unpacking(str(work))
    assert (elsewhere / "run-x").is_dir(), "a sweep does not reach through the link"

    out = extract_archive_into_workdir(_tar(tmp_path / "a.tar", ("a.jpg", b"x")), str(work), print)
    assert out and os.listdir(out) == ["a.jpg"]
    assert not (work / ".unpacking").is_symlink() and os.listdir(elsewhere) == ["run-x"]


def test_a_library_another_server_holds_keeps_its_unpackings(tmp_path):
    from bpp.web.app import create_app

    lib, work, staging = _library_with_leftover(tmp_path)
    with open(os.path.join(work, ".serving.lock"), "w") as f:
        f.write(str(os.getppid()))  # a live process that is not this one

    with pytest.raises(RuntimeError, match="Another bpp server"):
        create_app(workdir=work, library_path=str(lib))

    assert os.path.isdir(os.path.join(staging, "run-left")), "it may be that server's"


@pytest.mark.parametrize("spelling", ["link", "case"], ids=["by a link", "in another case"])
@pytest.mark.parametrize("can_lock", [True, False], ids=["locks", "a drive that can't lock"])
def test_opening_the_library_again_mid_unpacking_leaves_the_unpacking_alone(
    tmp_path, monkeypatch, can_lock, spelling
):
    from bpp.web import analyze_archive, archive_placement
    from bpp.web.archive_placement import set_aside_and_remove_in_background, wait_for_removals

    if not can_lock:
        monkeypatch.setattr(archive_placement, "_try_lock", lambda fd: None)
    work = tmp_path / "data"
    work.mkdir()
    if spelling == "link":
        alias = tmp_path / "alias"  # the same library, by another path
        alias.symlink_to(work, target_is_directory=True)
    else:
        alias = tmp_path / "DATA"
        if not alias.exists():
            pytest.skip("this volume tells letter cases apart")
    archive = _tar(tmp_path / "a.tar", *((f"p{i}.jpg", b"x") for i in range(10)))
    real_extract = analyze_archive._extract_tar

    def library_opens_again_halfway(tf, members, dest):  # as init_app_db does
        real_extract(tf, members[:5], dest)
        set_aside_and_remove_in_background(str(alias))
        wait_for_removals()
        real_extract(tf, members[5:], dest)

    monkeypatch.setattr(analyze_archive, "_extract_tar", library_opens_again_halfway)

    out = analyze_archive.extract_archive_into_workdir(archive, str(work), lambda e: None)

    assert out is not None and len(os.listdir(out)) == 10


_HOLDER = """
import sys
from bpp.web.archive_placement import unpacking_folder
with unpacking_folder(sys.argv[1]) as content:
    print("HOLDING", content, flush=True)
    sys.stdin.readline()
"""


def test_an_unpacking_another_process_holds_is_left_alone(tmp_path):
    import subprocess
    import sys

    from bpp.web.archive_placement import sweep_unfinished_unpacking

    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    try:
        line = next((x for x in holder.stdout if x.startswith("HOLDING ")), "")
        content = line[len("HOLDING ") :].strip()
        assert content, "the other process made no unpacking folder"

        sweep_unfinished_unpacking(str(tmp_path))
        assert os.path.isdir(content), "an unpacking in use was removed"
    finally:
        holder.communicate("\n", timeout=30)

    os.makedirs(tmp_path / ".unpacking" / "run-left")  # a crash left one
    sweep_unfinished_unpacking(str(tmp_path))
    assert os.listdir(tmp_path / ".unpacking") == []


# ── sparse members, merged folders and damaged archives ──────────────────


def _sparse_tar(path, data: bytes, pieces: str, size: int) -> str:
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo("photo.jpg")
        info.size = len(data)
        info.pax_headers = {"GNU.sparse.map": pieces, "GNU.sparse.size": str(size)}
        tf.addfile(info, io.BytesIO(data))
        ok = tarfile.TarInfo("ok.jpg")
        ok.size = 1
        tf.addfile(ok, io.BytesIO(b"x"))
    return str(path)


def test_a_sparse_member_with_a_piece_beyond_its_size_is_not_unpacked(tmp_path):
    from bpp.web.analyze_archive import extract_archive_into_workdir

    archive = _sparse_tar(tmp_path / "a.tar", b"x" * 4096, "0,4096", 1)

    out = extract_archive_into_workdir(archive, str(tmp_path / "data"), lambda e: None)

    assert sorted(os.listdir(out)) == ["ok.jpg"]


def test_a_sound_sparse_member_is_unpacked(tmp_path):
    from bpp.web.analyze_archive import extract_archive_into_workdir

    archive = _sparse_tar(tmp_path / "a.tar", b"abc", "0,3", 10)

    out = extract_archive_into_workdir(archive, str(tmp_path / "data"), lambda e: None)

    with open(os.path.join(out, "photo.jpg"), "rb") as f:
        assert f.read() == b"abc" + bytes(7)


def _album_tar(path, *files) -> str:
    with tarfile.open(path, "w") as tf:
        folder = tarfile.TarInfo("album")
        folder.type, folder.mtime, folder.mode = tarfile.DIRTYPE, 123456789, 0o755
        tf.addfile(folder)
        for name, data in files:
            photo = tarfile.TarInfo(f"album/{name}")
            photo.size = len(data)
            tf.addfile(photo, io.BytesIO(data))
    return str(path)


def test_a_folder_merged_on_a_second_unpacking_keeps_the_archives_date(tmp_path):
    from bpp.web.analyze_archive import extract_archive_into_workdir

    archive = _album_tar(tmp_path / "a.tar", ("a.jpg", b"x"))
    work = str(tmp_path / "data")

    extract_archive_into_workdir(archive, work, lambda e: None)
    out = extract_archive_into_workdir(archive, work, lambda e: None)  # analysed again

    assert int(os.stat(os.path.join(out, "album")).st_mtime) == 123456789


def test_a_folder_keeps_the_archives_date_when_photos_already_imported_are_dropped(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo
    from bpp.web.analyze_archive import extract_archive_into_workdir

    work = tmp_path / "data"
    old = work / "extracted" / "album" / "a.jpg"  # where earlier versions unpacked it
    old.parent.mkdir(parents=True)
    old.write_bytes(b"x")
    upsert_photo(init_db(str(work / "photopicker.db")), {"filepath": str(old)})
    archive = _album_tar(tmp_path / "a.tar", ("a.jpg", b"x"), ("b.jpg", b"y"))

    out = extract_archive_into_workdir(archive, str(work), lambda e: None)

    assert os.listdir(os.path.join(out, "album")) == ["b.jpg"]
    assert int(os.stat(os.path.join(out, "album")).st_mtime) == 123456789


def test_a_zip_with_a_damaged_directory_is_reported(tmp_path, monkeypatch):
    from bpp.web import analyze_archive

    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("a.jpg", b"x")
    monkeypatch.setattr(analyze_archive.zipfile, "is_zipfile", lambda p: True)
    path.write_bytes(path.read_bytes()[:-10])  # the end of the directory is cut off
    seen, emit = _errors()

    assert (
        analyze_archive.extract_archive_into_workdir(str(path), str(tmp_path / "d"), emit) is None
    )
    assert seen and "it is damaged or holds an unsafe entry" in seen[0], seen


def test_a_file_that_is_no_archive_this_app_reads_is_said_plainly(tmp_path):
    from bpp.web.analyze_archive import extract_archive_into_workdir

    path = tmp_path / "a.zip"
    path.write_bytes(b"photos, but not an archive\n" * 10)
    seen, emit = _errors()

    assert extract_archive_into_workdir(str(path), str(tmp_path / "data"), emit) is None
    assert "not a zip or tar archive this app can read, or it is damaged" in seen[0]


def test_a_tar_xz_damaged_late_is_reported(tmp_path):
    from bpp.web.analyze_archive import extract_archive_into_workdir

    path = tmp_path / "a.tar.xz"
    with tarfile.open(path, "w:xz") as tf:
        for i in range(20):
            info = tarfile.TarInfo(f"p{i}.jpg")
            data = os.urandom(4096)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    raw = bytearray(path.read_bytes())
    raw[len(raw) // 2 : len(raw) // 2 + 64] = bytes(64)  # damaged in the middle
    path.write_bytes(bytes(raw))
    seen, emit = _errors()

    assert extract_archive_into_workdir(str(path), str(tmp_path / "data"), emit) is None
    assert seen and "it is damaged or holds an unsafe entry" in seen[0], seen


def test_a_tar_gz_cut_short_is_reported(tmp_path):
    from bpp.web.analyze_archive import extract_archive_into_workdir

    path = tmp_path / "a.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        for i in range(20):
            info = tarfile.TarInfo(f"p{i}.jpg")
            data = os.urandom(4096)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    path.write_bytes(path.read_bytes()[: path.stat().st_size // 2])
    seen, emit = _errors()

    assert extract_archive_into_workdir(str(path), str(tmp_path / "data"), emit) is None
    assert seen and "it is damaged or holds an unsafe entry" in seen[0], seen


def test_clear_library_removes_a_leftover_unpacking(tmp_path):
    from bpp.db.library import ensure_library_dirs
    from bpp.web.app import create_app

    lib = tmp_path / "library"
    work = ensure_library_dirs(str(lib))["data"]
    app = create_app(workdir=work, library_path=str(lib))
    app.config["TESTING"] = True
    leftover = os.path.join(work, ".unpacking", "run-left")
    os.makedirs(leftover)  # a crash left an archive's photos here
    with open(os.path.join(leftover, "photo.jpg"), "wb") as f:
        f.write(b"x")

    with app.test_client() as client:
        r = client.delete("/api/v1/library", json={"confirmation": "delete"})

    assert r.status_code == 200, r.get_json()
    assert os.listdir(os.path.join(work, ".unpacking")) == []
