"""Server-Sent-Events streaming of background-worker progress.

Every progress route (analyze, import, CLIP, face extraction, export)
streams through :func:`stream_worker_progress`. It reads the worker's
event log, not its queue, so each reader (a second tab, a reload, a
paired phone) sees every message of the run; emits a ``keepalive`` while
nothing happens; synthesizes a worker-stopped error when the worker has
died; and, for a paired device, re-checks that the device is still
trusted and ends the stream with ``auth_revoked`` when it is not.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

from bpp.constants import PROGRESS_QUEUE_TIMEOUT_S


def sse_event(payload: dict[str, Any]) -> str:
    """One Server-Sent Events ``data:`` frame carrying ``payload`` as
    JSON — the only place the wire format is written."""
    return f"data: {json.dumps(payload)}\n\n"


def captured_device_fingerprint() -> str | None:
    """The paired device behind the current request, or None for the local
    app (loopback / app token). Captured at the handshake, while the
    request context exists; the generator runs after it is gone. Only
    paired devices can be revoked mid-stream."""
    from flask import g

    from bpp.web.share import PRINCIPAL_LAN_DEVICE

    principal = getattr(g, "bpp_principal", None)
    if principal is None or principal.kind != PRINCIPAL_LAN_DEVICE:
        return None
    return principal.fingerprint


def _revoked(ctx: Any, device_fp: str | None) -> bool:
    """A paired device loses its stream when its trust is revoked or
    when sharing is switched off altogether (the switch is what the
    request guard checks for new requests; open streams re-check it)."""
    if device_fp is None:
        return False
    from bpp.web.share_devices import is_device_trusted
    from bpp.web.share_runtime import is_lan_sharing_enabled

    conn = ctx.get_conn()
    return not is_lan_sharing_enabled(conn) or not is_device_trusted(conn, device_fp)


_AUTH_REVOKED = {"type": "error", "message": "auth_revoked"}
_WORKER_STOPPED = {"type": "error", "message": "Worker stopped unexpectedly"}


def stream_worker_progress(
    worker: Any,
    *,
    on_done: Callable[[dict], None] | None = None,
    terminal: tuple[str, ...] = ("done", "error", "cancelled"),
    ctx: Any = None,
    device_fp: str | None = None,
) -> Iterator[str]:
    """Yield SSE ``data:`` frames for ``worker``'s current run.

    Stops on a terminal message (``done`` / ``error`` / ``cancelled``).
    ``on_done`` runs when ``done`` arrives, before the stream ends, once
    per run however many readers see it. With ``ctx`` and ``device_fp`` (a
    paired device's fingerprint) the device's trust and the sharing
    switch are re-checked before every batch of events and on every
    keepalive: one settings read per wake-up, and nothing private is
    sent after the device lost its standing.
    """
    run_id, seq = int(worker.run_id), 0
    while True:
        run_id, seq, msgs = worker.events_since(run_id, seq, timeout=PROGRESS_QUEUE_TIMEOUT_S)
        if not msgs:
            yield sse_event({"type": "keepalive"})
            if _revoked(ctx, device_fp):
                yield sse_event(_AUTH_REVOKED)
                return
            if not worker.is_alive:
                # The worker may have ended between the wait and this
                # check: its last messages (done / cancelled / error) are
                # in the log, so read once more before calling it dead.
                run_id, seq, msgs = worker.events_since(run_id, seq)
                if not msgs:
                    yield sse_event(_WORKER_STOPPED)
                    return
            else:
                continue
        if device_fp is not None and _revoked(ctx, device_fp):
            yield sse_event(_AUTH_REVOKED)
            return
        for msg in msgs:
            yield sse_event(msg)
            if msg.get("type") in terminal:
                if msg.get("type") == "done" and on_done is not None:
                    _finalize_once(worker, run_id, msg, on_done)
                return


def _finalize_once(worker: Any, run_id: int, msg: dict, on_done: Callable[[dict], None]) -> None:
    """Run the done hook for this run unless another reader already did."""
    claim = getattr(worker, "claim_finalize", None)
    if claim is None or claim(run_id):
        on_done(msg)
