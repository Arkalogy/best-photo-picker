"""Phase-1 (scoring) subprocess machinery for the analyze worker.

Scoring loads 8+ ML models whose memory the ONNX/TFLite arena
allocators never release. Running it in a dedicated subprocess
guarantees the OS reclaims every page when the child exits.

This module owns:

* ``_scoring_worker`` — the subprocess entry point that loads the
  models, scores each image, and pushes ``(idx, result, basename)``
  tuples through a ``multiprocessing.Queue``;
* ``ScoringPhase`` — the :class:`bpp.utils.subprocess_runner.Phase`
  implementation that wraps the worker plus the result-accumulation
  reducer;
* ``run_scoring_subprocess`` — the parent-side runner entry point
  that drives the phase through :class:`BoundedSubprocessRunner`.

Imported from ``analyze_worker.py``; not part of the public API.

P2: this module's queue / sentinel / drain / join machinery moved to
``bpp.utils.subprocess_runner``. The fields kept here are scoring-
specific: the message reducer, the worker's per-photo loop, and the
input shape (``list[str]`` of file paths).
"""

from __future__ import annotations

import multiprocessing
import os
import traceback
from typing import Any

from bpp.utils.logging import get_logger
from bpp.utils.subprocess_runner import (
    SENTINEL,
    BoundedSubprocessRunner,
)
from bpp.web.analyze_subprocess import _snapshot_config

log = get_logger(__name__)


def _scoring_worker(
    images: list[str],
    config: dict[str, Any],
    db_path: str,
    result_queue: multiprocessing.Queue,
    cancel_event: multiprocessing.Event | None,
) -> None:
    """Child process entry point: score images and put results on queue.

    Runs entirely in the child — all model memory dies when this returns.
    """
    try:
        from bpp.scoring.aggregate import (
            init_analysis_db,
            process_one,
        )

        max_long_side = config.get("max_long_side", 1024)
        face_conf = float(config.get("face_detection_confidence", 0.3))

        from bpp.constants import MODEL_TOGGLE_KEYS

        model_toggles = {k: config.get(k, True) for k in MODEL_TOGGLE_KEYS}
        extra_config = {
            "pet_detection_confidence": float(config.get("pet_detection_confidence", 0.2)),
            "pet_input_size": int(config.get("pet_input_size", 1024)),
        }

        init_analysis_db(db_path)

        for i, filepath in enumerate(images):
            if cancel_event and cancel_event.is_set():
                break
            result = process_one(
                (
                    filepath,
                    max_long_side,
                    db_path,
                    face_conf,
                    model_toggles,
                    extra_config,
                )
            )
            # Send (index, result, filepath) so parent can track progress
            result_queue.put((i, result, os.path.basename(filepath)))
    except Exception as e:
        # Fatal crash (import error, OOM, etc.) — surface to parent.
        # BoundedSubprocessRunner's drain loop interprets this shape.
        result_queue.put(
            {
                "type": "fatal_error",
                "error": str(e),
                "traceback": traceback.format_exc(),
            }
        )
    finally:
        result_queue.put(SENTINEL)


class ScoringPhase:
    """Phase wrapping :func:`_scoring_worker` for BoundedSubprocessRunner.

    Holds the (config_snapshot, db_path) closure values the runner can't
    know about, plus the total image count needed to emit consistent
    progress messages. The runner gives us the queue + cancel event;
    we hand the worker the rest.
    """

    name = "scoring"

    def __init__(self, config_snapshot: dict[str, Any], db_path: str, total: int) -> None:
        self.config_snapshot = config_snapshot
        self.db_path = db_path
        self.total = total

    def target(self):
        return _scoring_worker

    def build_args(
        self,
        payload: list[str],
        result_queue: multiprocessing.Queue,
        cancel_event: multiprocessing.synchronize.Event,
    ) -> tuple[Any, ...]:
        # _scoring_worker signature: (images, config, db_path, queue, cancel)
        return (payload, self.config_snapshot, self.db_path, result_queue, cancel_event)

    def initial_state(self) -> list[dict[str, Any]]:
        return []

    def reduce(
        self,
        state: list[dict[str, Any]],
        msg: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        # Each work-bearing message is the (idx, result, basename) tuple
        # the worker put on the queue. Drop ``None`` results (process_one
        # returns None for unscorable images) — they aren't useful in the
        # accumulator, but we still emit a progress tick for them.
        if isinstance(msg, tuple) and len(msg) == 3:
            idx, result, basename = msg
            if result is not None:
                state.append(result)
            progress = {
                "type": "progress",
                "current": idx + 1,
                "total": self.total,
                "filepath": basename,
            }
            return state, progress
        # Anything else (shouldn't happen — fatal_error is handled by the
        # runner; SENTINEL terminates the loop) is logged and ignored.
        log.warning("Scoring phase: unexpected message shape: %r", msg)
        return state, None


def run_scoring_subprocess(
    images: list[str],
    config: dict[str, Any],
    db_path: str,
    cancel_event: Any = None,
    progress_callback: Any | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    """Run scoring in a subprocess. Returns (results, child_pid).

    The child process loads all ML models, scores every image, sends
    results through a queue, then exits. All model memory is freed by
    the OS when the child terminates.

    P2 — the queue/sentinel/drain/join machinery now lives in
    :class:`bpp.utils.subprocess_runner.BoundedSubprocessRunner`. This
    function is a thin orchestrator: snapshot config, build a phase,
    delegate.

    Args:
        images: list of image file paths to score
        config: analysis config dict (max_long_side, model toggles, etc.)
        db_path: path to analysis cache DB
        cancel_event: P1 contract — accept any of: ProcessCancellation,
            ThreadCancellation, raw multiprocessing.Event, raw
            threading.Event, or None. The runner normalizes the shape.
        progress_callback: optional callable(msg_dict) for progress updates

    Returns:
        (results, child_pid) — results is list of score dicts, child_pid
        is the PID of the terminated child process (for memory verification).
    """
    if not images:
        return [], None

    # Snapshot Config → dict before pickling for the child. Same
    # mappingproxy gotcha as run_face_extraction_subprocess.
    config_snapshot = _snapshot_config(config)

    phase = ScoringPhase(config_snapshot=config_snapshot, db_path=db_path, total=len(images))
    # daemon=True for scoring — no inner ProcessPool, so daemonic is fine
    # (Python forbids daemonic processes from having children but scoring
    # doesn't fork further).
    runner: BoundedSubprocessRunner = BoundedSubprocessRunner(phase, daemon=True)
    return runner.run(images, cancel_event=cancel_event, progress_callback=progress_callback)
