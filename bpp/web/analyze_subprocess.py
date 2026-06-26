"""Shared infrastructure for the Phase-1 / Phase-2 subprocess runners.

Owns the sentinel value, the config-flattening helper that side-steps
the mappingproxy pickle bug, and the atomic JSON writer the analyze
worker uses to persist intermediate results.

The actual subprocess machinery lives in two sibling modules:

* :mod:`bpp.web.analyze_scoring` — Phase-1 scoring runner.
* :mod:`bpp.web.analyze_face_extract` — Phase-2 face-extraction runner
  plus the chunking layer.

The env-var pinning (``OMP_NUM_THREADS`` / ``OPENBLAS_NUM_THREADS`` /
``MKL_NUM_THREADS`` = 1) lives in :mod:`bpp.web.analyze_worker` so it
fires once at module import and is inherited by ``multiprocessing.spawn``
children that re-import the parent module.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from typing import Any

from bpp.utils.logging import get_logger

log = get_logger(__name__)

_SENTINEL = None  # signals child to stop sending results


def _write_analysis_json(path: str, data: list) -> None:
    """Write *data* to *path* atomically (tmp-file + rename).

    A crash or OS error during the write leaves the pre-existing file
    untouched. Used by the analyze worker to persist intermediate
    results so a cancelled run still has something useful on disk.
    """
    dirname = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".analysis.", suffix=".tmp.json", dir=dirname)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _snapshot_config(config: Any) -> dict[str, Any]:
    """Coerce a live :class:`bpp.config_resolver.Config` (or any dict-like)
    into a plain ``dict`` snapshot that's safe to pickle across process
    boundaries.

    Thin wrapper around :class:`bpp.utils.config_snapshot.ConfigSnapshot.from_live`
    that returns the inner dict for back-compat with existing callers.
    New code should call ``ConfigSnapshot.from_live(config)`` directly and
    pass the typed snapshot to subprocess workers.

    The runtime ``Config`` holds a bound method (``_get_conn``) for
    resolving DB-layer values lazily. Bound methods drag their owner's
    class dict through pickle, and ``cls.__dict__`` is a ``mappingproxy``
    which the spawn-method ``ForkingPickler`` refuses. Tests covering
    this regression: tests/test_config_snapshot.py.
    """
    from bpp.utils.config_snapshot import ConfigSnapshot

    return dict(ConfigSnapshot.from_live(config).values)
