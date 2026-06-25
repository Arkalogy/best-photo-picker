"""P4b — WebAppState collaborator: analysis-related background state.

Pre-P4 ``WebAppState`` carried five attributes related to the phash
compute + thumbnail warm lifecycle as bare fields:

* ``phash_ready`` — a ``threading.Event`` flipped True once the phash
  compute thread has populated phashes for every analysed photo.
* ``_phash_generation`` — monotonic counter bumped by
  ``switch_library`` so an orphan compute thread from the old library
  refuses to ``set()`` the new library's Event.
* ``_compute_thread`` / ``_warm_thread`` — handles for the two daemon
  threads (phash compute + thumbnail cache warm). Tracked so
  ``switch_library`` can join them before swapping the DB underneath.
* ``_cancel_warm`` — ``threading.Event`` the warmer polls between
  iterations so library switch / shutdown can drain it cleanly.

Five interrelated fields, one logical concern: "the in-flight
analysis-derived background work for this library." The audit grouped
them under ``AnalysisStore`` so a future maintainer doesn't have to
piece the relationship back together from cross-file reads.

This module defines :class:`AnalysisStore`. WebAppState constructs one
at start; property delegates preserve the legacy bare-attribute access
so the ~3,800 call sites the audit counted continue to work unchanged.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from bpp.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class AnalysisStore:
    """Bundle of analysis-derived background state owned by one WebAppState.

    Field semantics inherited from pre-P4 ``WebAppState``; see module
    docstring for the full story. The generation counter is the
    load-bearing invariant: switch_library increments it; threads
    spawned for library A check it against their captured generation
    before signaling Events, so an orphaned thread from a switched-
    away library can't write into the new library's state.
    """

    #: Set ``True`` when the phash compute thread has populated phashes
    #: for every analysed photo. Replaced (new Event) on library switch.
    phash_ready: threading.Event = field(default_factory=threading.Event)

    #: Monotonic counter. ``switch_library`` bumps it; spawning threads
    #: capture it at spawn time and refuse to fire Event sets if the
    #: current generation has moved on.
    phash_generation: int = 0

    #: Thread handles. Tracked (not fire-and-forget) so
    #: ``switch_library`` / ``shutdown`` can join them before the DB
    #: pool is closed.
    compute_thread: threading.Thread | None = None
    warm_thread: threading.Thread | None = None

    #: Cancel signal the thumbnail warmer polls between iterations.
    #: Independent of the workers' cancel tokens because the warmer is
    #: a daemon helper, not a registered BackgroundWorker.
    cancel_warm: threading.Event = field(default_factory=threading.Event)

    #: Cancel signal + live progress for the phash backfill compute
    #: thread. The backfill is a CPU-heavy startup pass (decode + hash
    #: every not-yet-hashed photo) that, on a large real library, must
    #: be (a) cancellable on switch/shutdown so it doesn't keep churning
    #: and (b) visible — it pegged a machine + ran silently before this.
    #: ``phash_running``/``phash_done``/``phash_total`` are surfaced via
    #: /api/v1/status.
    phash_cancel: threading.Event = field(default_factory=threading.Event)
    phash_running: bool = False
    phash_done: int = 0
    phash_total: int = 0

    #: Re-entrancy queue for the derived-recovery pipeline: when an
    #: analyze finishes while a recovery thread is still running, its
    #: data is parked here and the running thread re-spawns with it at
    #: the end instead of two threads racing the same rows.
    recovery_rerun: list[Any] | None = None

    def bump_generation_and_reset_phash(self) -> None:
        """Atomically: increment the generation counter and install a
        fresh ``phash_ready`` Event.

        Called by ``switch_library`` so orphan compute threads from the
        old library refuse to signal the new library's Event.
        """
        # Order matters: replace the Event FIRST so any thread that
        # captured `phash_ready` before the bump sees the OLD Event;
        # the generation guard then prevents it from setting the new
        # one. Reversing the order would leave a tiny window where a
        # late writer could set the NEW Event under the OLD generation.
        self.phash_ready = threading.Event()
        self.phash_generation += 1
        # Fresh cancel token + reset progress for the new library so an
        # orphaned backfill can't leave the new lib showing stale "running".
        self.phash_cancel = threading.Event()
        self.phash_running = False
        self.phash_done = 0
        self.phash_total = 0

    def join_threads(self, timeout: float) -> None:
        """Signal cancel + join both daemon threads.

        ``switch_library`` calls this before swapping the DB connection
        pool so the threads release any open conns first. Best-effort:
        a thread that won't join within ``timeout`` triggers an ERROR
        log but doesn't block the caller.
        """
        self.cancel_warm.set()
        # Signal the phash backfill to stop at its next loop check so it
        # doesn't keep decoding images while we tear down the DB pool.
        self.phash_cancel.set()
        for name, t in (("warm_thread", self.warm_thread), ("compute_thread", self.compute_thread)):
            if t is not None and t.is_alive():
                t.join(timeout=timeout)
                if t.is_alive():
                    log.error(
                        "%s did not stop within %ds — DB connections may race on shutdown",
                        name,
                        timeout,
                    )
        # Clear references so a stale handle doesn't survive into the
        # next library lifetime.
        self.warm_thread = None
        self.compute_thread = None
