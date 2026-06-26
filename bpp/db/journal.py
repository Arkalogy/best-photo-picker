"""Operation journal — durable breadcrumb for long-running mutations.

Some operations (permanent delete, face clustering, CLIP extraction)
span multiple steps with disk I/O between DB commits. A SIGKILL or
crash mid-flight leaves observable partial state:
- permanent_delete: DB rows gone, files orphaned on disk
- face clustering: cluster_ids assigned, but smart_person albums stale
- CLIP extraction: some embeddings written, some missing

This module gives them a journal: pre-write a record before mutating,
mark it complete after, and on next startup re-run a per-kind handler
for any incomplete entries.

The journal lives in `operation_journal` (schema v29). It uses a JSON
payload column so each operation kind picks its own shape; recovery
handlers downcast.

Pattern at call sites:

    journal_id = journal_start(conn, "permanent_delete", {"filepaths": [...]})
    try:
        # do the work
        ...
        journal_complete(conn, journal_id)
    except Exception:
        # leave the journal entry — startup recovery picks it up
        raise

DB-stored JSON is always parsed via `safe_json_loads` in the recovery
path — corrupt rows must not crash startup.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from typing import Any

from bpp.utils.json_utils import safe_json_loads
from bpp.utils.logging import get_logger

log = get_logger(__name__)


# Recovery handlers register themselves here, keyed by `kind` string.
# Each handler takes (conn, payload) and returns True if recovery
# succeeded (entry will be deleted) or False to leave the entry for
# manual investigation.
_RECOVERY_HANDLERS: dict[str, Callable[[sqlite3.Connection, dict[str, Any]], bool]] = {}


def register_recovery_handler(
    kind: str,
    handler: Callable[[sqlite3.Connection, dict[str, Any]], bool],
    *,
    replace: bool = False,
) -> None:
    """Register a recovery handler for a journal kind.

    Default is collision-safe: re-registering the same handler twice
    is fine, but a different handler for the same kind raises
    (catches accidental duplicate registration during development).

    Pass `replace=True` to swap in a new handler unconditionally —
    used by `WebAppState.startup()` after a library switch to rebind
    handlers whose closures captured the old ctx.
    """
    existing = _RECOVERY_HANDLERS.get(kind)
    if existing is not None and existing is not handler and not replace:
        raise ValueError(
            f"Recovery handler for kind {kind!r} already registered "
            f"with a different function (pass replace=True if intentional)"
        )
    _RECOVERY_HANDLERS[kind] = handler


def _reset_handlers_for_tests() -> None:
    """Clear the handler registry. Test-only hook."""
    _RECOVERY_HANDLERS.clear()


def library_bound_recovery(
    expected_library_path: str,
    handler: Callable[[sqlite3.Connection, dict[str, Any]], bool],
    *,
    library_path_getter: Callable[[], str | None],
) -> Callable[[sqlite3.Connection, dict[str, Any]], bool]:
    """Wrap a recovery handler so it refuses to fire after a library switch.

    Recovery handlers commonly close over the live ``WebAppState`` to reach
    workers / thumbs / dirs. ``WebAppState`` is mutated in place by
    ``switch_library`` — its ``paths.library_path`` changes after registration.
    Without this wrapper, a pending recovery written against library A could
    fire while ctx now points at library B, writing A's intent into B's data.

    The wrapper captures the expected library path at registration time
    (immutable string) and verifies it at fire time via ``library_path_getter``
    (which reads the current ctx state). On mismatch, it logs and returns
    False, leaving the journal entry in place. Next startup with the matching
    library will pick it up.

    Args:
        expected_library_path: Absolute, normalized library path at registration.
        handler: The underlying recovery handler (same signature as the
            inner callable in :data:`_RECOVERY_HANDLERS`).
        library_path_getter: Callable returning the current ctx's library
            path. Passed as a getter (not a captured value) so the wrapper
            sees post-switch state.

    Returns the wrapped handler. The wrapper's identity is what gets stored
    in :data:`_RECOVERY_HANDLERS`, so re-registration replaces cleanly.
    """

    def _guarded(conn: sqlite3.Connection, payload: dict[str, Any]) -> bool:
        current = library_path_getter()
        if current != expected_library_path:
            log.warning(
                "Refusing recovery — library changed since handler registered "
                "(registered for %r, ctx now on %r). Leaving journal entry in "
                "place; will recover on next startup against the matching library.",
                expected_library_path,
                current,
            )
            return False
        return handler(conn, payload)

    return _guarded


def journal_start(conn: sqlite3.Connection, kind: str, payload: dict[str, Any]) -> int:
    """Open a journal entry. Returns the row id.

    The entry is committed before the caller starts work — the whole
    point is that this row survives a crash. The caller must call
    `journal_complete()` (or let `recover_pending` clean up) when done.
    """
    cur = conn.execute(
        "INSERT INTO operation_journal (kind, payload_json, started_at) VALUES (?, ?, ?)",
        (kind, json.dumps(payload), int(time.time())),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def journal_complete(conn: sqlite3.Connection, journal_id: int) -> None:
    """Mark a journal entry complete. We delete rather than just
    timestamp — keeps the table small, and we don't currently expose
    completed history anywhere."""
    conn.execute("DELETE FROM operation_journal WHERE id = ?", (journal_id,))
    conn.commit()


def pending_journals(conn: sqlite3.Connection, kind: str | None = None) -> list[dict[str, Any]]:
    """Return uncompleted journal entries, optionally filtered by kind.

    Each entry is `{id, kind, payload, started_at}` with payload already
    parsed from JSON (returns {} on parse failure rather than crashing
    recovery — corrupted journal payloads are themselves a degenerate
    case worth surfacing as a logged warning, not a startup blocker).
    """
    if kind is not None:
        rows = conn.execute(
            "SELECT id, kind, payload_json, started_at FROM operation_journal"
            " WHERE completed_at IS NULL AND kind = ?"
            " ORDER BY started_at",
            (kind,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, kind, payload_json, started_at FROM operation_journal"
            " WHERE completed_at IS NULL"
            " ORDER BY started_at"
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        payload = safe_json_loads(r[2], {}, context=f"operation_journal id={r[0]} kind={r[1]}")
        if not isinstance(payload, dict):
            payload = {}
        out.append(
            {
                "id": int(r[0]),
                "kind": str(r[1]),
                "payload": payload,
                "started_at": int(r[3]),
            }
        )
    return out


def recover_pending(conn: sqlite3.Connection) -> dict[str, int]:
    """Run registered recovery handlers for any pending journal entries.

    Called once during startup, after schema migration. Returns a dict
    of {kind: count_recovered}. Entries without a registered handler
    are left in place (with a warning) — better to leave a breadcrumb
    than silently delete state we don't understand.
    """
    pending = pending_journals(conn)
    if not pending:
        return {}

    log.info("Found %d pending operation_journal entries on startup", len(pending))
    recovered: dict[str, int] = {}
    for entry in pending:
        kind = entry["kind"]
        handler = _RECOVERY_HANDLERS.get(kind)
        if handler is None:
            log.warning(
                "No recovery handler for journal kind %r (id=%d) — leaving entry in place",
                kind,
                entry["id"],
            )
            continue
        try:
            ok = handler(conn, entry["payload"])
        except Exception:
            log.warning(
                "Recovery handler for kind %r failed (id=%d); leaving entry",
                kind,
                entry["id"],
                exc_info=True,
            )
            continue
        if ok:
            journal_complete(conn, entry["id"])
            recovered[kind] = recovered.get(kind, 0) + 1
    if recovered:
        log.info("Recovered pending operations: %s", recovered)
    return recovered
