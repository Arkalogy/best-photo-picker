"""Post-event hooks plugins subscribe to.

bpp already has a deletion event bus in :mod:`bpp.db.photo_hooks`.
This module exposes the same shape (register / unregister / dispatch
+ swallow-and-log on plugin failure) for the three other points where
plugins commonly want to react:

* **on_post_analyze** — after :class:`bpp.web.analyze_worker.AnalyzeWorker`
  finishes Phase 1 (scoring) and persists the result dicts to DB. Plugins
  can enrich the result dicts before downstream phases consume them.
* **on_post_cluster** — after face / pet clustering completes and the
  cluster IDs have been persisted. Plugins can side-mirror cluster stats
  to a backup store, auto-tag similar-looking people across clusters,
  send a webhook, etc.
* **on_post_import** — after the import worker finishes copying files
  and inserting photos into the DB. Plugins can react to the new
  filepaths (EXIF enrichment, side-cache priming, external mirroring).

Design notes (mirror :mod:`bpp.db.photo_hooks`):

* **Post-commit dispatch.** Callbacks fire AFTER ``conn.commit()`` so
  even if a plugin handler crashes, the user-visible DB write is durable.
* **Errors are swallowed + logged.** A misbehaving plugin must not break
  the user-facing flow. Same trust contract as the photo deletion bus
  and the lifecycle hook firing in :mod:`bpp.plugin_protocol`.
* **Empty payload short-circuits.** An import that landed zero photos
  doesn't fire post_import; a cluster pass with zero new clusters
  doesn't fire post_cluster. Plugins aren't woken up for nothing.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from typing import Any

from bpp.utils.logging import get_logger

log = get_logger(__name__)

# One lock per event bus. register/unregister mutate; dispatch reads
# under the lock and then iterates a local copy so a slow plugin
# handler doesn't block a concurrent registration.
_post_analyze_lock = threading.Lock()
_post_cluster_lock = threading.Lock()
_post_import_lock = threading.Lock()


# ──────────────────────────────────────────────────────────────────
# Post-analyze
# ──────────────────────────────────────────────────────────────────

#: ``(conn, results)`` where ``results`` is the list of score dicts
#: the worker just persisted. Plugins may mutate dicts in place — the
#: dispatcher does NOT defensively copy.
PostAnalyzeHook = Callable[[sqlite3.Connection, list[dict[str, Any]]], None]

_post_analyze_hooks: list[PostAnalyzeHook] = []


def register_post_analyze_hook(callback: PostAnalyzeHook) -> None:
    """Subscribe a callback to fire after every analyze (scoring) pass.

    Callback signature: ``(conn, results)``. The DB write has already
    committed. Errors are logged at WARNING and swallowed.
    """
    with _post_analyze_lock:
        _post_analyze_hooks.append(callback)


def unregister_post_analyze_hook(callback: PostAnalyzeHook) -> bool:
    with _post_analyze_lock:
        try:
            _post_analyze_hooks.remove(callback)
            return True
        except ValueError:
            return False


def dispatch_post_analyze(conn: sqlite3.Connection, results: list[dict[str, Any]]) -> None:
    if not results:
        return
    with _post_analyze_lock:
        hooks = list(_post_analyze_hooks)
    if not hooks:
        return
    for hook in hooks:
        try:
            hook(conn, results)
        except Exception:
            log.warning(
                "Post-analyze hook %s raised (%d results) — swallowing",
                getattr(hook, "__qualname__", repr(hook)),
                len(results),
                exc_info=True,
            )


# ──────────────────────────────────────────────────────────────────
# Post-cluster
# ──────────────────────────────────────────────────────────────────

#: ``(conn, kind, n_clusters)`` where ``kind`` is ``"face"`` or
#: ``"pet"`` (plugins may introduce more — keep them prefixed).
PostClusterHook = Callable[[sqlite3.Connection, str, int], None]

_post_cluster_hooks: list[PostClusterHook] = []


def register_post_cluster_hook(callback: PostClusterHook) -> None:
    """Subscribe a callback to fire after every clustering pass.

    Callback signature: ``(conn, kind, n_clusters)`` where ``kind``
    is a stable string (``"face"`` / ``"pet"``). Plugins MAY introduce
    new kinds; use a plugin-prefixed string to avoid collisions.
    """
    with _post_cluster_lock:
        _post_cluster_hooks.append(callback)


def unregister_post_cluster_hook(callback: PostClusterHook) -> bool:
    with _post_cluster_lock:
        try:
            _post_cluster_hooks.remove(callback)
            return True
        except ValueError:
            return False


def dispatch_post_cluster(conn: sqlite3.Connection, kind: str, n_clusters: int) -> None:
    with _post_cluster_lock:
        hooks = list(_post_cluster_hooks)
    if not hooks:
        return
    for hook in hooks:
        try:
            hook(conn, kind, n_clusters)
        except Exception:
            log.warning(
                "Post-cluster hook %s raised on kind=%s (%d clusters) — swallowing",
                getattr(hook, "__qualname__", repr(hook)),
                kind,
                n_clusters,
                exc_info=True,
            )


# ──────────────────────────────────────────────────────────────────
# Post-import
# ──────────────────────────────────────────────────────────────────

#: ``(conn, photo_ids, filepaths)`` for the photos just imported.
#: ``filepaths`` parallels ``photo_ids`` index-wise so plugins that
#: only need the filepaths can ignore the ids.
PostImportHook = Callable[[sqlite3.Connection, list[int], list[str]], None]

_post_import_hooks: list[PostImportHook] = []


def register_post_import_hook(callback: PostImportHook) -> None:
    """Subscribe a callback to fire after every import batch."""
    with _post_import_lock:
        _post_import_hooks.append(callback)


def unregister_post_import_hook(callback: PostImportHook) -> bool:
    with _post_import_lock:
        try:
            _post_import_hooks.remove(callback)
            return True
        except ValueError:
            return False


def dispatch_post_import(
    conn: sqlite3.Connection,
    photo_ids: list[int],
    filepaths: list[str],
) -> None:
    if not photo_ids:
        return
    with _post_import_lock:
        hooks = list(_post_import_hooks)
    if not hooks:
        return
    for hook in hooks:
        try:
            hook(conn, photo_ids, filepaths)
        except Exception:
            log.warning(
                "Post-import hook %s raised (%d photos) — swallowing",
                getattr(hook, "__qualname__", repr(hook)),
                len(photo_ids),
                exc_info=True,
            )


# Test isolation helper — clears every event bus the module owns.
def _reset_for_tests() -> None:
    with _post_analyze_lock:
        _post_analyze_hooks.clear()
    with _post_cluster_lock:
        _post_cluster_hooks.clear()
    with _post_import_lock:
        _post_import_hooks.clear()
