"""Edge cases of unpacking archives and of the note of deleted photos."""

from __future__ import annotations

import errno
import io
import json
import os
import tarfile
import threading
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
    assert seen and seen[0].startswith("Could not extract archive")


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


# ── the note of deleted photos ───────────────────────────────────────────


def test_a_pipe_at_the_notes_name_is_not_read(tmp_path):
    from bpp.db.analysis_leftovers import PENDING, pending_forgets

    os.mkfifo(tmp_path / PENDING)
    result: list = []
    reader = threading.Thread(target=lambda: result.append(pending_forgets(str(tmp_path))))
    reader.start()
    reader.join(5)
    if reader.is_alive():  # unblock it before failing
        with open(tmp_path / PENDING, "w"):
            pass
        pytest.fail("reading the note blocked on a pipe")
    assert result == [set()]


def test_a_failed_settling_of_companions_does_not_stop_the_start(tmp_path, monkeypatch):
    import sqlite3

    from bpp.web import state_init
    from bpp.web.app import create_app

    def locked(conn):
        conn.execute("UPDATE photos SET is_live_photo_sidecar = 0 WHERE 0")
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(state_init, "settle_companions_of_binned", locked)
    app = create_app(library_path=str(tmp_path / "library"))
    with app.app_context():
        assert not app.extensions["bpp"].get_conn().in_transaction


# ── unfinished unpackings: set aside at open, unless one is in use ───────


def _library_with_leftover(tmp_path):
    from bpp.db.library import ensure_library_dirs

    lib = tmp_path / "library"
    work = ensure_library_dirs(str(lib))["data"]
    base = os.path.join(work, "extracted")
    os.makedirs(os.path.join(base, ".unpacking-left", "x"))
    os.symlink(tmp_path, os.path.join(base, ".unpacking-link"), target_is_directory=True)
    return lib, work, base


def test_leftover_unpackings_are_removed_after_the_library_opens(tmp_path):
    from bpp.web.app import create_app
    from bpp.web.archive_placement import wait_for_removals

    lib, work, base = _library_with_leftover(tmp_path)

    create_app(workdir=work, library_path=str(lib))  # as `bpp serve` opens it
    wait_for_removals()

    assert sorted(os.listdir(base)) == [".unpacking-link"], "a link is not followed or removed"


def test_a_library_another_server_holds_keeps_its_unpackings(tmp_path):
    from bpp.web.app import create_app

    lib, work, base = _library_with_leftover(tmp_path)
    with open(os.path.join(work, ".serving.lock"), "w") as f:
        f.write(str(os.getppid()))  # a live process that is not this one

    with pytest.raises(RuntimeError, match="Another bpp server"):
        create_app(workdir=work, library_path=str(lib))

    assert os.path.isdir(os.path.join(base, ".unpacking-left")), "it may be that server's"


@pytest.mark.parametrize("can_lock", [True, False], ids=["locks", "a drive that can't lock"])
def test_opening_the_library_again_mid_unpacking_leaves_the_unpacking_alone(
    tmp_path, monkeypatch, can_lock
):
    from bpp.web import analyze_archive, archive_placement
    from bpp.web.archive_placement import set_aside_and_remove_in_background, wait_for_removals

    if not can_lock:
        monkeypatch.setattr(archive_placement, "_try_lock", lambda fd: None)

    work = str(tmp_path / "data")
    archive = _tar(tmp_path / "a.tar", *((f"p{i}.jpg", b"x") for i in range(10)))
    real_extract = analyze_archive._extract_tar

    def library_opens_again_halfway(tf, members, dest):  # as init_app_db does
        real_extract(tf, members[:5], dest)
        set_aside_and_remove_in_background(work)
        wait_for_removals()
        real_extract(tf, members[5:], dest)

    monkeypatch.setattr(analyze_archive, "_extract_tar", library_opens_again_halfway)

    out = analyze_archive.extract_archive_into_workdir(archive, work, lambda e: None)

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

    base = tmp_path / "extracted"
    base.mkdir()
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(base)],
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

    os.makedirs(os.path.join(base, ".unpacking-left"))  # a crash left one
    sweep_unfinished_unpacking(str(tmp_path))
    assert os.listdir(base) == []


# ── a later analysis never ends a deletion ───────────────────────────────


