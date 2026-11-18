"""Edge cases of unpacking archives and of the note of deleted photos."""

from __future__ import annotations

import errno
import io
import json
import os
import tarfile
import threading
import time
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
    ],
)
def test_each_kind_of_failure_is_said_plainly(error, says):
    from bpp.web.analyze_archive import _report_unpack_failure

    seen, emit = _errors()
    _report_unpack_failure(error, emit)
    assert says in seen[0]


# ── unfinished unpackings are removed when the library opens ─────────────


def test_only_old_unfinished_unpackings_are_removed_at_open(tmp_path):
    from bpp.web.archive_placement import sweep_unfinished_unpacking

    base = tmp_path / "extracted"
    old, recent = base / ".unpacking-old", base / ".unpacking-now"
    for folder in (old, recent):
        (folder / "x").mkdir(parents=True)
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    (base / ".unpacking-link").symlink_to(tmp_path, target_is_directory=True)

    assert sweep_unfinished_unpacking(str(tmp_path), older_than_s=600) == 1

    assert not old.exists() and recent.exists()
    assert (base / ".unpacking-link").is_symlink(), "a link is not followed or removed"


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


def test_a_photo_back_on_disk_and_analysed_again_loses_its_note(tmp_path):
    from bpp.db import analysis_leftovers as leftovers
    from bpp.scoring.aggregate import load_analysis

    work = str(tmp_path)
    back = tmp_path / "IMG_1.jpg"
    back.write_bytes(b"restored from a backup")
    still_gone = str(tmp_path / "IMG_2.jpg")
    leftovers._note_pending(work, [str(back), still_gone])
    rows = [{"filepath": str(back)}, {"filepath": still_gone}]
    with open(os.path.join(work, "analysis.json"), "w") as f:
        json.dump(rows, f)

    leftovers.drop_notes_for_present_files(work, rows)  # what a new analysis does

    assert leftovers.pending_forgets(work) == {still_gone}
    assert [d["filepath"] for d in load_analysis(work)] == [str(back)]


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
