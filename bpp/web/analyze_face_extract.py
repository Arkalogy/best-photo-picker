"""Phase-2 (face extraction + clustering) subprocess machinery.

Face extraction loads SCRFD + BlazeFace + dlib + SFace +
HandLandmarker. Two execution architectures are supported:

1. **Chunked subprocesses** (default — workers=1 / thread pool).
   Bounds the per-child memory peak by capping each subprocess at
   ``_FACE_EXTRACTION_CHUNK_SIZE`` photos. The OS reclaims model +
   image memory between chunks — without this the ONNX/TFLite arena
   allocators accumulate state across photos and eventually SIGSEGV
   the child near the end of a large run.

2. **Single subprocess + ProcessPool** (workers > 1 + pool=process).
   The chunk loop is skipped because each ProcessPool worker has its
   own interpreter + model arena, so memory recovery happens per
   worker at pool close.

Imported from ``analyze_worker.py``; not part of the public API.

P2: queue / sentinel / drain / join machinery moved to
:class:`bpp.utils.subprocess_runner.BoundedSubprocessRunner`. This
module keeps the face-extraction-specific bits:

* ``_face_extraction_worker`` — the child entry point;
* ``FaceExtractionPhase`` — the Phase implementation;
* ``run_face_extraction_subprocess`` — the chunk-loop orchestrator
  that drives the runner once per chunk and aggregates totals.
"""

from __future__ import annotations

import multiprocessing
import traceback
from typing import Any

from bpp.utils.logging import get_logger
from bpp.utils.subprocess_runner import (
    SENTINEL,
    BoundedSubprocessRunner,
)
from bpp.web.analyze_subprocess import _snapshot_config

log = get_logger(__name__)

#: Cap photos processed per face-extraction subprocess. ML model
#: memory (SCRFD + BlazeFace + dlib + SFace + HandLandmarker) plus
#: per-photo decoded ndarrays accumulate in the child's RSS as the
#: ONNX/TFLite arena allocators don't release pages until the
#: process exits. On a fresh child loading models from cache, ~250
#: photos fits comfortably in 2 GB; 1500+ in one shot reliably
#: SIGSEGV's the child near the end. Keep this conservative — model
#: re-load cost is ~5-10 s per chunk but we get the full memory
#: reset for free.
_FACE_EXTRACTION_CHUNK_SIZE = 250

#: Per-message timeout for the face-extraction runner. Each photo can
#: take seconds with cold models; 10 minutes per message is generous
#: enough that we don't false-positive on a slow run and tight enough
#: that a truly stuck child doesn't hang the server forever.
_FACE_MESSAGE_TIMEOUT_S = 600.0


def _face_extraction_worker(
    with_faces: list[dict[str, Any]],
    config: dict[str, Any],
    db_path: str,
    result_queue: multiprocessing.Queue,
    cancel_event: multiprocessing.Event | None = None,
) -> None:
    """Child process: extract face embeddings + cluster, then exit.

    All face models load in this child and die when it returns.

    ``cancel_event`` is the cross-process side of the
    :class:`bpp.utils.cancel.ProcessCancellation` token from the parent.
    It's passed through to ``extract_and_cluster_faces`` so the inner
    detect/embed loop AND the ProcessPool branch can poll between
    photos and exit promptly when the user clicks Cancel.
    """
    try:
        from bpp.db.connection import init_db
        from bpp.db.photos import get_photo_id_map_by_paths
        from bpp.web.face_worker import extract_and_cluster_faces

        conn = init_db(db_path)
        photo_map = get_photo_id_map_by_paths(
            conn,
            [item["filepath"] for item in with_faces],
        )
        max_long_side = config.get("max_long_side", 1024)
        face_conf = float(config.get("face_detection_confidence", 0.3))

        def _progress(msg: dict) -> None:
            result_queue.put(msg)

        faces_found, face_clusters = extract_and_cluster_faces(
            conn,
            with_faces,
            photo_map,
            max_long_side,
            face_conf,
            config,
            progress_callback=_progress,
            post_cluster_dedup=True,
            cancel_event=cancel_event,
        )
        result_queue.put(
            {
                "type": "result",
                "faces_found": faces_found,
                "face_clusters": face_clusters,
            }
        )
        # No conn.close() — the pool's lifecycle is owned by
        # close_all_connections(). This child process exits after the
        # queue.put() above, so the pool dies with it (project rule).
    except Exception as e:
        result_queue.put(
            {
                "type": "fatal_error",
                "error": str(e),
                "traceback": traceback.format_exc(),
            }
        )
    finally:
        result_queue.put(SENTINEL)