def _external_photo_deleted(tmp_path):
    from bpp.db import analysis_leftovers as leftovers

    work = tmp_path / "data"
    work.mkdir()
    photo = tmp_path / "external.jpg"  # an original outside the library stays on disk
    photo.write_bytes(b"x")
    leftovers._note_pending(str(work), [str(photo)])  # its rewrite has not run yet
    return work, str(photo)


def test_a_command_line_analysis_written_after_a_delete_keeps_the_photo_out(tmp_path, monkeypatch):
    from bpp.db import analysis_leftovers as leftovers
    from bpp.db.connection import init_db
    from bpp.scoring import aggregate
    from bpp.web.state_init_phases import import_from_legacy_caches

    work, photo = _external_photo_deleted(tmp_path)
    old = {"filepath": photo, "blur_raw": 200.0, "gps_lat": 39.1}  # scored before the delete
    monkeypatch.setattr(aggregate, "parallel_map", lambda *a, **k: [old])

    aggregate.analyze_all([photo], str(work), {}, workers=1)

    assert leftovers.pending_forgets(str(work)) == {photo}
    conn = init_db(str(work / "photopicker.db"))
    import_from_legacy_caches(conn, str(work), str(tmp_path), 0)
    assert conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 0


def test_an_analysis_in_the_app_does_not_clear_a_note(tmp_path, offline_downloads):
    from unittest.mock import patch

    from bpp.db import analysis_leftovers as leftovers
    from bpp.web.analyze_worker import AnalyzeWorker

    work, photo = _external_photo_deleted(tmp_path)
    scored = [
        {
            "filepath": photo,
            "date": "2024-01-01T00:00:00",
            "file_size": 1,
            "file_mtime": 1.0,
            "blur_raw": 100.0,
            "blur_score": 0.5,
            "exposure_score": 0.5,
            "face_score": 0.5,
            "composition_score": 0.5,
            "face_count": 0,
        }
    ]
    worker = AnalyzeWorker()
    with patch.object(AnalyzeWorker, "_score", return_value=scored):
        worker.start(str(tmp_path), str(work), {"max_long_side": 256}, [".jpg"])
        worker._thread.join(timeout=60)

    assert leftovers.pending_forgets(str(work)) == {photo}


# ── a note in the library root ───────────────────────────────────────────


def _root_with_note(tmp_path, *paths):
    from bpp.db import analysis_leftovers as leftovers

    root, work = tmp_path, tmp_path / "data"
    work.mkdir()
    with open(root / "analysis.json", "w") as f:
        json.dump([{"filepath": p} for p in paths], f)
    leftovers._note_pending(str(root), [paths[0]])  # a session that kept its data in the root
    return root, work


def test_a_note_in_the_library_root_is_honoured_and_then_done(tmp_path):
    from bpp.db.analysis_leftovers import PENDING
    from bpp.db.connection import init_db
    from bpp.web.state_init_phases import import_from_legacy_caches

    root, work = _root_with_note(tmp_path, "/p/gone.jpg", "/p/b.jpg")
    conn = init_db(str(work / "photopicker.db"))

    import_from_legacy_caches(conn, str(work), str(root), 0)

    assert [r[0] for r in conn.execute("SELECT filepath FROM photos")] == ["/p/b.jpg"]
    assert not (root / PENDING).exists()
    assert [d["filepath"] for d in json.loads((root / "analysis.json").read_text())] == ["/p/b.jpg"]


def test_clear_library_removes_a_note_in_the_library_root(tmp_path):
    from bpp.db.analysis_leftovers import PENDING, forget_analysis_files

    root, work = _root_with_note(tmp_path, "/p/gone.jpg")

    forget_analysis_files(str(work), str(root))

    assert not (root / PENDING).exists()


def test_a_photo_added_back_after_its_delete_keeps_its_analysis(tmp_path):
    from bpp.db import analysis_leftovers as leftovers
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo

    work, photo = _external_photo_deleted(tmp_path)
    with open(work / "analysis.json", "w") as f:
        json.dump([{"filepath": photo}], f)
    upsert_photo(init_db(str(work / "photopicker.db")), {"filepath": photo})  # added back

    leftovers.apply_pending_forgets(str(work))

    assert json.loads((work / "analysis.json").read_text()) == [{"filepath": photo}]
    assert leftovers.pending_forgets(str(work)) == set()


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
    assert seen and seen[0].startswith("Could not extract archive")


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
    assert seen and "damaged" in seen[0]
