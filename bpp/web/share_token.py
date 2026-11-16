"""The LAN share link's token: created on first use, rotated by the owner.

Split out of bpp.web.share (500-LOC cap) and re-exported from there.
"""

from __future__ import annotations

import os
import secrets
import sqlite3

from bpp.utils.logging import get_logger
from bpp.web.share_seal import KEY_SHARE_TOKEN as _KEY_SHARE_TOKEN
from bpp.web.share_seal import set_sealed, sync_seal_to_backups

log = get_logger(__name__)


def get_share_token(conn: sqlite3.Connection) -> str:
    """Return the persisted LAN share token, creating one on first use.

    Distinct from `WebAppState.auth_token` (which rotates on every
    server boot) — the share token must survive restarts so that share
    URLs handed to family members keep working.
    """
    from bpp.db.settings import get_setting

    token = get_setting(conn, _KEY_SHARE_TOKEN)
    if token:
        return token
    token = secrets.token_hex(32)  # 256-bit
    set_sealed(conn, _KEY_SHARE_TOKEN, token)
    log.info(
        "LAN share token created on first use — old share URLs "
        "(none yet) will not work; new ones use this token."
    )
    return token


def _propagate_token_rotation_to_backups(db_path: str, new_token: str) -> None:
    """Rewrite the rotated share token into ``.backup`` and
    ``.backup.prev`` so a "user revoked because they suspect compromise"
    flow doesn't leave the old token sitting in a sibling file.

    The startup ``backup_db()`` snapshot is meant to be a recovery
    artifact for schema migration failures — but until this fix, it
    captured every value in ``settings`` at boot time, including the
    LAN share token. After a user clicks "Revoke link" expecting the
    old token to be gone, ``.backup`` (mode 0644 like the live DB
    until ``_restrict_db_perms`` chmods it) still held the
    compromised credential, readable by Time Machine, iCloud sync,
    Dropbox auto-backup, support bundles, etc.

    Best-effort: a failure here doesn't undo the rotation in the live
    DB. Worst case the user has to manually delete the backup files —
    which is the previous behaviour for the same scenario, so the
    failure mode is no worse than what we ship today. Logged at
    WARNING so an operator can surface the gap.
    """
    if not db_path:
        return
    from bpp.db.settings import set_setting

    for suffix in (".backup", ".backup.prev"):
        backup_path = db_path + suffix
        if not os.path.isfile(backup_path):
            continue
        try:
            from bpp.db.connection import get_db

            backup_conn = get_db(backup_path)
            set_setting(backup_conn, _KEY_SHARE_TOKEN, new_token)
            backup_conn.commit()
        except Exception:
            log.warning(
                "Failed to propagate rotated share token to %s — old token "
                "may still be readable in this backup file. Delete the "
                "backup manually if compromise is suspected.",
                backup_path,
                exc_info=True,
            )


def regenerate_share_token(conn: sqlite3.Connection) -> str:
    """Rotate the share token. Existing share URLs become invalid.

    User-driven: invoked from the "Revoke link" button in Settings →
    Share when a leaked URL needs to be killed.

    Also propagates the new token into ``.backup`` and ``.backup.prev``
    so the old (presumed-compromised) token doesn't outlive the
    rotation in the on-disk recovery snapshots — see
    ``_propagate_token_rotation_to_backups``.
    """
    from bpp.db.dialect import dialect

    token = secrets.token_hex(32)
    set_sealed(conn, _KEY_SHARE_TOKEN, token)
    # Audit trail: a support case "my share link stopped working"
    # leaves a clear log breadcrumb showing user-driven rotation.
    log.info("LAN share token rotated — all existing share URLs invalidated.")

    db_path = dialect.database_path(conn)
    if db_path:
        _propagate_token_rotation_to_backups(db_path, token)
        # A backup that now holds the live sharing state gets the live seal.
        sync_seal_to_backups(conn)
    return token