class FaceExtractionPhase:
    """Phase wrapping :func:`_face_extraction_worker` for the runner.

    Accumulator captures progress-rewriting state plus the final
    ``faces_found`` / ``face_clusters`` numbers the worker emits as a
    typed result message. The chunk-loop offset / total override let
    the runner emit globally-correct progress numbers even when each
    chunk only knows about its local slice.
    """

    name = "face-extraction"

    def __init__(
        self,
        config_snapshot: dict[str, Any],
        db_path: str,
        progress_offset: int = 0,
        progress_total: int | None = None,
    ) -> None:
        self.config_snapshot = config_snapshot
        self.db_path = db_path
        self.progress_offset = progress_offset
        self.progress_total = progress_total

    def target(self):
        return _face_extraction_worker

    def build_args(
        self,
        payload: list[dict[str, Any]],
        result_queue: multiprocessing.Queue,
        cancel_event: multiprocessing.synchronize.Event,
    ) -> tuple[Any, ...]:
        return (payload, self.config_snapshot, self.db_path, result_queue, cancel_event)

    def initial_state(self) -> dict[str, int]:
        return {"faces_found": 0, "face_clusters": 0}

    def reduce(
        self,
        state: dict[str, int],
        msg: Any,
    ) -> tuple[dict[str, int], dict[str, Any] | None]:
        if not isinstance(msg, dict):
            log.warning("Face extraction phase: unexpected message shape: %r", msg)
            return state, None
        mtype = msg.get("type")
        if mtype == "face_progress":
            # Re-frame chunk-local progress into the global counter so
            # the UI sees a single monotonic progress bar across all
            # chunks. Producing a NEW dict (not mutating msg) keeps
            # the reducer pure.
            global_msg = dict(msg)
            global_msg["current"] = self.progress_offset + msg.get("current", 0)
            if self.progress_total is not None:
                global_msg["total"] = self.progress_total
            return state, global_msg
        if mtype == "result":
            state["faces_found"] = int(msg.get("faces_found", 0))
            state["face_clusters"] = int(msg.get("face_clusters", 0))
            return state, None
        # Unknown typed dict — pass through for the UI if it looks
        # like a UI hint, else drop. Be conservative: only known shapes
        # forward.
        return state, None


def _run_face_extraction_chunk(
    with_faces: list[dict[str, Any]],
    config_snapshot: dict[str, Any],
    db_path: str,
    progress_callback: Any | None = None,
    progress_offset: int = 0,
    progress_total: int | None = None,
    cancel_event: Any = None,
) -> tuple[int, int, int | None, bool]:
    """Spawn one face-extraction subprocess for a single chunk via the runner.

    Returns ``(faces_found, face_clusters, child_pid, ok)`` where
    ``ok`` is False if the subprocess crashed or timed out. The
    parent uses ``ok`` to decide whether to continue with the next
    chunk or surface a fatal error.

    ``progress_offset`` shifts the chunk's ``current`` counter into
    the global progress space so the UI sees a single monotonic
    progress bar across all chunks. ``progress_total`` overrides the
    chunk's reported total with the global photo count.
    """
    log.info(
        "Face extraction chunk starting (%d photos, offset=%d)",
        len(with_faces),
        progress_offset,
    )
    phase = FaceExtractionPhase(
        config_snapshot=config_snapshot,
        db_path=db_path,
        progress_offset=progress_offset,
        progress_total=progress_total,
    )
    # daemon=False is REQUIRED so the chunk subprocess can spawn its
    # own ProcessPoolExecutor workers when `_face_extract_pool=process`
    # is set. Daemonic processes are forbidden from having children
    # by Python's multiprocessing module — daemon=True would crash the
    # chunk with "daemonic processes are not allowed to have children"
    # the moment the inner ProcessPool starts.
    runner: BoundedSubprocessRunner = BoundedSubprocessRunner(
        phase,
        message_timeout_s=_FACE_MESSAGE_TIMEOUT_S,
        daemon=False,
    )

    # The runner doesn't know whether ``ok`` should be False on
    # timeout/crash — that's a phase-aware decision. We wrap the
    # progress_callback to detect the "error" tick the runner emits
    # on timeout / fatal_error so we can return ok=False without
    # needing the runner to expose a crashed flag.
    saw_error = {"flag": False}

    def _wrapped_progress(msg: dict) -> None:
        if msg.get("type") == "error":
            saw_error["flag"] = True
        if progress_callback:
            progress_callback(msg)

    state, child_pid = runner.run(
        with_faces, cancel_event=cancel_event, progress_callback=_wrapped_progress
    )
    ok = not saw_error["flag"]
    log.info(
        "Face extraction chunk done (pid=%s, ok=%s): %d faces, %d clusters",
        child_pid,
        ok,
        state["faces_found"],
        state["face_clusters"],
    )
    return state["faces_found"], state["face_clusters"], child_pid, ok


