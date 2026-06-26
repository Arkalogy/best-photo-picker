"""Plugin-facing lifecycle hooks for photo deletion / restore.

Photo-mutation flows (``soft_delete_photos``, ``restore_photos``,
``permanent_delete_photos`` in ``bpp/db/photos.py``) call
``dispatch_photo_deletion(conn, photo_ids, kind)`` AFTER the DB write
has committed.  Plugins register callbacks via
``register_photo_deletion_hook`` to react to those events — keep a
side-index in sync, mirror to a backup disk, send a webhook, etc.

Design notes:

- **Post-commit dispatch**.  Callbacks fire after ``conn.commit()`` so
  even if a plugin handler crashes, the DB write is durable.  This
  also means callbacks see the new state, not the old (the deleted
  rows are gone / the deleted_at column is already populated).

- **Errors are swallowed + logged**.  A misbehaving plugin must not
  break the user-facing delete.  Same pattern as the
  ``SmartAlbumRegistry.on_rename`` and recovery-handler dispatch.

- **Kind values are stable**.  ``"soft"``, ``"restore"``,
  ``"permanent"`` form the public contract; plugins switch on these.
  Adding a new kind is fine; renaming an existing one is a breaking
  change.

- **photo_ids on permanent delete**: the IDs are passed BEFORE the
  ON DELETE CASCADE removes the rows — they're integers, not rows,
  so the values survive the delete just fine.  Plugins that need
  the old filepaths should subscribe to soft-delete (where rows
  still exist), not permanent-delete.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from typing import Literal

from bpp.utils.logging import get_logger

log = get_logger(__name__)

PhotoDeletionKind = Literal["soft", "restore", "permanent"]
PhotoDeletionHook = Callable[[sqlite3.Connection, list[int], PhotoDeletionKind], None]


_hooks: list[PhotoDeletionHook] = []
# Match the locking pattern used by bpp.db.event_hooks (added during
# review followup): register / unregister mutate, dispatch copies under
# the lock and iterates a local copy so a slow plugin handler doesn't
# block concurrent registration from a worker thread.
_hooks_lock = threading.Lock()


def register_photo_deletion_hook(callback: PhotoDeletionHook) -> None:
    """Register a callback invoked after every photo delete / restore.

    The callback receives ``(conn, photo_ids, kind)``:

    - ``conn``: the active sqlite3 connection.  The DB write that
      triggered the dispatch has already committed; the callback can
      issue further reads or writes on the same connection.
    - ``photo_ids``: list of integer photo IDs affected by the
      operation.  Empty list never reaches a hook — dispatch is
      skipped when the underlying operation moved zero rows.
    - ``kind``: ``"soft"`` (moved to recycle bin), ``"restore"``
      (recycle → live), or ``"permanent"`` (hard delete from DB +
      disk).

    Errors raised by callbacks are caught + logged at WARNING level.
    Plugins do not affect the user-facing delete result.
    """
    with _hooks_lock:
        _hooks.append(callback)


def unregister_photo_deletion_hook(callback: PhotoDeletionHook) -> bool:
    """Remove a previously-registered hook.  Returns True if found.

    Mostly useful in tests; plugin authors usually register once at
    load time and never unregister.
    """
    with _hooks_lock:
        try:
            _hooks.remove(callback)
            return True
        except ValueError:
            return False


def dispatch_photo_deletion(
    conn: sqlite3.Connection,
    photo_ids: list[int],
    kind: PhotoDeletionKind,
) -> None:
    """Call every registered hook with ``(conn, photo_ids, kind)``.

    Internal helper — bpp.db.photos calls this after each mutation
    commits.  No-op when ``photo_ids`` is empty so plugins aren't
    woken up for nothing.  Exceptions raised by hooks are swallowed
    + logged so a bad plugin can't break the user-facing delete.
    """
    if not photo_ids:
        return
    with _hooks_lock:
        hooks = list(_hooks)
    if not hooks:
        return
    for hook in hooks:
        try:
            hook(conn, photo_ids, kind)
        except Exception:
            log.warning(
                "Photo deletion hook %s raised on kind=%s (%d ids) — swallowing",
                getattr(hook, "__qualname__", repr(hook)),
                kind,
                len(photo_ids),
                exc_info=True,
            )
