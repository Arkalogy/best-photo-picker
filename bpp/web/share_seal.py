"""Sharing state belongs to this installation, not to the library folder.

The LAN share link, the sharing switch and the list of approved devices
are stored in the library database, and a library folder can come from
someone else. Honouring what such a database says would give its author
access: their link, their "approved" device, sharing already on.

So every owner write to that state (create or rotate the link, flip the
switch, approve or revoke a device) is sealed with a key kept in this
user's config folder, outside every library (:mod:`share_seal_store`).
The seal covers:

* the state itself (link, switch, approved devices);
* which library it is (a random id) and a counter that goes up with each
  owner write. This installation records both, with the place the
  library was last opened, so a seal copied into another folder, or an
  older copy of the same database, does not pass.

When a library is opened its seal is checked; sharing state this
installation did not seal is removed (sharing off, link and devices
gone) and the owner is told. The same check runs before every owner
write, so state that hasn't been checked yet is never sealed as ours.

Databases this installation already used before the key existed are
adopted once, at their first open.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass

from bpp.utils.logging import get_logger
from bpp.web import share_seal_store as store

log = get_logger(__name__)

KEY_SHARE_TOKEN = "lan_share_token"
KEY_LAN_ENABLED = "lan_sharing_enabled"
KEY_SEAL = "lan_share_seal"
KEY_SEAL_ID = "lan_share_seal_id"
KEY_SEAL_N = "lan_share_seal_n"
SEAL_KEYS = (KEY_SEAL, KEY_SEAL_ID, KEY_SEAL_N)
_UPSERT = (
    "INSERT INTO settings (key, value) VALUES (?, ?) "
    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
)

#: What :func:`check_on_open` found.
SEALED = "sealed"  # sealed by this installation: left as it is
EMPTY = "empty"  # no sharing state to honour
UNCHECKED = "unchecked"  # this installation's key can't be read: left alone, sharing off
ADOPTED = "adopted"  # from before the key existed: sealed now
RESET = "reset"  # not ours: sharing off, link and devices removed

_BAD = object()  # a stored value that isn't the text this app writes


def ensure_installation() -> None:
    """Create this installation's key if it has none. Call it before a
    newly opened library is registered (see :func:`share_seal_store.key`)."""
    store.key()


# ── what is sealed ───────────────────────────────────────────────────────


def _setting(conn: sqlite3.Connection, key: str) -> object:
    """A stored setting: text, None, or ``_BAD`` when it is anything else
    (a blob, text that can't be decoded)."""
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            raise
        return _BAD
    value = row[0] if row else None
    return value if value is None or isinstance(value, str) else _BAD


def _state(conn: sqlite3.Connection) -> tuple[str, str, list[str]] | None:
    """The link, the switch and the approved devices; None when what is
    stored isn't text this app could have written."""
    token = _setting(conn, KEY_SHARE_TOKEN)
    enabled = _setting(conn, KEY_LAN_ENABLED)
    try:
        rows = conn.execute(
            "SELECT fingerprint FROM share_devices"
            " WHERE trusted_at IS NOT NULL AND revoked_at IS NULL ORDER BY fingerprint"
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            raise
        return None
    fingerprints = [r[0] for r in rows]
    if token is _BAD or not all(isinstance(fp, str) for fp in fingerprints):
        return None
    return str(token or ""), "1" if enabled == "1" else "0", fingerprints


def _mac(lib_id: str, n: int, state: tuple[str, str, list[str]]) -> str:
    message = json.dumps([lib_id, n, *state], separators=(",", ":")).encode()
    return hmac.new(bytes.fromhex(store.key()), message, hashlib.sha256).hexdigest()


def _db_file(conn: sqlite3.Connection) -> str:
    from bpp.db.dialect import dialect

    path = dialect.database_path(conn)
    return os.path.realpath(path) if path else ""


def _seal_id_at(db_file: str) -> object:
    """The seal id stored in another database file, or None."""
    from bpp.db.connection import get_db

    try:
        return _setting(get_db(db_file), KEY_SEAL_ID)
    except sqlite3.Error:
        return None


def _same_library(recorded: str, here: str, lib_id: str) -> bool:
    """Is the database at *here* the library last seen at *recorded*
    (the same file, or moved), rather than a copy of it? A copy is told
    by the original still being there with the same id."""
    if recorded == here or not os.path.isfile(recorded):
        return True
    with contextlib.suppress(OSError):
        if os.path.samefile(recorded, here):
            return True
    return _seal_id_at(recorded) != lib_id


def _stored_seal(conn: sqlite3.Connection) -> tuple[str, str, int] | None:
    seal, lib_id, n = (_setting(conn, key) for key in SEAL_KEYS)
    if not (isinstance(seal, str) and isinstance(lib_id, str) and isinstance(n, str)):
        return None
    if not (n.isascii() and n.isdigit() and int(n) >= 1):
        return None
    return seal, lib_id, int(n)


def _ours(conn: sqlite3.Connection, state: tuple[str, str, list[str]]) -> bool:
    """Is the stored seal this installation's, for this state, and is
    this the library (not a copy, not an older database) it was made in?"""
    stored = _stored_seal(conn)
    if stored is None:
        return False
    seal, lib_id, n = stored
    if not hmac.compare_digest(seal.encode(), _mac(lib_id, n, state).encode()):
        return False
    here = _db_file(conn)
    seen = store.record(lib_id)
    if seen is not None and (n < seen["n"] or not _same_library(seen["path"], here, lib_id)):
        return False
    # First sight (a seal committed just before a crash), a newer counter
    # or a new place: record it.
    _record(lib_id, n, here)
    return True


def _record(lib_id: str, n: int, db_file: str) -> None:
    def change(state: dict) -> None:
        seen = state["libs"].get(lib_id)
        state["libs"][lib_id] = {"n": max(n, seen["n"] if seen else 0), "path": db_file}

    store.update(change)


def _is_empty(conn: sqlite3.Connection, state: tuple[str, str, list[str]]) -> bool:
    """No link, sharing off, no device rows and no seal: nothing to honour."""
    return (
        state == ("", "0", [])
        and all(_setting(conn, key) is None for key in SEAL_KEYS)
        and conn.execute("SELECT 1 FROM share_devices LIMIT 1").fetchone() is None
    )


def _remove(conn: sqlite3.Connection) -> None:
    """Delete the sharing state, in the caller's transaction.

    What this installation recorded for the library (its counter) is
    kept: the caller may still roll back, and an older sealed state put
    back later must not pass as the first one seen."""
    conn.execute("DELETE FROM share_devices")
    conn.execute("DELETE FROM share_access_log")
    marks = ", ".join("?" for _ in range(2 + len(SEAL_KEYS)))
    conn.execute(
        f"DELETE FROM settings WHERE key IN ({marks})",
        (KEY_SHARE_TOKEN, KEY_LAN_ENABLED, *SEAL_KEYS),
    )
    log.warning(
        "Sharing was turned off for this library: its sharing settings were not made "
        "by this installation. The share link and approved devices it carried were "
        "removed. Turn sharing on again in Settings > Share if you want it."
    )


# ── sealing an owner write ───────────────────────────────────────────────


@dataclass(frozen=True)
class _Staged:
    lib_id: str
    n: int
    db_file: str


def ensure_ours(conn: sqlite3.Connection) -> None:
    """Before an owner write, inside its transaction: the sharing state
    must be this installation's, or empty. Anything else is removed
    first, so a write can never seal state that was not checked."""
    reason = store.unreadable()
    if reason:
        from bpp.errors import ConflictError

        raise ConflictError(
            "Sharing settings can't be changed",
            user_message="this app's own sharing key file can't be read "
            f"({reason}); fix its permissions and restart Best Photo Picker",
        )
    state = _state(conn)
    if state is None or not (_ours(conn, state) or _is_empty(conn, state)):
        _remove(conn)


def stage(conn: sqlite3.Connection) -> _Staged:
    """Write the seal of the state as it stands in the caller's open
    transaction (which began with :func:`ensure_ours`). After the commit,
    pass the result to :func:`confirm`."""
    state = _state(conn)
    if state is None:
        raise RuntimeError("the sharing state can't be sealed")
    stored = _stored_seal(conn)
    lib_id, n = (stored[1], stored[2]) if stored else (secrets.token_hex(16), 0)
    seen = store.record(lib_id)
    n = max(n, seen["n"] if seen else 0) + 1
    conn.execute(_UPSERT, (KEY_SEAL_ID, lib_id))
    conn.execute(_UPSERT, (KEY_SEAL_N, str(n)))
    conn.execute(_UPSERT, (KEY_SEAL, _mac(lib_id, n, state)))
    return _Staged(lib_id, n, _db_file(conn))


def confirm(staged: _Staged) -> None:
    """Record a committed seal with this installation."""
    _record(staged.lib_id, staged.n, staged.db_file)


@contextlib.contextmanager
def sealed(conn: sqlite3.Connection) -> Iterator[None]:
    """One transaction around an owner write to sharing state and its
    seal: both are committed together, or neither."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        ensure_ours(conn)
        yield
        staged = stage(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    confirm(staged)


def set_sealed(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Store one sharing setting and the seal in one transaction."""
    with sealed(conn):
        conn.execute(_UPSERT, (key, value))


# ── opening a library ────────────────────────────────────────────────────


def check_on_open(conn: sqlite3.Connection) -> str:
    """Decide whether an opened library's sharing state is this
    installation's, and remove it when it is not. Returns one of
    :data:`SEALED`, :data:`EMPTY`, :data:`ADOPTED`, :data:`RESET`, or
    :data:`UNCHECKED` when the key itself can't be read (nothing is
    removed then; the caller keeps devices out)."""
    if store.unreadable():
        return UNCHECKED
    ident = store.file_identity(_db_file(conn))
    try:
        state = _state(conn)
    except sqlite3.OperationalError:  # "no such table": no sharing state at all
        return EMPTY

    if state is not None and _ours(conn, state):
        outcome = SEALED
    elif state is not None and _is_empty(conn, state):
        outcome = EMPTY
    elif state is not None and _setting(conn, KEY_SEAL) is None and store.adoptable(ident):
        staged = stage(conn)
        conn.commit()
        confirm(staged)
        sync_seal_to_backups(conn)
        log.info("Sharing settings of this library are now tied to this installation")
        outcome = ADOPTED
    else:
        try:
            _remove(conn)
            conn.commit()
        except sqlite3.Error as exc:
            conn.rollback()
            from bpp.errors import ValidationError

            raise ValidationError(
                "This library can't be opened: the sharing settings stored in it can't be cleared."
            ) from exc
        outcome = RESET
    store.forget_adoption(ident)
    return outcome


def sync_seal_to_backups(conn: sqlite3.Connection) -> None:
    """Give the startup backups the live seal when their sharing state is
    the live one, so restoring such a backup keeps the owner's sharing.
    A backup whose sharing state differs (an older one) is left alone: a
    restore of it comes up with sharing off. Best-effort."""
    from bpp.db.connection import get_db

    db_file = _db_file(conn)
    try:
        live = _state(conn)
        rows = [(key, _setting(conn, key)) for key in SEAL_KEYS]
    except sqlite3.Error:
        return
    if not db_file or live is None or not all(isinstance(value, str) for _, value in rows):
        return
    for suffix in (".backup", ".backup.prev"):
        backup = db_file + suffix
        if not os.path.isfile(backup):
            continue
        try:
            backup_conn = get_db(backup)
            if _state(backup_conn) != live:
                continue
            backup_conn.executemany(_UPSERT, rows)
            backup_conn.commit()
        except sqlite3.Error:
            log.warning("Could not update the sharing seal in %s", backup, exc_info=True)
