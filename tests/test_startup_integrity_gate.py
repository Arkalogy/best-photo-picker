"""Tests for Protection C — startup integrity gate.

The Jun-2 demo lib incident: ``PRAGMA quick_check`` returned "ok" but
``PRAGMA integrity_check`` revealed dozens of "never used pages"
debris. The full check is the diagnostic; the quick check is just a
sanity probe.

Protection C adds three helpers in ``bpp/db/connection.py``:
  * ``full_integrity_check`` — runs the full PRAGMA, returns
    ``(ok, [errors])``.
  * ``prune_corrupt_face_embeddings`` — DELETEs rows whose embedding
    BLOB isn't the contract-stipulated 512 bytes.
  * ``restore_from_backup_if_corrupt`` — when the main DB fails the
    full check AND ``.backup`` passes, move the corrupt main aside
    and copy the backup over it. Refuses (raises RuntimeError) when
    no good backup is available — the caller surfaces the actionable
    message to the user.

These tests pin all three on fixture DBs that mirror the Jun-2 shapes.
"""

from __future__ import annotations

import os
import sqlite3
import struct
from pathlib import Path

import pytest

from bpp.db.connection import (
    full_integrity_check,
    prune_corrupt_face_embeddings,
    restore_from_backup_if_corrupt,
)


