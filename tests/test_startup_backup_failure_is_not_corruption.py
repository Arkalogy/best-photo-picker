"""A backup that cannot be written (disk full, permissions) is not a
corrupt database: start-up continues with a warning instead of telling
the owner to overwrite a healthy database with an older backup."""

from __future__ import annotations

import errno
import os


def test_a_failed_backup_copy_does_not_refuse_a_healthy_database(tmp_path, monkeypatch):
    import shutil

    from bpp.db import backup
    from bpp.web.app import create_app

    lib = tmp_path / "library"
    (lib / "photos").mkdir(parents=True)
    create_app(workdir=str(lib / "data"), library_path=str(lib))
    db = os.path.join(str(lib / "data"), "photopicker.db")
    assert os.path.isfile(db)
    # An older backup exists (the branch that used to raise).
    shutil.copy2(db, db + ".backup")
    real_copy = shutil.copy2

    def disk_full(src, dst, *a, **k):
        if str(dst).endswith(".backup") or str(dst).endswith(".backup.prev"):
            raise OSError(errno.ENOSPC, "No space left on device", str(dst))
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(backup.shutil, "copy2", disk_full)
    # A second app on the same healthy library: must start, not refuse.
    app2 = create_app(workdir=str(lib / "data"), library_path=str(lib))
    assert app2 is not None and os.path.isfile(db)


def test_a_corrupt_database_is_still_refused_with_the_recovery_hint(tmp_path):
    import pytest

    from bpp.web.app import create_app

    lib = tmp_path / "library"
    (lib / "photos").mkdir(parents=True)
    create_app(workdir=str(lib / "data"), library_path=str(lib))
    db = os.path.join(str(lib / "data"), "photopicker.db")
    import shutil

    shutil.copy2(db, db + ".backup")
    from bpp.db.connection import close_all_connections

    close_all_connections()
    for suffix in ("-wal", "-shm"):
        if os.path.exists(db + suffix):
            os.remove(db + suffix)
    size = os.path.getsize(db)
    with open(db, "wb") as f:
        f.write(os.urandom(size))  # the file is no longer a database
    with pytest.raises(RuntimeError, match="restore-backup"):
        create_app(workdir=str(lib / "data"), library_path=str(lib))
