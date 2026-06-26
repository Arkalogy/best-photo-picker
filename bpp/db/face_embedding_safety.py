"""Defensive readers for face embedding BLOBs.

Why this exists
---------------
On 2026-06-02 the demo lib had 136 face_embeddings rows whose
``embedding`` BLOB was 1024 bytes instead of the contract-stipulated
512 (= 128 float32). The bytes interpreted as float32 produced
non-finite values (norm = inf, values at 2^65), almost certainly debris
from a SIGKILL-mid-write WAL corruption. A single ``GET
/api/v1/faces/clusters`` call did ``np.stack([np.frombuffer(blob, ...)
for row in rows])`` over the unvalidated list and crashed with
``ValueError: all input arrays must have the same shape`` — taking
the sidebar and the People panel with it.

This module pins the contract once: every reader that decodes an
embedding BLOB does it through ``decode_embedding``. Bad rows are
returned as ``None`` (or filtered, for the batch helper), with a
metric incremented so /health and Activity log surface the count
instead of the user discovering it via a 500.

Contract
--------
- Embedding is float32, ``EMBEDDING_DIM`` floats long.
- BLOB length is exactly ``EMBEDDING_BYTES`` (128 * 4 = 512).
- Values are finite (no NaN / inf).
- L2-norm is positive (not all-zero).

Anything else is treated as corrupt. Callers MUST handle ``None``.

Integration sites
-----------------
- bpp/db/face_queries.py: per-photo cluster fetch
- bpp/db/face_feedback.py: cluster-feedback aggregation
- bpp/db/face_cluster_ops.py: candidate matrix builder
- bpp/web/face_create_helpers.py: duplicate-guard read
- bpp/web/face_extraction_phases.py: orchestrator's embedding loop
- bpp/db/face_identity_remap.py: identity-remap stacker
- bpp/scoring/face_cluster.py: cluster matrix builders

Each site filters with this helper before stacking. The helper is
the only authorized way to turn a stored BLOB back into an ndarray.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Any

import numpy as np

from bpp.utils.logging import get_logger

log = get_logger(__name__)

EMBEDDING_DIM = 128
EMBEDDING_BYTES = EMBEDDING_DIM * 4  # float32

# Running counts, exposed via /api/v1/health → caches.embedding_safety.
# Per-process, reset on restart — that's fine: the value is "since
# last boot," not "since import." Lock-guarded so concurrent reads
# (the clusters endpoint runs N rows in a list comp, the recompute
# endpoint streams thousands) don't race the increment.
_counter_lock = threading.Lock()
_counters: dict[str, int] = {
    "decoded_ok": 0,
    "decoded_bad_size": 0,
    "decoded_non_finite": 0,
    "decoded_zero_norm": 0,
}

# Warning rate-limit state.
#
# A library with thousands of corrupt rows used to write one log line
# per row, drowning every other signal in the server log. Instead we
# log the FIRST occurrence of each (where, reason) pair with full
# detail (row_id + bytes), suppress the bulk, and emit a periodic
# summary every _LOG_SUMMARY_EVERY hits so the operator can still tell
# the cascade is ongoing without scrolling past the noise.
#
# Keyed by (where, reason). Value is the running count for that pair
# since the last summary fired. The lock protects both this dict and
# the counters above.
_LOG_SUMMARY_EVERY = 100
_log_counts: dict[tuple[str, str], int] = {}


def reset_log_state_for_tests() -> None:
    """Test-only: clear the rate-limit state so each test starts fresh."""
    with _counter_lock:
        _log_counts.clear()


def _should_log_now(where: str, reason: str) -> tuple[bool, int]:
    """Decide whether to write this warning to the log right now.

    Returns ``(emit, suppressed_count)``. ``emit`` is True on the
    first occurrence of (where, reason) and again every
    ``_LOG_SUMMARY_EVERY`` occurrences. ``suppressed_count`` is the
    number of warnings squashed since the last emit (for the periodic
    summary line)."""
    with _counter_lock:
        key = (where, reason)
        n = _log_counts.get(key, 0) + 1
        _log_counts[key] = n
        if n == 1:
            return True, 0
        if n % _LOG_SUMMARY_EVERY == 0:
            return True, _LOG_SUMMARY_EVERY - 1
        return False, 0


def get_counters() -> dict[str, int]:
    """Snapshot of decode counters. Read by /api/v1/health.

    The snapshot is a copy — the consumer can't mutate the live
    counters by accident."""
    with _counter_lock:
        return dict(_counters)


def reset_counters_for_tests() -> None:
    """Test-only reset. Not part of the production surface."""
    with _counter_lock:
        for k in _counters:
            _counters[k] = 0


def _bump(key: str) -> None:
    with _counter_lock:
        _counters[key] = _counters.get(key, 0) + 1


def _log_skip(where: str, reason: str, row_id: Any, detail: str) -> None:
    """Emit a rate-limited skip warning.

    Format: first hit per (where, reason) carries the row_id so
    support can find the offending row. Subsequent hits are
    suppressed; every _LOG_SUMMARY_EVERY-th hit emits a summary line
    so the operator can tell the cascade is ongoing."""
    emit, suppressed = _should_log_now(where, reason)
    if not emit:
        return
    if suppressed:
        log.warning(
            "decode_embedding(%s) [%s]: skipped %d more rows since last log (latest row_id=%r, %s)",
            where,
            reason,
            suppressed,
            row_id,
            detail,
        )
    else:
        log.warning(
            "decode_embedding(%s) [%s]: skipping row_id=%r — %s",
            where,
            reason,
            row_id,
            detail,
        )


def decode_embedding(
    blob: bytes | bytearray | memoryview | None,
    *,
    where: str = "face_embeddings",
    row_id: Any = None,
) -> np.ndarray | None:
    """Decode one stored embedding BLOB into a validated ``np.ndarray``.

    Returns ``None`` if the BLOB violates the contract (wrong size,
    non-finite values, zero-norm). Callers MUST filter ``None``.

    ``where`` is a human-readable hint (table or call site) that
    appears in the rate-limited log to make grep-on-server-log easy.
    ``row_id`` is the DB id of the offending row (or any caller-
    friendly identifier). It's included in the first warning per
    (where, reason) pair so support can chase the exact row without
    digging into the DB.
    """
    if blob is None:
        _bump("decoded_bad_size")
        _log_skip(where, "blob_is_none", row_id, "blob is None")
        return None
    n = len(blob)
    if n != EMBEDDING_BYTES:
        _bump("decoded_bad_size")
        _log_skip(
            where,
            "bad_size",
            row_id,
            f"expected {EMBEDDING_BYTES} bytes, got {n} (likely SIGKILL-mid-write corruption)",
        )
        return None
    try:
        arr = np.frombuffer(blob, dtype=np.float32)
    except (ValueError, TypeError) as exc:
        _bump("decoded_bad_size")
        _log_skip(where, "frombuffer_raised", row_id, f"frombuffer raised {exc}")
        return None
    if not bool(np.isfinite(arr).all()):
        _bump("decoded_non_finite")
        _log_skip(where, "non_finite", row_id, "non-finite values (NaN/inf)")
        return None
    norm = float(np.linalg.norm(arr))
    if norm <= 0.0:
        _bump("decoded_zero_norm")
        _log_skip(where, "zero_norm", row_id, "zero-norm embedding")
        return None
    _bump("decoded_ok")
    return arr


def decode_embeddings_filtered(
    items: Iterable[tuple[Any, bytes | memoryview]],
    *,
    where: str = "face_embeddings",
) -> tuple[list[Any], list[np.ndarray]]:
    """Decode a batch of (id, blob) tuples, dropping invalid ones.

    Returns ``(valid_ids, valid_embeddings)`` zipped so callers that
    need to track which DB row each surviving embedding belonged to
    can do so without re-iterating. If everything was bad, both lists
    are empty.

    Convenient at every np.stack site:

        ids, embs = decode_embeddings_filtered(
            ((r["id"], r["embedding"]) for r in rows),
            where="face_cluster_ops.candidate_matrix",
        )
        if not embs:
            return ...  # caller's empty-result path
        matrix = np.stack(embs)
    """
    valid_ids: list[Any] = []
    valid_embs: list[np.ndarray] = []
    for ident, blob in items:
        arr = decode_embedding(blob, where=where, row_id=ident)
        if arr is not None:
            valid_ids.append(ident)
            valid_embs.append(arr)
    return valid_ids, valid_embs