def _make_healthy_db(path: Path) -> None:
    """Create a small healthy DB with face_embeddings table."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE face_embeddings ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " photo_id INTEGER, face_index INTEGER,"
        " embedding BLOB"
        ")"
    )
    # One valid 128-d float32 row (512 bytes).
    valid_blob = struct.pack(f"{128}f", *([0.1] * 128))
    conn.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, embedding) VALUES (1, 0, ?)",
        (valid_blob,),
    )
    conn.commit()
    conn.close()


def _add_corrupt_face_rows(path: Path, n: int = 3) -> None:
    """Append rows with wrong-size embedding BLOBs."""
    conn = sqlite3.connect(path)
    bad_blob = b"\x00" * 1024  # 256-d garbage
    for i in range(n):
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, embedding) VALUES (?, 99, ?)",
            (100 + i, bad_blob),
        )
    conn.commit()
    conn.close()


class TestFullIntegrityCheck:
    def test_healthy_db_returns_ok(self, tmp_path: Path) -> None:
        db = tmp_path / "good.db"
        _make_healthy_db(db)
        ok, errors = full_integrity_check(str(db))
        assert ok
        assert errors == []

    def test_missing_db_returns_ok(self, tmp_path: Path) -> None:
        """No DB file is fine — pre-init, the call should not raise."""
        ok, errors = full_integrity_check(str(tmp_path / "absent.db"))
        assert ok
        assert errors == []

    def test_truncated_db_returns_not_ok(self, tmp_path: Path) -> None:
        """A truncated DB file should fail the integrity check.

        Quick_check might miss this (it's a sanity probe, not the
        full diagnostic). This pins that the full check catches it.
        """
        db = tmp_path / "bad.db"
        _make_healthy_db(db)
        # Truncate to half size — should produce integrity errors.
        size = db.stat().st_size
        with open(db, "r+b") as f:
            f.truncate(size // 2)
        ok, errors = full_integrity_check(str(db))
        # Either ok=False with errors, or the connect itself failed
        # (the helper traps and returns False with a "check raised"
        # entry — both shapes are valid "corrupt" outcomes).
        assert not ok
        assert errors  # non-empty


class TestPruneCorruptFaceEmbeddings:
    def test_prunes_wrong_size_rows(self, tmp_path: Path) -> None:
        db = tmp_path / "lib.db"
        _make_healthy_db(db)
        _add_corrupt_face_rows(db, n=5)
        # Verify they're really in there before we prune.
        with sqlite3.connect(db) as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM face_embeddings WHERE length(embedding) != 512"
            ).fetchone()[0]
        assert before == 5

        pruned = prune_corrupt_face_embeddings(str(db))
        assert pruned == 5

        with sqlite3.connect(db) as conn:
            after = conn.execute(
                "SELECT COUNT(*) FROM face_embeddings WHERE length(embedding) != 512"
            ).fetchone()[0]
            kept = conn.execute(
                "SELECT COUNT(*) FROM face_embeddings WHERE length(embedding) = 512"
            ).fetchone()[0]
        assert after == 0
        assert kept == 1  # the original healthy row survived

    def test_idempotent_on_clean_db(self, tmp_path: Path) -> None:
        db = tmp_path / "clean.db"
        _make_healthy_db(db)
        assert prune_corrupt_face_embeddings(str(db)) == 0
        assert prune_corrupt_face_embeddings(str(db)) == 0

    def test_skips_when_table_missing(self, tmp_path: Path) -> None:
        """Pre-init / migration-still-running DBs shouldn't crash."""
        db = tmp_path / "no_table.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE settings (k TEXT, v TEXT)")
        conn.commit()
        conn.close()
        assert prune_corrupt_face_embeddings(str(db)) == 0

    def test_skips_when_db_absent(self, tmp_path: Path) -> None:
        assert prune_corrupt_face_embeddings(str(tmp_path / "nope.db")) == 0

    def test_prunes_null_embeddings(self, tmp_path: Path) -> None:
        """P-05: NULL embeddings shouldn't exist per the schema, but
        a SIGKILL-mid-write can leave one. Sweep them in the same
        pass as wrong-size rows so face counts match what the app
        actually reads."""
        db = tmp_path / "lib.db"
        _make_healthy_db(db)
        conn = sqlite3.connect(db)
        # Mix of NULL and wrong-size rows on top of the one healthy row.
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, embedding)"
            " VALUES (50, 0, NULL), (51, 0, NULL)"
        )
        bad_blob = b"\x00" * 1024
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, embedding) VALUES (52, 0, ?)",
            (bad_blob,),
        )
        conn.commit()
        conn.close()

        pruned = prune_corrupt_face_embeddings(str(db))
        assert pruned == 3  # 2 NULL + 1 wrong-size

        with sqlite3.connect(db) as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0]
        assert remaining == 1  # only the original healthy row survives

    def test_log_names_affected_photos(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """P-04: the prune-summary log line should name the affected
        photos so a user noticing missing face data can grep the log
        and find the cause instead of asking support to dig in the DB."""
        db = tmp_path / "lib.db"
        _make_healthy_db(db)
        # Add a photos table + rows so the LEFT JOIN in the prune
        # helper resolves filepaths.
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY, filepath TEXT)")
        conn.execute(
            "INSERT INTO photos (id, filepath) VALUES (101, '/lib/IMG_0001.HEIC'),"
            " (102, '/lib/IMG_0002.HEIC'), (103, '/lib/IMG_0003.HEIC')"
        )
        bad_blob = b"\x00" * 1024
        for pid in (101, 102, 103):
            conn.execute(
                "INSERT INTO face_embeddings (photo_id, face_index, embedding) VALUES (?, 0, ?)",
                (pid, bad_blob),
            )
        conn.commit()
        conn.close()

        with caplog.at_level("WARNING", logger="bpp.db.connection"):
            pruned = prune_corrupt_face_embeddings(str(db))
        assert pruned == 3
        msgs = [r.getMessage() for r in caplog.records if "Pruned" in r.getMessage()]
        assert len(msgs) == 1
        line = msgs[0]
        # Names every filename and includes "id=filename" structure.
        assert "IMG_0001.HEIC" in line
        assert "IMG_0002.HEIC" in line
        assert "IMG_0003.HEIC" in line
        # No "+N more" tail when sample covers everything.
        assert "more)" not in line

    def test_log_truncates_with_more_suffix_on_huge_libs(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A 50-row prune should name ~20 rows and tail with '(+30 more)'
        so the log stays readable on a Jun-2-sized incident."""
        from bpp.db import integrity as _integrity_mod

        db = tmp_path / "huge.db"
        _make_healthy_db(db)
        sample = _integrity_mod._PRUNE_LOG_SAMPLE
        total = sample + 30
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY, filepath TEXT)")
        bad_blob = b"\x00" * 1024
        for i in range(total):
            pid = 200 + i
            conn.execute(
                "INSERT INTO photos (id, filepath) VALUES (?, ?)",
                (pid, f"/lib/x_{i:04d}.HEIC"),
            )
            conn.execute(
                "INSERT INTO face_embeddings (photo_id, face_index, embedding) VALUES (?, 0, ?)",
                (pid, bad_blob),
            )
        conn.commit()
        conn.close()

        with caplog.at_level("WARNING", logger="bpp.db.connection"):
            pruned = prune_corrupt_face_embeddings(str(db))
        assert pruned == total
        line = next(r.getMessage() for r in caplog.records if "Pruned" in r.getMessage())
        assert f"(+{total - sample} more)" in line, line


class TestRestoreFromBackupIfCorrupt:
    def test_healthy_db_no_restore(self, tmp_path: Path) -> None:
        db = tmp_path / "lib.db"
        _make_healthy_db(db)
        # A valid .backup is irrelevant when the main is healthy.
        backup = tmp_path / "lib.db.backup"
        _make_healthy_db(backup)
        assert restore_from_backup_if_corrupt(str(db)) is None

    def test_corrupt_db_restores_from_valid_backup(self, tmp_path: Path) -> None:
        db = tmp_path / "lib.db"
        backup = tmp_path / "lib.db.backup"
        _make_healthy_db(db)
        _make_healthy_db(backup)
        # Stash a marker row in the backup so we can verify the
        # restored DB really came from there.
        with sqlite3.connect(backup) as conn:
            conn.execute("CREATE TABLE backup_marker (v TEXT)")
            conn.execute("INSERT INTO backup_marker VALUES ('from-backup')")
            conn.commit()

        # Truncate the main DB to force corruption.
        size = db.stat().st_size
        with open(db, "r+b") as f:
            f.truncate(size // 2)

        corrupt_path = restore_from_backup_if_corrupt(str(db))
        assert corrupt_path is not None
        assert os.path.isfile(corrupt_path)
        # The main DB now matches the backup's content.
        with sqlite3.connect(db) as conn:
            marker = conn.execute("SELECT v FROM backup_marker").fetchone()
        assert marker == ("from-backup",)

    def test_corrupt_db_no_backup_raises(self, tmp_path: Path) -> None:
        db = tmp_path / "lib.db"
        _make_healthy_db(db)
        size = db.stat().st_size
        with open(db, "r+b") as f:
            f.truncate(size // 2)
        with pytest.raises(RuntimeError, match=r"no \.backup found"):
            restore_from_backup_if_corrupt(str(db))

    def test_corrupt_db_corrupt_backup_raises(self, tmp_path: Path) -> None:
        db = tmp_path / "lib.db"
        backup = tmp_path / "lib.db.backup"
        _make_healthy_db(db)
        _make_healthy_db(backup)
        # Truncate both — neither is usable.
        for f_path in (db, backup):
            size = f_path.stat().st_size
            with open(f_path, "r+b") as f:
                f.truncate(size // 2)
        with pytest.raises(RuntimeError, match=r"\.backup is also corrupt"):
            restore_from_backup_if_corrupt(str(db))

    def test_missing_db_no_op(self, tmp_path: Path) -> None:
        """Pre-first-run: DB doesn't exist yet."""
        assert restore_from_backup_if_corrupt(str(tmp_path / "absent.db")) is None

    def test_db_path_is_never_absent_during_swap(self, tmp_path: Path) -> None:
        """P-11: the old sequence ``move(db_path → corrupt)`` followed
        by ``copy(backup → db_path)`` left a ~ms window where ``db_path``
        didn't exist. A concurrent opener (a plugin acting on
        on_db_restore, a future admin UI calling this directly) would
        see "file not found" instead of either the corrupt or restored
        DB.

        The new sequence stages the restore via copy + atomic
        ``os.replace`` so ``db_path`` is always present. Pin that by
        patching ``os.replace`` to inspect the live path at the
        atomic moment."""
        from unittest.mock import patch

        db = tmp_path / "lib.db"
        backup = tmp_path / "lib.db.backup"
        _make_healthy_db(db)
        _make_healthy_db(backup)
        # Corrupt the main DB to force the restore branch.
        size = db.stat().st_size
        with open(db, "r+b") as f:
            f.truncate(size // 2)

        real_replace = os.replace
        observations: list[bool] = []

        def observing_replace(src, dst):
            # At the instant before the atomic swap, ``dst`` (db_path)
            # MUST already point at a valid file — either the corrupt
            # one (no hard-link fallback path) or the linked sidecar
            # (the fast path). Either way: present.
            observations.append(os.path.isfile(dst))
            return real_replace(src, dst)

        with patch("bpp.db.connection.os.replace", side_effect=observing_replace):
            corrupt_path = restore_from_backup_if_corrupt(str(db))
        assert corrupt_path is not None
        assert observations == [True], (
            f"db_path must already exist at the moment of the atomic swap; "
            f"observations: {observations}"
        )
        # Sanity: end state matches the existing happy-path test.
        assert os.path.isfile(db)
        assert os.path.isfile(corrupt_path)

    def test_staging_file_cleaned_up_on_failure(self, tmp_path: Path) -> None:
        """If the atomic swap fails partway, the .restoring sidecar
        must be removed so the next attempt isn't confused by a stale
        staging file."""
        from unittest.mock import patch

        db = tmp_path / "lib.db"
        backup = tmp_path / "lib.db.backup"
        _make_healthy_db(db)
        _make_healthy_db(backup)
        size = db.stat().st_size
        with open(db, "r+b") as f:
            f.truncate(size // 2)

        def boom_replace(src, dst):
            raise OSError("simulated swap failure")

        with (
            patch("bpp.db.connection.os.replace", side_effect=boom_replace),
            pytest.raises(OSError),
        ):
            restore_from_backup_if_corrupt(str(db))

        # No phantom .restoring file left behind.
        assert not (tmp_path / "lib.db.restoring").exists()
