"""Shared Server-Sent-Events helper for streaming background-worker
progress.

The simple worker-progress endpoints (CLIP extraction, face extraction)
all drained their worker's ``progress_queue`` with the same loop: yield
each message as an SSE ``data:`` frame, emit a ``keepalive`` when the
queue starves, synthesize a worker-stopped error if the worker has died,
and stop on a terminal (``done`` / ``error``) message. That loop was
copy-pasted per endpoint; :func:`stream_worker_progress` is the single
home for it.

Two endpoints deliberately do NOT use this helper because they need
more than the common loop:

- ``bp_analysis`` re-checks device trust each iteration and emits
  ``auth_revoked`` on revocation (``_stream_with_revoke_check``).
- ``bp_export`` uses an SSE-comment heartbeat + a grace period before
  declaring the worker dead.

Keep those as the intentional variants; this helper covers the plain case.
"""

from __future__ import annotations

import json
import queue
from collections.abc import Callable, Iterator
from typing import Any

from bpp.constants import PROGRESS_QUEUE_TIMEOUT_S


def sse_event(payload: dict[str, Any]) -> str:
    """One Server-Sent Events ``data:`` frame carrying ``payload`` as
    JSON — the only place the wire format is written."""
    return f"data: {json.dumps(payload)}\n\n"


def stream_worker_progress(
    worker: Any,
    *,
    on_done: Callable[[dict], None] | None = None,
    terminal: tuple[str, ...] = ("done", "error"),
) -> Iterator[str]:
    """Yield SSE ``data:`` frames draining ``worker.progress_queue``.

    Stops on a terminal message (``done`` / ``error`` by default). On
    queue starvation, emits a ``keepalive`` and — if the worker is no
    longer alive — a synthetic worker-stopped ``error`` before stopping.
    ``on_done`` runs when a ``done`` message arrives (e.g. to invalidate
    a cache), before the loop breaks.
    """
    while True:
        try:
            msg = worker.progress_queue.get(timeout=PROGRESS_QUEUE_TIMEOUT_S)
            yield sse_event(msg)
            if msg.get("type") in terminal:
                if msg.get("type") == "done" and on_done is not None:
                    on_done(msg)
                break
        except queue.Empty:
            yield sse_event({"type": "keepalive"})
            if not worker.is_alive:
                yield sse_event({"type": "error", "message": "Worker stopped unexpectedly"})
                break
