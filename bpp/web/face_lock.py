"""Serializing face mutations: the face lock decorator and the guard
that refuses them while an interrupted face extraction resumes.

Split out of :mod:`bpp.web.state` (500-LOC cap); the names are
re-exported there.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

#: Longest a face mutation waits for the face lock before answering 409.
#: A resume that outlived a library switch (join gave up) could otherwise
#: park every request thread for the rest of its run.
FACE_LOCK_TIMEOUT_S = 30.0

FACE_RECOVERY_MESSAGE = "Face extraction is resuming after a restart; wait for it to finish"


def refuse_while_face_recovery(ctx: Any) -> None:
    """409 while an interrupted face extraction resumes on its thread:
    it owns the face tables until it finishes, so nothing else may
    extract faces or move them between clusters meanwhile."""
    store = getattr(ctx, "analysis_store", None)
    if getattr(store, "face_recovery_running", False) is True:
        from bpp.errors import ConflictError

        raise ConflictError(FACE_RECOVERY_MESSAGE, blocker="face_recovery")


def with_face_lock(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Serialize face-mutating operations via face_op_lock and invalidate
    the face cluster map on exit.

    All endpoints that mutate face_embeddings, photo_person_tags, or
    smart_person albums MUST use this decorator. Two contracts:

    1. ``face_op_lock`` serializes the body so concurrent face mutations
       can't interleave.
    2. ``invalidate_face_cluster_map()`` fires in ``finally`` so the
       grid + recompute paths see fresh ``filepath → [cluster_ids]``
       data on the next read.

    Pre-fix every face-mutation endpoint (create, update_bbox, merge,
    dismiss, split, restore, retry, tag_person, untag_person, reassign,
    purge, recluster) relied on the analyse worker's Phase 7
    invalidation. Between mutations, the cache stayed stale — the
    /api/v1/pick handler boosted by the old cluster IDs and the photo
    grid badged faces against the pre-merge state. Wiring the invalidate
    into the decorator closes the gap at one site instead of 13.

    The invalidate fires even on exception: a face mutation that partly
    committed (e.g. a SAVEPOINT rolled back inside a multi-statement
    handler) still leaves the cluster_id columns in a state the cache
    doesn't reflect, so a stale cache after error is the worst outcome.
    Cheap to invalidate; expensive to debug a "why does Alice show as
    Bob" report.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        refuse_while_face_recovery(ctx)
        if not ctx.face_op_lock.acquire(timeout=FACE_LOCK_TIMEOUT_S):
            from bpp.errors import ConflictError

            # A resume that took the lock after the guard above passed
            # is reported as what it is.
            refuse_while_face_recovery(ctx)
            raise ConflictError(
                "Another face operation is still running; try again in a moment",
                blocker="face_lock",
            )
        try:
            return fn(*args, **kwargs)
        finally:
            # Invalidate while still holding the lock, so the next holder
            # (a concurrent reader re-acquiring) sees a fresh map.
            try:
                ctx.invalidate_face_cluster_map()
            finally:
                ctx.face_op_lock.release()

    return wrapper
