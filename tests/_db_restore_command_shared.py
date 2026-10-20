"""Shared fixtures and helpers of the test_db_restore_command.py suite (split for the
500-LOC cap)."""

from __future__ import annotations

import argparse
import os

from bpp.db.connection import (
    close_all_connections,
    init_db,
)
from bpp.db.photos import upsert_photo


def _make_library(tmp_path):
    """Build a minimal library tree with an initialized DB."""
    library = tmp_path / "library"
    data = library / "data"
    data.mkdir(parents=True)
    db_path = str(data / "photopicker.db")
    conn = init_db(db_path)
    upsert_photo(conn, {"filepath": "/fake/a.jpg", "sha256": "aaa"})
    conn.close()
    close_all_connections()
    return str(library), db_path


def _args(library, *, previous=False, yes=True, accept_stale=True, force=False):
    # Default accept_stale=True so existing tests don't trip the new
    # staleness guard. Tests that exercise the staleness guard pass
    # accept_stale=False explicitly.
    # Default force=False; tests of the running-server lock pass
    # force=True to bypass.
    return argparse.Namespace(
        command="db",
        db_command="restore-backup",
        library=library,
        previous=previous,
        yes=yes,
        accept_stale=accept_stale,
        force=force,
    )


# ─── Staleness guard ──────────────────────────────────────────────


def _age_backup(backup_path, days):
    """Backdate the .backup file's mtime AND the metadata sidecar's
    created_at_epoch so the restore command treats it as stale.

    The metadata sidecar is the primary staleness signal; mtime is
    the fallback for older backups without sidecars. Tests that
    simulate "this backup is N days old" need to age both."""
    import json
    import time

    delta = days * 86400
    target = time.time() - delta
    os.utime(backup_path, (target, target))

    meta_path = backup_path + ".meta.json"
    if os.path.isfile(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
        meta["created_at_epoch"] = target
        # created_at_utc is informational; keep it consistent
        import datetime

        meta["created_at_utc"] = datetime.datetime.fromtimestamp(
            target, tz=datetime.UTC
        ).isoformat()
        with open(meta_path, "w") as f:
            json.dump(meta, f)
