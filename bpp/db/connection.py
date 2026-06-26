"""Thread-safe DB connection management.

Connection-level setup (WAL, foreign keys, busy timeout) and
checkpointing now go through `bpp.db.dialect.dialect` so a future
backend swap (e.g., Postgres) doesn't require touching this file.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import weakref

from bpp.constants import SQLITE_TIMEOUT_S
from bpp.db.dialect import dialect
from bpp.db.schema import create_tables
from bpp.utils.logging import get_logger

_local = threading.local()
_log = get_logger(__name__)

try:
    import resource  # POSIX only; absent on Windows
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]

# Live DB-connection accounting.
#
# The thread-local connection model is intentional: each thread that
# calls `get_db()` opens one SQLite connection that lives for the
# thread's lifetime. Werkzeug's dev server spawns a thread per
# request with no upper bound, so over a session many short-lived
# request threads each open a connection, then die. When a thread
# dies its connection is garbage-collected and its file descriptors
# are released.
#
# `_conn_count` tracks connections open RIGHT NOW, not the cumulative
# number ever created: `get_db()` increments it, and a
# `weakref.finalize` callback decrements it when the connection
# object is collected (thread death or explicit close). That is what
# makes the warning below honest — it fires on connections genuinely
# held open, not on benign request-thread churn. (An earlier version
# only ever incremented, so it crossed any fixed threshold given a
# long enough session and cried wolf on normal use.)
#
# The warning is a soft observability breadcrumb, not a hard pool: we
# never refuse to allocate (failing requests is worse than burning
# FDs on a single-user local app), but we log once if the live count
# climbs into a range that threatens the process's REAL
# file-descriptor limit, so a genuine leak surfaces as a clear log
# line instead of an opaque "too many open files" crash.

# Each WAL-mode SQLite connection holds ~3 FDs open: the main DB, the
# -wal, and the -shm. The budget is counted in connections but
# derived from the FD limit so it speaks the same currency as the
# resource it protects.
_FDS_PER_CONN = 3
# Warn once live connections would consume this fraction of the FD
# limit. Half leaves generous room for everything else the process
# holds open (sockets, model files, the HTTP server).
_FD_BUDGET_FRACTION = 0.5
# Floor so a pathologically small ulimit can't make the warning a
# hair trigger; a real runaway leak climbs well past this.
_MIN_CONN_BUDGET = 64


def _fd_soft_limit() -> int:
    """Return the process's soft open-file-descriptor limit.

    Reads the real ``RLIMIT_NOFILE`` rather than assuming a fixed
    value — the old code hardcoded 256, but modern macOS/Linux
    default to 1,048,576. Falls back conservatively where the
    platform won't say (Windows has no ``resource`` module)."""
    if resource is None:
        return 512  # Windows C-runtime default ballpark
    try:
        soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ValueError, OSError):
        return 512
    if soft <= 0:  # RLIM_INFINITY / unset
        return 1_000_000
    return soft


def _conn_budget() -> int:
    """Live-connection count at which we warn, derived from the real
    FD limit. A generous limit yields a large budget (the warning
    stays silent unless connections truly run away); a constrained
    limit tightens it automatically."""
    derived = int(_fd_soft_limit() * _FD_BUDGET_FRACTION / _FDS_PER_CONN)
    return max(derived, _MIN_CONN_BUDGET)


# Computed once at import — the FD limit is fixed for the process
# lifetime. Tests monkeypatch this to exercise the warning cheaply.
_CONN_WARN_BUDGET = _conn_budget()
_conn_count_lock = threading.Lock()
_conn_count = 0
_conn_warned = False


class _Connection(sqlite3.Connection):
    """``sqlite3.Connection`` subclass that supports weak references.

    The base C type can't be the target of a ``weakref.finalize``;
    a Python subclass with an explicit ``__weakref__`` slot can,
    while behaving identically as a connection. Used as the
    ``factory`` in ``get_db`` so the live-count finalizer can attach."""

    __slots__ = ("__weakref__",)


def _on_conn_finalized() -> None:
    """Decrement the live connection count when a connection object is
    garbage-collected or explicitly closed. Registered via
    ``weakref.finalize`` in ``get_db`` so the counter reflects
    connections open right now, not the cumulative number ever
    created."""
    global _conn_count
    with _conn_count_lock:
        _conn_count = max(0, _conn_count - 1)


def _connection_count() -> int:
    """Test-only accessor for the live connection counter."""
    with _conn_count_lock:
        return _conn_count


def _reset_connection_count() -> None:
    """Test-only reset to keep the warning latch unsticky between tests."""
    global _conn_count, _conn_warned
    with _conn_count_lock:
        _conn_count = 0
        _conn_warned = False


def _restrict_db_perms(db_path: str) -> None:
    """Set 0600 permissions on the DB file + WAL/SHM siblings.

    The ``settings`` table holds the ``lan_share_token`` in plaintext —
    a 256-bit credential that re-authenticates a paired phone or LAN
    device. With the default umask (typically 022), the file lands
    mode 0644 — readable by every other local account, indexed by
    Spotlight, copied verbatim into Time Machine snapshots, mirrored
    by iCloud/Dropbox/OneDrive sync agents, and captured by support
    bundles. Locking down to 0600 (owner read/write only) closes the
    "any other process on this Mac can see my LAN share token" path
    without changing functional behaviour for the bpp owner.

    Idempotent: chmod is safe to re-run, and we ignore failures
    (Windows / non-POSIX filesystems) so the chmod doesn't become a
    new operability footgun on platforms where it's not applicable.
    The WAL and SHM siblings inherit the same SHA-protected token
    state via SQLite's WAL-mode write path; same chmod applies.
    """
    for path in (db_path, db_path + "-wal", db_path + "-shm"):
        if os.path.isfile(path):
            with contextlib.suppress(OSError, NotImplementedError):
                os.chmod(path, 0o600)


