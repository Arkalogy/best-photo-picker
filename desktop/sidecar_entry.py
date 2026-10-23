"""PyInstaller entry point for the desktop app's bundled bpp server.

``multiprocessing.freeze_support()`` must run before anything else is
imported: in a frozen binary, multiprocessing's helper processes (the
resource tracker, spawned workers) re-execute this same executable, and
without it they fall through to bpp's CLI parser and crash.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()

    from bpp.cli import main

    sys.exit(main())
