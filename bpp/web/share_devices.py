"""Paired-device CRUD on the ``share_devices`` table.

Extracted from share.py — these are pure DB ops on the
``share_devices`` table with no dependency on the rest of share.py's
auth / proxy / QR machinery. Splitting them out shrinks share.py and
gives the LAN-pair flow a dedicated home for future device-lifecycle
work (TTL knobs, multi-device merge, etc.) to land in.

Backwards compat preserved via re-exports in share.py — every existing
``from bpp.web.share import approve_device`` etc. keeps working.

Trust-state cheat sheet (columns on share_devices):

  pending  →  trusted_at IS NULL  AND  revoked_at IS NULL
  trusted  →  trusted_at IS NOT NULL  AND  revoked_at IS NULL
  revoked  →  revoked_at IS NOT NULL  (regardless of trusted_at)

The ``prev_revoked`` flag is sticky — set the first time a device is
revoked and never cleared. ``request_access`` looks at it to surface
"this device was revoked before" cues in the owner UI.
"""

from __future__ import annotations

import sqlite3
import time

from bpp.utils.logging import get_logger

log = get_logger(__name__)


def _row_to_device(row: object) -> dict[str, object]:
    """Coerce a sqlite3.Row to a plain dict (cleaner JSON serialization)."""
    # sqlite3.Row supports `for k in row` returning column names; the
    # SIM118 suppression keeps the explicit .keys() call for readability
    # at this DB-row boundary.
    return {k: row[k] for k in row.keys()}  # type: ignore[attr-defined]  # noqa: SIM118