def _config_uses_process_pool(config_snapshot: dict[str, Any]) -> bool:
    """Decide whether the chunk-level loop should be skipped.

    Two requirements: workers > 1 (otherwise there's no parallelism to
    care about) AND pool kind = "process" (the only case where memory
    recovery happens at *worker-process* teardown rather than at
    *chunk-subprocess* teardown). When both are true, chunking is
    redundant — each ProcessPool worker has its own model arena and
    Python interpreter, so memory reclaims at pool close. Chunking
    on top of that would just pay the model-load cost N extra times.
    """
    try:
        n_workers = int(config_snapshot.get("_face_extract_workers") or 1)
    except (TypeError, ValueError):
        n_workers = 1
    if n_workers <= 1:
        return False
    pool_kind = (config_snapshot.get("_face_extract_pool") or "process").lower()
    return pool_kind == "process"


def run_face_extraction_subprocess(
    with_faces: list[dict[str, Any]],
    config: Any,
    db_path: str,
    progress_callback: Any | None = None,
    cancel_event: Any = None,
) -> tuple[int, int, int | None]:
    """Run face extraction + clustering in a memory-safe subprocess.

    Returns ``(faces_found, face_clusters, last_child_pid)``.

    Two architectures depending on parallelism config:

    1. **Chunked subprocesses** (default — workers=1 / thread pool).
       The photo list is split into chunks of
       :data:`_FACE_EXTRACTION_CHUNK_SIZE`. Each chunk gets its own
       fresh subprocess via :class:`BoundedSubprocessRunner` that
       loads SCRFD + BlazeFace + dlib + SFace + HandLandmarker,
       processes its slice, then exits. The OS reclaims model +
       image memory between chunks — without this the ONNX/TFLite
       arena allocators accumulate state across photos and eventually
       SIGSEGV the child near the end of a large run. Throughput is
       dominated by per-photo inference (~500 ms each), so the
       per-chunk model-reload cost is small.

    2. **Single subprocess + ProcessPool** (workers > 1 + pool=process).
       The chunk loop is skipped. One face-extraction subprocess
       starts, and *its* internal ProcessPool of N workers handles
       all photos in one shot. Each worker process has its own
       interpreter + model arena, so memory recovery happens
       per-worker at pool close — chunking on top would just pay
       model-load cost N extra times per chunk for nothing. The
       savings amortize across the whole library: 4 workers x 1
       model load (instead of 4 x N_chunks model loads) plus full
       parallelism over the entire run.

    The parent (server) keeps the SQLite connection pool and the
    Flask request loop alive throughout in either mode, so the UI
    stays responsive. ``progress_callback`` receives
    ``{"type": "face_progress", "current", "total", "filepath"}``
    messages with ``current`` re-framed into the global counter so
    the UI sees a single monotonic progress bar.

    Crash handling: if a chunk SIGSEGVs or times out, the parent
    logs a warning and continues with the next chunk (chunked mode)
    or aborts (single-subprocess mode — there's nothing to fall
    back to). Embeddings and cluster assignments written to the DB
    before the crash are kept (incremental commit pattern), so
    retrying picks up where it stopped.

    Cancellation (P1): ``cancel_event`` accepts the unified token
    contract. The chunk loop polls between chunks so a cancel signal
    halts at the next chunk boundary (≤ one chunk of work). The child
    also gets a picklable mp.Event so its inner detect/embed loop and
    ProcessPool branch can poll per photo. The runner normalizes the
    shape — see :class:`BoundedSubprocessRunner`.
    """
    if not with_faces:
        return 0, 0, None

    # Snapshot the live Config (which holds a bound method for DB-layer
    # resolution) into a plain dict before pickling args for the child.
    # See _snapshot_config docstring for the mappingproxy gotcha.
    config_snapshot = _snapshot_config(config)

    # P1 cancel token — kept here in the orchestrator (not delegated to
    # the runner) because the between-chunk poll needs the parent-side
    # token, not the picklable mp.Event the runner would build.
    # ``parent_token`` is what we poll between chunks; the runner re-
    # normalizes its own copy for each chunk's child.
    from bpp.utils.cancel import as_token

    parent_token = as_token(cancel_event)

    total = len(with_faces)

    # Architecture #2: ProcessPool inside a single subprocess. Skip
    # the chunk loop entirely — chunking would just pay duplicate
    # model-load costs for every chunk, killing the parallelism win.
    if _config_uses_process_pool(config_snapshot):
        log.info(
            "Face extraction (single-subprocess + ProcessPool, %d photos)",
            total,
        )
        faces_found, face_clusters, pid, _ok = _run_face_extraction_chunk(
            with_faces,
            config_snapshot,
            db_path,
            progress_callback=progress_callback,
            progress_offset=0,
            progress_total=total,
            cancel_event=cancel_event,
        )
        return faces_found, face_clusters, pid

    # Architecture #1: chunked subprocesses (memory-safe for the
    # workers=1 / thread-pool path that's vulnerable to allocator
    # accumulation across the full library).
    chunk_size = max(1, _FACE_EXTRACTION_CHUNK_SIZE)
    total_faces = 0
    last_clusters = 0
    last_pid: int | None = None

    for start in range(0, total, chunk_size):
        # Between-chunk cancel check (P1). Without this, a 5,000-photo
        # run would have to grind through every remaining chunk before
        # the parent could stop it. Bound is ≤ one chunk of work.
        if parent_token is not None and parent_token.is_set():
            log.info(
                "Face extraction cancelled before chunk %d/%d",
                (start // chunk_size) + 1,
                (total + chunk_size - 1) // chunk_size,
            )
            break
        chunk = with_faces[start : start + chunk_size]
        log.info(
            "Face extraction chunk %d/%d (%d photos)",
            (start // chunk_size) + 1,
            (total + chunk_size - 1) // chunk_size,
            len(chunk),
        )
        chunk_faces, chunk_clusters, pid, ok = _run_face_extraction_chunk(
            chunk,
            config_snapshot,
            db_path,
            progress_callback=progress_callback,
            progress_offset=start,
            progress_total=total,
            cancel_event=cancel_event,
        )
        last_pid = pid
        # Each chunk re-runs clustering over ALL embeddings in the DB
        # (extract_and_cluster_faces is incremental-cluster aware), so
        # the LAST chunk's cluster count is the authoritative number.
        # `faces_found` accumulates across chunks because it counts
        # newly-extracted faces in each call.
        total_faces += chunk_faces
        # Only update cluster count when the chunk actually found faces —
        # a trailing chunk with 0 faces returns 0 clusters, which would
        # overwrite the real count from earlier chunks.
        if chunk_clusters > 0:
            last_clusters = chunk_clusters
        if not ok:
            log.warning(
                "Face extraction chunk %d failed — continuing with next chunk. "
                "Already-written embeddings are kept; the user can retry.",
                (start // chunk_size) + 1,
            )

    log.info(
        "Face extraction across %d chunks done: %d faces, %d clusters",
        (total + chunk_size - 1) // chunk_size,
        total_faces,
        last_clusters,
    )
    return total_faces, last_clusters, last_pid
