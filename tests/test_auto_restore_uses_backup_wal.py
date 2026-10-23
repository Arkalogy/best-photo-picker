"""The startup auto-restore brings the backup's WAL sidecar along, as the
CLI restore does: rows committed into the backup's WAL are part of the
backup that passed its integrity check."""

from __future__ import annotations

import sqlite3


def test_rows_in_the_backup_wal_survive_the_auto_restore(tmp_path):
    from bpp.db.backup import restore_from_backup_if_corrupt

    db_path = str(tmp_path / "lib.db")
    backup_path = db_path + ".backup"
    # the backup, with a committed row still in its WAL
    keep = sqlite3.connect(backup_path)
    keep.execute("PRAGMA journal_mode=WAL")
    keep.execute("CREATE TABLE t (v TEXT)")
    keep.execute("INSERT INTO t VALUES ('in the wal')")
    keep.commit()
    assert (tmp_path / "lib.db.backup-wal").stat().st_size > 0
    # a corrupt live DB
    with open(db_path, "wb") as f:
        f.write(b"SQLite format 3\x00" + b"\xff" * 4096)

    assert restore_from_backup_if_corrupt(db_path) is not None
    keep.close()
    with sqlite3.connect(db_path) as restored:
        assert restored.execute("SELECT v FROM t").fetchall() == [("in the wal",)]
