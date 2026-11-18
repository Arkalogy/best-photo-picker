"""`bpp serve` says on one line why it won't open a library.

The desktop app reads that line (desktop/src-tauri/src/sidecar.rs), stops
restarting the server and shows the reason, instead of retrying five
times and showing "Server could not restart"."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

MARKER = "BPP_LIBRARY_REFUSED: "


def _serve(lib) -> int:
    from bpp.commands.serve import do_serve

    args = argparse.Namespace(
        library=str(lib), port=0, host=None, debug=False, config=None, no_browser=True
    )
    return do_serve(args)


def _refusals(err: str) -> list[str]:
    return [line[len(MARKER) :] for line in err.splitlines() if line.startswith(MARKER)]


def test_a_library_with_a_link_for_a_folder_is_refused_on_one_line(tmp_path, capsys):
    from bpp.db.library import ensure_library_dirs

    lib = tmp_path / "library"
    ensure_library_dirs(str(lib))
    os.rmdir(lib / "logs")
    os.symlink(tmp_path, lib / "logs")  # a library prepared elsewhere

    assert _serve(lib) == 1

    (reason,) = _refusals(capsys.readouterr().err)
    assert reason.startswith("This library can't be opened: 'logs' inside it is a link")


def test_a_library_another_process_holds_is_refused_with_who_holds_it(tmp_path, capsys):
    from bpp.db.library import ensure_library_dirs

    lib = tmp_path / "library"
    data = ensure_library_dirs(str(lib))["data"]
    script = (
        "import sys\n"
        "from bpp.utils.serving_lock import acquire_lock\n"
        "print(acquire_lock(sys.argv[1]), flush=True)\n"
        "sys.stdin.readline()\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", script, data],  # e.g. `bpp analyze` writing into it
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    try:
        assert holder.stdout.readline().strip() == "None"
        assert _serve(lib) == 1
    finally:
        holder.communicate("\n", timeout=30)

    (reason,) = _refusals(capsys.readouterr().err)
    assert f"process {holder.pid}" in reason and "wait for it to finish" in reason


def test_a_damaged_database_with_no_backup_is_refused_on_one_line(tmp_path, capsys):
    from bpp.db.library import ensure_library_dirs

    lib = tmp_path / "library"
    data = ensure_library_dirs(str(lib))["data"]
    with open(os.path.join(data, "photopicker.db"), "wb") as f:
        f.write(b"SQLite format 3\x00" + os.urandom(8192))  # damaged past its header

    assert _serve(lib) == 1

    (reason,) = _refusals(capsys.readouterr().err)
    assert "corrupt" in reason.lower(), reason


def test_a_reason_over_several_lines_is_given_on_one(capsys):
    from bpp.commands.serve import _refuse

    assert _refuse("Database is corrupt.\nTo restore, run:\n  bpp db restore-backup") == 1

    assert _refusals(capsys.readouterr().err) == [
        "Database is corrupt. To restore, run: bpp db restore-backup"
    ]


def test_a_database_that_cant_be_read_is_never_replaced_by_its_backup(tmp_path, capsys):
    import sqlite3

    from bpp.db.library import ensure_library_dirs

    lib = tmp_path / "library"
    data = ensure_library_dirs(str(lib))["data"]
    db = os.path.join(data, "photopicker.db")
    sqlite3.connect(db).execute("CREATE TABLE t (x)").connection.close()
    sqlite3.connect(db + ".backup").execute("CREATE TABLE old (x)").connection.close()
    os.chmod(db, 0)  # e.g. copied from another account: it may be fine
    try:
        assert _serve(lib) == 1
    finally:
        os.chmod(db, 0o600)

    (reason,) = _refusals(capsys.readouterr().err)
    assert reason.startswith("The library's database can't be read"), reason
    assert sqlite3.connect(db).execute("SELECT name FROM sqlite_master").fetchall() == [("t",)]
    assert not [n for n in os.listdir(data) if ".corrupted-" in n], "nothing was set aside"


def test_a_library_held_elsewhere_is_not_checked_or_repaired_before_the_refusal(tmp_path, capsys):
    import sqlite3

    from bpp.db.library import ensure_library_dirs

    lib = tmp_path / "library"
    data = ensure_library_dirs(str(lib))["data"]
    db = os.path.join(data, "photopicker.db")
    with open(db, "wb") as f:
        f.write(b"SQLite format 3\x00" + os.urandom(8192))  # looks damaged to a checker
    sqlite3.connect(db + ".backup").execute("CREATE TABLE old (x)").connection.close()
    with open(db, "rb") as f:
        before = f.read()
    holder = _hold(data)
    try:
        assert _serve(lib) == 1
    finally:
        holder.communicate("\n", timeout=30)

    (reason,) = _refusals(capsys.readouterr().err)
    assert "is using it" in reason, reason
    with open(db, "rb") as f:
        assert f.read() == before, "the holder's database was not restored over"


def _hold(folder):
    script = (
        "import sys\n"
        "from bpp.utils.serving_lock import acquire_lock\n"
        "print(acquire_lock(sys.argv[1]), flush=True)\n"
        "sys.stdin.readline()\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", script, folder],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert holder.stdout.readline().strip() == "None"
    return holder
