"""High-level command implementations for the bpp CLI.

This was a single 1159-LOC module until the v0.1 cleanup; now it's
a package whose submodules each own one CLI command (or a tight
cluster of related ones):

* ``bpp.commands.analyze``  — ``do_analyze``, ``do_select``, ``do_run``
* ``bpp.commands.serve``    — ``do_web``, ``do_serve``
* ``bpp.commands.demo``     — ``do_demo``
* ``bpp.commands.pick``     — ``do_pick``
* ``bpp.commands.db_restore`` — ``do_db_restore_backup``, ``_do_restore_locked``

Everything is re-exported from this package so existing call sites
keep working unchanged:

    from bpp.commands import do_serve         # still works
    from bpp.commands import _do_restore_locked  # still works

The ``bpp.cli`` argparse plumbing lazy-imports each ``do_*`` only when
its subcommand fires, which means subcommand startup cost is bounded
by the cost of importing one submodule, not all five.
"""

from __future__ import annotations

from bpp.commands.analyze import do_analyze, do_run, do_select
from bpp.commands.db_restore import _do_restore_locked, do_db_restore_backup
from bpp.commands.demo import do_demo
from bpp.commands.pick import do_pick
from bpp.commands.serve import do_serve, do_web

__all__ = [
    "_do_restore_locked",
    "do_analyze",
    "do_db_restore_backup",
    "do_demo",
    "do_pick",
    "do_run",
    "do_select",
    "do_serve",
    "do_web",
]
