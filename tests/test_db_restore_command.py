"""`bpp db restore-backup` — recovery from a failed migration — part 1 of 3."""

from __future__ import annotations

import os
import shutil
import sqlite3

from bpp.commands import do_db_restore_backup
from bpp.db.connection import (
    backup_db,
    close_all_connections,
    init_db,
)
from bpp.db.photos import upsert_photo
from tests._db_restore_command_shared import _age_backup, _args, _make_library

# ─── Happy path ───────────────────────────────────────────────────


def test_restore_replaces_current_db_with_backup(tmp_path):
    library, db_path = _make_library(tmp_path)

    # Make a verified .backup snapshot.
    assert backup_db(db_path) is not None

    # Mutate the live DB so before/after differ.
    conn = init_db(db_path)
    upsert_photo(conn, {"filepath": "/fake/added-after-backup.jpg", "sha256": "bbb"})
    conn.close()
    close_all_connections()

    # Sanity check the live DB has the new row.
    c = sqlite3.connect(db_path)
    rows_before = c.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    c.close()
    assert rows_before == 2

    rc = do_db_restore_backup(_args(library))
    assert rc == 0

    # Live DB now matches the backup (1 row).
    c = sqlite3.connect(db_path)
    rows_after = c.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    c.close()
    assert rows_after == 1, "restored DB should match the .backup snapshot"


def test_restore_moves_current_db_aside_with_timestamp(tmp_path):
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)

    rc = do_db_restore_backup(_args(library))
    assert rc == 0

    # Look for the timestamped aside copy in the data dir.
    data_dir = os.path.dirname(db_path)
    aside = [f for f in os.listdir(data_dir) if f.startswith("photopicker.db.before-restore-")]
    assert len(aside) == 1, f"expected one aside file, got {aside}"


def test_restore_previous_uses_backup_prev(tmp_path):
    library, db_path = _make_library(tmp_path)

    # First backup
    backup_db(db_path)

    # Mutate, second backup → rotates .backup → .backup.prev
    conn = init_db(db_path)
    upsert_photo(conn, {"filepath": "/fake/v2.jpg", "sha256": "v2v"})
    conn.close()
    close_all_connections()
    backup_db(db_path)

    # The .backup.prev should reflect the older state (1 row), .backup
    # the newer (2 rows). After --previous restore, live DB has 1 row.
    rc = do_db_restore_backup(_args(library, previous=True))
    assert rc == 0

    c = sqlite3.connect(db_path)
    rows = c.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    c.close()
    assert rows == 1


# ─── Refusals ─────────────────────────────────────────────────────


def test_refuses_when_backup_missing(tmp_path):
    library, _db_path = _make_library(tmp_path)
    # No .backup created.
    rc = do_db_restore_backup(_args(library))
    assert rc == 2


def test_refuses_when_db_missing(tmp_path):
    library = str(tmp_path / "library")
    os.makedirs(os.path.join(library, "data"))
    rc = do_db_restore_backup(_args(library))
    assert rc == 2


def test_refuses_when_previous_missing(tmp_path):
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)  # only .backup, no .prev
    rc = do_db_restore_backup(_args(library, previous=True))
    assert rc == 2


def test_refuses_corrupt_backup_leaves_db_alone(tmp_path):
    """If the .backup file fails integrity check, do NOT swap it
    in — the user is better off with the current (possibly migrated)
    DB than with a corrupt restore."""
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)
    backup_path = db_path + ".backup"

    # Live DB has unique row that should still be there after refusal.
    conn = init_db(db_path)
    upsert_photo(conn, {"filepath": "/fake/sentinel.jpg", "sha256": "sent"})
    conn.close()
    close_all_connections()

    # Corrupt the backup file so verify rejects it.
    with open(backup_path, "wb") as f:
        f.write(b"not-a-sqlite-db" * 200)

    rc = do_db_restore_backup(_args(library))
    assert rc == 3

    # Live DB still has the sentinel row (untouched by the refusal).
    c = sqlite3.connect(db_path)
    rows = [r[0] for r in c.execute("SELECT filepath FROM photos")]
    c.close()
    assert "/fake/sentinel.jpg" in rows


# ─── Restored-DB verify-after-copy guard ──────────────────────────


def test_verify_fails_after_copy_returns_error(tmp_path, monkeypatch):
    """If something corrupts the file between source-verify and live
    DB, the post-copy integrity check must return error code 4 so
    the user knows the restore is half-done."""
    library, db_path = _make_library(tmp_path)
    backup_db(db_path)

    real_copy2 = shutil.copy2

    def break_target_copy(src, dst, *args, **kwargs):
        result = real_copy2(src, dst, *args, **kwargs)
        # After the legitimate copy, scribble over the destination
        # to simulate a torn write between verify and copy.
        if dst == db_path:
            with open(dst, "wb") as f:
                f.write(b"corrupt" * 100)
        return result

    # do_db_restore_backup imports shutil locally — patch the module
    # itself rather than a re-export.
    monkeypatch.setattr(shutil, "copy2", break_target_copy)
    rc = do_db_restore_backup(_args(library))
    assert rc == 4


