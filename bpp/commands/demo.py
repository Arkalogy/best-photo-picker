"""`bpp demo` — generate sample photos and launch the web UI.

Extracted from bpp.commands during the v0.1 cleanup. Re-exported
from `bpp.commands` for backwards compatibility with the CLI.
"""

from __future__ import annotations

import argparse
import os

from bpp.utils.logging import get_logger, setup_logging


def do_demo(args: argparse.Namespace) -> int:
    """Launch demo with generated sample photos."""
    setup_logging(debug=getattr(args, "debug", False))
    log = get_logger(__name__)

    try:
        from bpp.web.app import create_app
    except ImportError:
        log.error("Flask is not installed. Install with: pip install bppicker[web]")
        return 1

    import atexit
    import shutil
    import tempfile

    from bpp.db.connection import close_all_connections, get_db, init_db
    from bpp.db.library import import_folder
    from bpp.demo.generate import generate_sample_photos

    demo_lib = tempfile.mkdtemp(prefix="photopicker_demo_")
    log.info("Demo library: %s", demo_lib)

    if not getattr(args, "keep", False):

        def _cleanup() -> None:
            shutil.rmtree(demo_lib, ignore_errors=True)

        atexit.register(_cleanup)

    # Generate sample photos into a staging directory
    staging = os.path.join(demo_lib, "_staging")
    generate_sample_photos(staging, count=12)

    # Import into demo library via the standard pipeline
    db_path = os.path.join(demo_lib, "photopicker.db")
    init_db(db_path)
    conn = get_db(db_path)
    import_folder(conn, staging, demo_lib, batch_name="demo_samples")
    close_all_connections()

    # Clean up staging
    shutil.rmtree(staging, ignore_errors=True)

    port = getattr(args, "port", 5001)
    app = create_app(workdir=demo_lib, library_path=demo_lib)

    if not getattr(args, "no_browser", False):
        import threading
        import webbrowser

        threading.Timer(1.0, webbrowser.open, args=[f"http://127.0.0.1:{port}"]).start()

    log.info("Starting demo at http://127.0.0.1:%d", port)
    app.run(host="127.0.0.1", port=port, debug=getattr(args, "debug", False), threaded=True)
    return 0