def get_db(db_path: str) -> sqlite3.Connection:
    """Get a thread-local DB connection with dialect-specific setup."""
    global _conn_count, _conn_warned

    key = f"conn_{db_path}"
    conn = getattr(_local, key, None)
    if conn is None:
        conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            timeout=SQLITE_TIMEOUT_S,
            factory=_Connection,
        )
        conn.row_factory = sqlite3.Row
        dialect.setup_connection(conn)
        # Tighten permissions on the DB + WAL/SHM siblings the FIRST
        # time this thread sees the connection. SQLite creates the
        # files with the current umask (typically 0644), which leaks
        # the lan_share_token to every other local account / Time
        # Machine / iCloud sync. Idempotent on re-open.
        _restrict_db_perms(db_path)
        setattr(_local, key, conn)
        # Decrement the live count when this connection is collected
        # (its owning thread dies, or close_all_connections closes
        # it). Stash the finalizer in thread-local so an explicit
        # close can run it deterministically without waiting for GC.
        # atexit=False: at interpreter shutdown we don't care about
        # the live count, and running the callback against torn-down
        # globals would only add noise.
        fin = weakref.finalize(conn, _on_conn_finalized)
        fin.atexit = False
        setattr(_local, f"fin_{db_path}", fin)
        with _conn_count_lock:
            _conn_count += 1
            count_now = _conn_count
            budget = _CONN_WARN_BUDGET
            should_warn = count_now > budget and not _conn_warned
            if should_warn:
                _conn_warned = True
            elif count_now <= budget // 2:
                # Re-arm once we fall back well below the line, so a
                # later genuine spike warns again while oscillation
                # near the threshold doesn't spam the log.
                _conn_warned = False
        if should_warn:
            _log.warning(
                "Live DB connection count crossed %d (now %d) against a "
                "file-descriptor limit of %d. Each in-flight request thread "
                "holds one connection; a count that stays this high means "
                "connections aren't being released — investigate runaway "
                "client polling.",
                budget,
                count_now,
                _fd_soft_limit(),
            )
    return conn


# Backup / integrity / restore moved to bpp.db.backup and
# bpp.db.integrity (LOC gate split, 2026-06-12). Re-exported so the
# historical bpp.db.connection import path keeps working.
from bpp.db.backup import (  # noqa: E402, F401
    backup_db,
    is_post_restore_skip_backup,
    read_backup_meta,
    restore_from_backup_if_corrupt,
    set_post_restore_skip_backup,
)
from bpp.db.integrity import (  # noqa: E402, F401
    check_integrity,
    full_integrity_check,
    prune_corrupt_face_embeddings,
)


def checkpoint_wal(conn: sqlite3.Connection) -> None:
    """Dialect-driven checkpoint. SQLite truncates the WAL to keep
    .db-wal and .db-shm empty (important for iCloud/Dropbox safety
    and clean shutdown). No-op for backends without a WAL concept.

    a failed checkpoint (disk full, permission, corrupt
    WAL) used to be swallowed silently by an outer
    `contextlib.suppress(Exception)`, leaving operators with no
    breadcrumb. Now we log at WARNING with exc_info so the failure
    shows up in server.log, but still let shutdown proceed — a
    crash here would leak open SQLite handles, which is strictly
    worse than a stale WAL."""
    try:
        dialect.checkpoint(conn)
    except Exception:
        _log.warning("WAL checkpoint failed; continuing shutdown", exc_info=True)


def close_all_connections() -> None:
    """Checkpoint WAL and close all thread-local SQLite connections."""
    keys = [k for k in _local.__dict__ if k.startswith("conn_")]
    for key in keys:
        db_path = key[len("conn_") :]
        conn = _local.__dict__.pop(key, None)
        fin = _local.__dict__.pop(f"fin_{db_path}", None)
        if conn is not None:
            # checkpoint_wal handles its own logging now; .close() is
            # the cleanup-of-last-resort, so a failure there is
            # logged + suppressed too.
            checkpoint_wal(conn)
            try:
                conn.close()
            except Exception:
                _log.warning("conn.close() failed during shutdown", exc_info=True)
            # Run the finalizer now so the live count drops
            # deterministically even if the caller still holds a
            # reference to `conn`. weakref.finalize is idempotent — the
            # GC path becomes a no-op once it has fired here.
            if fin is not None:
                fin()


def init_db(db_path: str) -> sqlite3.Connection:
    """Create tables if needed and return a connection.

    Only runs ``create_tables`` the first time this db_path is seen on
    the current thread. Subsequent calls return the cached connection
    without re-running DDL — which would otherwise require a write lock
    for ``PRAGMA user_version`` on every Flask request thread, causing
    "database is locked" contention with background writers (phash,
    face extraction).
    """
    init_key = f"initialized_{db_path}"
    if not getattr(_local, init_key, False):
        conn = get_db(db_path)
        create_tables(conn)
        setattr(_local, init_key, True)
    return get_db(db_path)