def find_or_create_pending_device(
    conn: sqlite3.Connection, fingerprint: str, name: str, ip: str
) -> dict[str, object]:
    """Return the device row for `fingerprint`, creating a pending row if absent.

    Behavior:
    - New fingerprint → insert as pending. Bare QR scan = implicit ask.
    - Existing row → bump `last_seen` only. Trust state (pending /
      trusted / revoked) is preserved as-is; revoked rows stay revoked.
      Re-requesting access after revoke is an explicit user action via
      `request_access()`, not a side-effect of page loads.
    """
    existing = conn.execute(
        "SELECT * FROM share_devices WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()
    now = int(time.time())
    if existing is None:
        cur = conn.execute(
            "INSERT INTO share_devices"
            " (fingerprint, name, ip_at_pair, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?)",
            (fingerprint, name, ip, now, now),
        )
        # Defensive: if the INSERT silently failed (disk full, schema
        # corruption, missing table), surface a loud RuntimeError
        # instead of letting the caller dereference None on the
        # follow-up SELECT and produce a confusing trace.
        if not cur.lastrowid or cur.lastrowid <= 0:
            raise RuntimeError(
                f"share_devices INSERT returned no row id (fingerprint={fingerprint!r})"
            )
        conn.commit()
        row = conn.execute("SELECT * FROM share_devices WHERE id = ?", (cur.lastrowid,)).fetchone()
        if row is None:
            raise RuntimeError(
                "share_devices INSERT committed but row id="
                f"{cur.lastrowid} not found on follow-up SELECT"
            )
        return _row_to_device(row)

    conn.execute(
        "UPDATE share_devices SET last_seen = ? WHERE id = ?",
        (now, existing["id"]),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM share_devices WHERE id = ?", (existing["id"],)).fetchone()
    return _row_to_device(row)


def request_access(conn: sqlite3.Connection, fingerprint: str) -> dict[str, object] | None:
    """Explicit re-request after a revoke (or no-op for non-revoked).

    Triggered when the phone user taps "Request access again" on the
    revoked pair page. Behavior by current state:
    - Unknown fingerprint → returns None (caller should 404).
    - Revoked → flips back to pending; `prev_revoked` stays sticky as
      a security cue for the owner UI.
    - Pending → idempotent no-op; just returns the row.
    - Trusted → idempotent no-op; never demotes a trusted device.
    """
    existing = conn.execute(
        "SELECT * FROM share_devices WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()
    if existing is None:
        return None

    now = int(time.time())
    if existing["revoked_at"] is not None:
        conn.execute(
            "UPDATE share_devices SET"
            " trusted_at = NULL, revoked_at = NULL,"
            " prev_revoked = 1, last_seen = ?"
            " WHERE id = ?",
            (now, existing["id"]),
        )
        conn.commit()

    row = conn.execute("SELECT * FROM share_devices WHERE id = ?", (existing["id"],)).fetchone()
    return _row_to_device(row)


def approve_device(conn: sqlite3.Connection, device_id: int) -> bool:
    """Mark a device as trusted (clears any revoked_at).

    Atomic: the existence check and the UPDATE share an IMMEDIATE
    transaction so a concurrent approve+revoke can't race past each
    other and leave the row in an inconsistent state. Returns True on
    success, False if the device doesn't exist.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT id FROM share_devices WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            conn.rollback()
            return False
        conn.execute(
            "UPDATE share_devices SET trusted_at = ?, revoked_at = NULL WHERE id = ?",
            (int(time.time()), device_id),
        )
        conn.commit()
        # success breadcrumb. Without this, a user who reports
        # "I approved a device but it still says pending" has no log
        # evidence the approve actually committed — the only existing
        # log line was the error path.
        log.info("Device %d approved (trusted_at set)", device_id)
        return True
    except Exception:
        # Log before re-raise so on-call sees the actual DB error
        # (lock contention / disk full / corruption) without having
        # to repro from scratch. RedactingFormatter scrubs any token
        # that surfaces in the traceback.
        log.error("approve_device DB error (device_id=%d)", device_id, exc_info=True)
        conn.rollback()
        raise


def revoke_device(conn: sqlite3.Connection, device_id: int) -> bool:
    """Revoke a device. Sets prev_revoked sticky flag for forensic UX.

    Clears `trusted_at` as part of the revoke so the row reflects a
    single coherent state — concurrent approve+revoke can never end
    with both timestamps set (which would confuse the Devices list,
    even if `is_device_trusted` already handles it correctly).

    Atomic: see approve_device for the transactional contract. Returns
    True on success, False if the device doesn't exist.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT id FROM share_devices WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            conn.rollback()
            return False
        conn.execute(
            "UPDATE share_devices SET"
            " trusted_at = NULL, revoked_at = ?, prev_revoked = 1"
            " WHERE id = ?",
            (int(time.time()), device_id),
        )
        conn.commit()
        # success breadcrumb. Mirrors approve_device — pairs
        # with the existing error-path log so revoke success/failure
        # are both visible in server.log without further code changes.
        log.info("Device %d revoked (revoked_at set, prev_revoked=1)", device_id)
        return True
    except Exception:
        # Log before re-raise — same rationale as approve_device.
        log.error("revoke_device DB error (device_id=%d)", device_id, exc_info=True)
        conn.rollback()
        raise


def is_device_trusted(conn: sqlite3.Connection, fingerprint: str) -> bool:
    """Whether the fingerprint maps to an approved, non-revoked device."""
    row = conn.execute(
        "SELECT trusted_at, revoked_at FROM share_devices WHERE fingerprint = ?",
        (fingerprint,),
    ).fetchone()
    if row is None:
        return False
    return row["trusted_at"] is not None and row["revoked_at"] is None


def get_device_by_fingerprint(
    conn: sqlite3.Connection, fingerprint: str
) -> dict[str, object] | None:
    """Return the share_devices row matching a fingerprint, or None.

    Returns the raw row regardless of trust state — caller checks
    `revoked_at` / `trusted_at` to decide what to do. Used by
    `authorize_request` to gate LAN clients.
    """
    row = conn.execute(
        "SELECT * FROM share_devices WHERE fingerprint = ?", (fingerprint,)
    ).fetchone()
    return _row_to_device(row) if row else None


def list_devices(conn: sqlite3.Connection) -> dict[str, list[dict[str, object]]]:
    """Return {pending: [...], trusted: [...]} for the Mac UI.

    Revoked devices are hidden — they only resurface if the phone
    reconnects, which moves them back into pending.
    """
    rows = conn.execute(
        "SELECT * FROM share_devices WHERE revoked_at IS NULL ORDER BY last_seen DESC"
    ).fetchall()
    pending: list[dict[str, object]] = []
    trusted: list[dict[str, object]] = []
    for r in rows:
        d = _row_to_device(r)
        if d["trusted_at"] is None:
            pending.append(d)
        else:
            trusted.append(d)
    return {"pending": pending, "trusted": trusted}


def prune_expired_pending(conn: sqlite3.Connection, *, ttl_seconds: int = 24 * 3600) -> int:
    """Delete pending devices whose last_seen is older than the TTL.

    Trusted devices are never pruned. Returns the row count removed.
    """
    cutoff = int(time.time()) - ttl_seconds
    cur = conn.execute(
        "DELETE FROM share_devices WHERE trusted_at IS NULL AND last_seen < ?",
        (cutoff,),
    )
    conn.commit()
    return cur.rowcount