class TestStalenessGuard:
    """`bpp db restore-backup` must warn (or refuse) when the
    backup is materially older than the live DB. Otherwise a user
    running the recovery command silently loses weeks/months of
    work."""

    def test_fresh_backup_no_warning(self, tmp_path, capsys):
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        rc = do_db_restore_backup(_args(library))
        assert rc == 0
        captured = capsys.readouterr()
        assert "stale" not in captured.err.lower()
        assert "WARNING: backup is" not in captured.err

    def test_stale_backup_yes_without_accept_stale_refuses(self, tmp_path, capsys):
        """The most-likely automation footgun: scripted recovery with
        --yes against a stale backup must refuse, not silently
        destroy."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        _age_backup(db_path + ".backup", days=14)

        rc = do_db_restore_backup(_args(library, yes=True, accept_stale=False))
        assert rc == 5
        err = capsys.readouterr().err
        assert "stale" in err.lower()
        assert "--accept-stale" in err

    def test_stale_backup_yes_with_accept_stale_proceeds(self, tmp_path, capsys):
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        _age_backup(db_path + ".backup", days=14)

        rc = do_db_restore_backup(_args(library, yes=True, accept_stale=True))
        assert rc == 0
        # Warning should still fire even when accepted, so the
        # operator sees the breadcrumb in CI logs.
        err = capsys.readouterr().err
        assert "WARNING: backup is" in err
        assert "14 days" in err

    def test_stale_backup_interactive_default_y_blocks_until_typed(
        self, tmp_path, monkeypatch, capsys
    ):
        """Stale-but-not-very-stale (8-30 days) uses the standard
        Y/N prompt. Confirm 'y' proceeds."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        _age_backup(db_path + ".backup", days=14)

        monkeypatch.setattr("builtins.input", lambda _prompt: "y")
        rc = do_db_restore_backup(_args(library, yes=False, accept_stale=False))
        assert rc == 0
        err = capsys.readouterr().err
        assert "14 days" in err

    def test_very_stale_backup_requires_typing_RESTORE(self, tmp_path, monkeypatch, capsys):
        """Backup older than 30 days raises the friction: typing
        'y' is no longer enough — operator must type RESTORE
        explicitly."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        _age_backup(db_path + ".backup", days=120)

        # First attempt: 'y' is rejected
        monkeypatch.setattr("builtins.input", lambda _prompt: "y")
        rc = do_db_restore_backup(_args(library, yes=False, accept_stale=False))
        assert rc == 1  # Aborted
        err = capsys.readouterr().err
        assert "120 days" in err

        # Second attempt: RESTORE proceeds
        monkeypatch.setattr("builtins.input", lambda _prompt: "RESTORE")
        rc = do_db_restore_backup(_args(library, yes=False, accept_stale=False))
        assert rc == 0

    def test_very_stale_backup_requires_exact_case(self, tmp_path, monkeypatch, capsys):
        """Lowercase 'restore' is NOT good enough — capital RESTORE
        is intentional friction."""
        library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        _age_backup(db_path + ".backup", days=60)

        monkeypatch.setattr("builtins.input", lambda _prompt: "restore")
        rc = do_db_restore_backup(_args(library, yes=False, accept_stale=False))
        assert rc == 1  # Aborted


# ─── Running-server lock guard ────────────────────────────────────


class TestStalenessMetadata:
    """Staleness must use the metadata sidecar's `created_at_epoch`
    as the primary signal, NOT filesystem mtime. mtime can be touched
    or copied without preserving the original creation time, so a
    stale backup with a fresh mtime would otherwise sail through.
    """

    def test_metadata_sidecar_is_written_on_backup(self, tmp_path):
        """backup_db must write a .meta.json next to the .backup."""
        import json

        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        meta_path = db_path + ".backup.meta.json"
        assert os.path.isfile(meta_path), (
            "backup_db must write a metadata sidecar for staleness tracking"
        )
        with open(meta_path) as f:
            meta = json.load(f)
        assert meta["version"] == 1
        assert "created_at_epoch" in meta
        assert "created_at_utc" in meta
        assert "user_version" in meta

    def test_stale_metadata_with_fresh_mtime_still_warns(self, tmp_path, capsys):
        """A backup whose metadata says 14 days old but whose mtime
        is fresh (because someone touched/copied it) MUST still
        trigger the staleness guard. Without the sidecar this would
        be bypassable."""
        import json

        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        # Backdate ONLY the metadata, leave mtime fresh
        meta_path = db_path + ".backup.meta.json"
        with open(meta_path) as f:
            meta = json.load(f)
        meta["created_at_epoch"] -= 14 * 86400
        with open(meta_path, "w") as f:
            json.dump(meta, f)
        # mtime stays current
        import time

        os.utime(db_path + ".backup", (time.time(), time.time()))

        rc = do_db_restore_backup(_args(str(tmp_path / "library"), yes=True, accept_stale=False))
        assert rc == 5, "Stale metadata must trigger staleness guard"
        err = capsys.readouterr().err
        assert "stale" in err.lower()

    def test_fresh_metadata_with_stale_mtime_uses_older(self, tmp_path, capsys):
        """When metadata says fresh but mtime is
        old, the conservative read is to use the OLDER timestamp.
        Previously this test asserted 'fresh metadata wins'; it was
        flipped to 'older value wins' so a forged-fresh sidecar
        on top of an actually-old file can't bypass the staleness
        guard. accept_stale=True is implicit in _args, so rc=0; we
        verify the warning fires anyway."""
        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        # Backdate ONLY mtime; metadata stays fresh
        import time

        old = time.time() - (14 * 86400)
        os.utime(db_path + ".backup", (old, old))

        rc = do_db_restore_backup(_args(str(tmp_path / "library")))
        # accept_stale=True default → proceeds, but the staleness
        # warning still fires because we pick the older (mtime) value.
        assert rc == 0
        err = capsys.readouterr().err
        assert "WARNING: backup is" in err, (
            "Conservative-read contract: older timestamp wins. With mtime "
            "14 days old, the warning must fire even when metadata is fresh."
        )
        assert "stale" not in err.lower()

    def test_missing_sidecar_falls_back_to_mtime_with_warning(self, tmp_path, capsys):
        """Backups from older bpp versions don't have a sidecar.
        Restore must still work but warn that the estimate is weak."""
        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)
        # Delete the sidecar — pretend this was an old backup
        os.remove(db_path + ".backup.meta.json")

        rc = do_db_restore_backup(_args(str(tmp_path / "library")))
        assert rc == 0
        err = capsys.readouterr().err
        assert "metadata sidecar missing" in err

    def test_future_dated_sidecar_rejected_and_falls_back(self, tmp_path, capsys):
        """A forged/corrupt sidecar with `created_at_epoch` set
        far in the future would otherwise produce age=0 (clamped from
        negative) and silently bypass the staleness guard. Reject
        future-dated metadata and fall back to mtime."""
        import json
        import time

        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        # Forge sidecar to 1 year in the future
        meta_path = db_path + ".backup.meta.json"
        with open(meta_path) as f:
            meta = json.load(f)
        meta["created_at_epoch"] = time.time() + 365 * 86400
        with open(meta_path, "w") as f:
            json.dump(meta, f)

        # And backdate the actual mtime to 14 days ago — so mtime
        # fallback correctly detects staleness
        old_mtime = time.time() - 14 * 86400
        os.utime(db_path + ".backup", (old_mtime, old_mtime))

        rc = do_db_restore_backup(_args(str(tmp_path / "library"), yes=True, accept_stale=False))
        assert rc == 5, (
            "Future-dated sidecar must be rejected and mtime fallback must "
            f"still detect the 14-day staleness — got rc={rc!r}"
        )
        err = capsys.readouterr().err
        assert "future" in err.lower()
        assert "stale" in err.lower()

    def test_sidecar_older_than_mtime_uses_sidecar(self, tmp_path, capsys):
        """The realistic case: sidecar (real creation time) older than
        mtime (touched / copy-with-new-mtime). Use the sidecar; don't
        let a fresher mtime mask staleness."""
        import json
        import time

        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        # Sidecar says 14 days, mtime fresh
        meta_path = db_path + ".backup.meta.json"
        with open(meta_path) as f:
            meta = json.load(f)
        meta["created_at_epoch"] = time.time() - 14 * 86400
        with open(meta_path, "w") as f:
            json.dump(meta, f)
        # mtime stays current

        rc = do_db_restore_backup(_args(str(tmp_path / "library"), yes=True, accept_stale=False))
        assert rc == 5, "Stale sidecar must trigger guard despite fresh mtime"

    def test_sidecar_newer_than_mtime_uses_older_value(self, tmp_path, capsys):
        """The other direction: sidecar fresh but mtime old (e.g.,
        someone restored the .backup file from an old archive without
        regenerating metadata). Use the OLDER value — mtime — so we
        don't trust a sidecar that may have been carried forward
        without the actual bytes being touched."""
        import json
        import time

        _library, db_path = _make_library(tmp_path)
        backup_db(db_path)

        # Sidecar fresh (just-now), mtime backdated 14 days
        meta_path = db_path + ".backup.meta.json"
        with open(meta_path) as f:
            meta = json.load(f)
        meta["created_at_epoch"] = time.time()
        with open(meta_path, "w") as f:
            json.dump(meta, f)
        old_mtime = time.time() - 14 * 86400
        os.utime(db_path + ".backup", (old_mtime, old_mtime))

        rc = do_db_restore_backup(_args(str(tmp_path / "library"), yes=True, accept_stale=False))
        assert rc == 5, (
            "Older mtime should win even when sidecar claims fresh — "
            "min(metadata, mtime) is the conservative read"
        )
