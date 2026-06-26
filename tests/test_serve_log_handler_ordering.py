"""Regression gate: in `do_serve` the file-log handler must be attached
BEFORE `create_app` runs.

Bug surfaced during UAT (2026-06-01). The Phase 5 daemon thread is
spawned inside `init_app_db()` which runs during `create_app()`. The
daemon immediately emits a `log.info("Phase 5 backfill starting ...")`
line. Before this fix, `add_file_handler()` was called after
`create_app()`, so the 'starting' line went to the stream handler
only and never reached `<library>/logs/server.log` — even though the
matching 'done' line ~1 s later DID land there (the handler was
attached by then).

A future refactor that moves these calls back into the broken order
silently reintroduces the gap. Static check on the source order is
cheap, deterministic, and catches the exact regression class.

(Same TDD pattern as the daemon's `db_path` capture test in
`test_init_app_db_helpers.py`. We deliberately avoid a live-server
runtime test because the boot is heavy and the threading involved
makes the runtime test non-deterministic.)
"""

from __future__ import annotations

import inspect

from bpp.commands import serve


def test_add_file_handler_runs_before_create_app_in_do_serve() -> None:
    """`add_file_handler(...)` must be called BEFORE `create_app(...)`
    in the body of `do_serve`, otherwise the Phase 5 daemon's first
    `log.info()` call (`'Phase 5 backfill starting'`) races the file
    handler being attached, lands on the stream handler only, and is
    lost to anyone reading `<library>/logs/server.log` later."""
    src = inspect.getsource(serve.do_serve)
    add_pos = src.find("add_file_handler(")
    create_pos = src.find("create_app(")

    assert add_pos != -1, (
        "add_file_handler(...) not found in do_serve — the file "
        "handler setup must remain in this function"
    )
    assert create_pos != -1, (
        "create_app(...) not found in do_serve — every other invariant "
        "in this file assumes the server is built here"
    )
    assert add_pos < create_pos, (
        "add_file_handler MUST run BEFORE create_app in do_serve. "
        "create_app constructs WebAppState which spawns the Phase 5 "
        "daemon thread; the daemon's first log.info() emits "
        "'Phase 5 backfill starting' immediately. Without the file "
        "handler attached first, that line never reaches "
        "<library>/logs/server.log (the matching 'done' line still "
        "does because it fires ~1s later, after the handler is "
        "attached — masking the bug). Move add_file_handler() to "
        "BEFORE the create_app() call."
    )
