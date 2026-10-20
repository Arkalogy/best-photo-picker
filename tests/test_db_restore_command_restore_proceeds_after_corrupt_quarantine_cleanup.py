"""`bpp db restore-backup` — recovery from a failed migration — part 3 of 3:
test_restore_proceeds_after_corrupt_quarantine_cleanup,
test_restore_refuses_when_legacy_corrupt_backup_marker_exists,
test_restore_refuses_when_both_legacy_and_timestamped_corrupt_present."""

from __future__ import annotations

import os
import sqlite3

from bpp.commands import do_db_restore_backup
from bpp.db.connection import (
    backup_db,
)
from tests._db_restore_command_shared import _args, _make_library


def test_restore_proceeds_after_corrupt_quarantine_cleanup(tmp_path):
    """The reverse: operator deletes the .corrupt sibling, retry
    succeeds. Pins that the preflight isn't a permanent block."""
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)

    quarantine = db_path + ".backup.corrupt-20260101T000000Z"
    with open(quarantine, "wb") as f:
        f.write(b"corrupt")

    # First attempt refuses.
    assert do_db_restore_backup(_args(library)) == 6

    # Operator cleans up the quarantine; retry succeeds.
    os.remove(quarantine)
    assert do_db_restore_backup(_args(library)) == 0


def test_restore_refuses_when_legacy_corrupt_backup_marker_exists(tmp_path, capsys):
    """Older builds quarantined corrupt backups under a
    fixed name `.backup.corrupt` (no timestamp). A user upgrading
    across versions could have one left over. The preflight must
    cover the legacy shape too, otherwise an upgrade path silently
    bypasses the safety check."""
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)

    # Strand a LEGACY (no timestamp) quarantine file alongside.
    legacy = db_path + ".backup.corrupt"
    with open(legacy, "wb") as f:
        f.write(b"old corrupt evidence")

    rc = do_db_restore_backup(_args(library))
    assert rc == 6, f"Legacy .backup.corrupt should still trip the preflight; got rc={rc}"

    err = capsys.readouterr().err
    assert "corrupt-quarantine sibling(s)" in err
    assert legacy in err

    # Live DB untouched.
    c = sqlite3.connect(db_path)
    n = c.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    c.close()
    assert n == 1


def test_restore_refuses_when_both_legacy_and_timestamped_corrupt_present(tmp_path, capsys):
    """If both a legacy `.backup.corrupt` AND timestamped
    `.backup.corrupt-<ts>` exist, the preflight lists both."""
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)

    legacy = db_path + ".backup.corrupt"
    timestamped = db_path + ".backup.corrupt-20260101T000000Z"
    for p in (legacy, timestamped):
        with open(p, "wb") as f:
            f.write(b"corrupt")

    rc = do_db_restore_backup(_args(library))
    assert rc == 6
    err = capsys.readouterr().err
    assert legacy in err
    assert timestamped in err
