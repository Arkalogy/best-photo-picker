"""Serializing face mutations: the face lock decorator and the guards
that refuse them while an interrupted face extraction resumes or a
face-model switch is still placing people's names.

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

FACE_SWITCH_MESSAGE = (
    "A face-model switch is still placing people's names; "
    "run the face analysis to completion before changing clusters or names"
)


def refuse_while_face_recovery(ctx: Any) -> None:
    """409 while an interrupted face extraction resumes on its thread:
    it owns the face tables until it finishes, so nothing else may
    extract faces or move them between clusters meanwhile."""
    store = getattr(ctx, "analysis_store", None)
    if getattr(store, "face_recovery_running", False) is True:
        from bpp.errors import ConflictError

        raise ConflictError(FACE_RECOVERY_MESSAGE, blocker="face_recovery")


def refuse_while_switch_pending(ctx: Any) -> None:
    """409 while a face-model switch has names it has not placed yet: a
    recluster, merge or rename would work on half-placed people, and the
    next chunk would place names onto clusters the user just changed."""
    from bpp.db.face_name_placement import switch_pending

    get_conn = getattr(ctx, "get_conn", None)
    if get_conn is None:  # a bare test double without a DB
        return
    if switch_pending(get_conn()):
        from bpp.errors import ConflictError

        raise ConflictError(FACE_SWITCH_MESSAGE, blocker="face_switch")


def allow_during_face_switch(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Mark an endpoint that :func:`with_face_lock` must NOT refuse while
    a face-model switch is pending: the ones that start face extraction,
    which is what finishes the switch."""
    fn.allow_during_face_switch = True  # type: ignore[attr-defined]
    return fn


def with_face_lock(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Serialize face-mutating operations via face_op_lock and invalidate
    the face cluster map on exit.

    All endpoints that mutate face_embeddings, photo_person_tags, or
    smart_person albums MUST use this decorator. Three contracts:

    1. ``face_op_lock`` serializes the body so concurrent face mutations
       can't interleave.
    2. A pending face-model switch refuses the body (409, ``face_switch``)
       unless the endpoint is marked :func:`allow_during_face_switch`.
       The check runs again once the lock is held, because the thread
       that held it before us may have been the extraction that began
       the switch.
    3. ``invalidate_face_cluster_map()`` fires in ``finally`` so the
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
    guard_switch = not getattr(fn, "allow_during_face_switch", False)

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        from bpp.web.state import get_ctx

        ctx = get_ctx()
        refuse_while_face_recovery(ctx)
        if guard_switch:
            refuse_while_switch_pending(ctx)
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
            if guard_switch:
                refuse_while_switch_pending(ctx)
            return fn(*args, **kwargs)
        finally:
            # Invalidate while still holding the lock, so the next holder
            # (a concurrent reader re-acquiring) sees a fresh map.
            try:
                ctx.invalidate_face_cluster_map()
            finally:
                ctx.face_op_lock.release()

    return wrapper
