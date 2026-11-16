"""Replay of one journaled permanent delete after a crash.

The journal row lives in the library database, which travels with the
library folder. So the replay takes the records of *what* was being
deleted from the row, but never *where it may act*: the allowed folders
come from the library that is open now, and the claim token must be the
plain token this app generates. A row that fails either test has its
files left alone.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from bpp.utils.logging import get_logger
from bpp.utils.path_validation import build_library_allowlist
from bpp.web.permanent_delete import FileRecord, reconcile, retry_payload

log = get_logger(__name__)

#: ``secrets.token_hex(8)``: one path component, so a claim name is
#: always a sibling of the journaled path.
_TOKEN_RE = re.compile(r"[0-9a-f]{16}")


def replay_entry(ctx: object, conn: sqlite3.Connection, payload: dict[str, Any]) -> bool:
    """Finish (or undo) the delete this journal row describes. Returns
    True when the row is consumed."""
    from bpp.db.journal import journal_start

    raw_files = payload.get("files")
    files: dict[str, FileRecord] = dict(raw_files) if isinstance(raw_files, dict) else {}
    token = payload.get("token") or ""
    if not files or not token:
        # Nothing was claimed (a crash before the plan was journaled,
        # or an entry from a build without records): nothing on disk
        # is touched.
        if payload.get("filepaths"):
            log.warning(
                "Permanent-delete entry names %d files without records; leaving them",
                len(payload["filepaths"]),
            )
        return True
    attempts = payload.get("attempts") or 0
    well_formed = (
        isinstance(token, str)
        and _TOKEN_RE.fullmatch(token)
        and all(isinstance(fp, str) and isinstance(rec, dict) for fp, rec in files.items())
        and isinstance(attempts, int)
    )
    if not well_formed:
        log.warning(
            "Permanent-delete entry is malformed (token, records, attempts); leaving its %d files",
            len(files),
        )
        return True
    state = getattr(ctx, "state", None) or {}
    allowed = build_library_allowlist(state.get("library_path"), state.get("workdir"))
    log.info("Recovering interrupted permanent_delete for %d files", len(files))
    outcome = reconcile(ctx, conn, files, token, payload.get("sha256_map") or {}, allowed)
    leftover = outcome.stuck + outcome.failed
    if leftover:
        # A fresh entry naming only what is still stuck replaces this
        # one (returning True consumes it); both carry the records.
        retry = retry_payload(payload, leftover, attempts + 1)
        if retry is not None:
            journal_start(conn, "permanent_delete", retry)
    return True
