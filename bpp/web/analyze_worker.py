"""Background analysis thread with progress queue for SSE streaming.

Three phases run sequentially within the same worker thread:
  Phase 1: Image scoring (blur, exposure, face detection, composition, etc.)
  Phase 2: Face embedding extraction + clustering (if face_recognition available)
  Phase 3: CLIP embedding extraction (if model_clip enabled, default on)

──────────────────────────────────────────────────────────────────────
ARCHITECTURE INVARIANT — NATIVE THREAD-POOL PINNING ORDER
──────────────────────────────────────────────────────────────────────
The env-var setdefault block below (OMP_NUM_THREADS etc.) MUST stay
BEFORE every subsequent import. C-extensions (cv2/numpy/onnxruntime/
mediapipe/dlib) cache their thread-pool size at first-import time —
setting these vars after the import is a silent no-op.

A future refactor that puts a "convenience" import above the env-var
block, or that calls into another module that imports cv2 transitively
before the pin, will re-introduce a SIGSEGV at workers >= 2 on a
large library. The signal isn't a Python exception — it's a hard
process kill, often near the END of a long run, so it looks like
"analyze just stopped working." Tests don't catch it because the unit
tests use workers=1.

Same invariant in: bpp/web/face_worker.py (top of file).
Test that asserts the ordering:
tests/test_face_thread_safety.py::test_analyze_worker_pins_native_thread_pools.

``BoundedSubprocessRunner`` MUST preserve this ordering in any spawn
target — see docs/adr/0001-subprocess-runner.md.
──────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

# Pin native-library thread pools to 1 BEFORE numpy / cv2 /
# onnxruntime / mediapipe / dlib import. These libraries spawn
# their own internal threads (OpenMP, OpenBLAS, MKL, ONNX Runtime
# intra-op) sized at the CPU count by default. The face-extraction
# pipeline runs a Python ThreadPoolExecutor on top: each Python
# worker thread invokes a model that itself spawns N native threads.
# At workers >= 2 on an 8-core box, that's 16+ threads competing
# for 8 cores, and the resulting allocator-state race in OpenBLAS /
# ONNX Runtime SIGSEGV's the entire child process — silently, with
# no Python traceback.
#
# The cure is one thread per worker. Bisected on the demo lib:
# `OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1`
# turns a deterministic SIGSEGV at 50 photos / workers=2 into a
# clean exit-code-0 run in ~5s. We use `setdefault` so an operator
# who knows their stack is fine can override (e.g. a user with a
# 32-core workstation might raise OMP_NUM_THREADS=4 deliberately).
#
# multiprocessing.spawn re-imports this module in the child before
# calling the worker function, so setting env vars at module-import
# time catches both parent and child paths. Setting them inside the
# worker function body would be too late — by then the C-extension
# imports have already cached the thread-pool size.
import os as _os

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    _os.environ.setdefault(_var, "1")

# All subsequent imports MUST stay below the env-var pinning above —
# C-extension imports (numpy / cv2 / onnxruntime / mediapipe) cache
# the thread-pool size at load time, so setting these later is a
# no-op. E402 is silenced per-import (not file-level) so any new
# top-level statement that doesn't need this ordering still gets
# the lint check.
import os  # noqa: E402
from typing import Any  # noqa: E402

from bpp.config import DEFAULTS, cfg_get  # noqa: E402
from bpp.io_scan import scan_images  # noqa: E402
from bpp.scoring.aggregate import (  # noqa: E402
    DB_NAME,
    compute_aggregate,
    init_analysis_db,
    normalize_blur_scores,
)
from bpp.utils.logging import get_logger  # noqa: E402
from bpp.web.analyze_archive import extract_archive_into_workdir  # noqa: E402
from bpp.web.analyze_phases import run_clip_phase, run_face_phase  # noqa: E402
from bpp.web.analyze_run_steps import (  # noqa: E402
    prescan_live_photo_sidecars,
    store_scoring_results,
)
from bpp.web.analyze_scoring import run_scoring_subprocess  # noqa: E402
from bpp.web.analyze_subprocess import _write_analysis_json  # noqa: E402
from bpp.web.base_worker import BackgroundWorker  # noqa: E402

log = get_logger(__name__)

_DEFAULT_FACE_CONF = DEFAULTS["face_detection_confidence"]


class AnalyzeWorker(BackgroundWorker):
    """Runs image analysis in a background thread, reporting progress via a queue."""

    # Scoring loads face, nudity, pet and CLIP models.
    loads_installable_packages = True

    _worker_name = "Analysis"

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, Any]] | None = None

    def start(
        self,
        input_dir: str,
        workdir: str,
        config: dict[str, Any],
        extensions: list[str],
        recursive: bool = False,
    ) -> bool:
        """Start background analysis. Returns False if already running."""
        self.results = None
        return self._start_thread(input_dir, workdir, config, extensions, recursive)

    def _run(
        self,
        input_dir: str,
        workdir: str,
        config: dict[str, Any],
        extensions: list[str],
        recursive: bool = False,
    ) -> None:
        # Extract archive if input_dir is actually a file
        if os.path.isfile(input_dir):
            extract_dir = extract_archive_into_workdir(input_dir, workdir, self._emit)
            if extract_dir is None:
                return
            input_dir = extract_dir
            recursive = True

        images = self._scan(input_dir, config, extensions, recursive)
        images, sidecar_records = prescan_live_photo_sidecars(images, workdir, self._emit)

        total = len(images)
        self._emit({"type": "start", "total": total})

        if total == 0 and not sidecar_records:
            self._emit({"type": "done", "total": 0, "processed": 0})
            return

        db_path = os.path.join(workdir, DB_NAME)
        init_analysis_db(db_path)

        # Pre-download ML models so the user sees progress (not silent stalls).
        # Body lives in analyze_model_preflight to keep this file under
        # the 500-LOC cap.
        from bpp.web.analyze_model_preflight import preflight_run_config

        config = preflight_run_config(self._emit, config)

        from bpp.db.connection import init_db
        from bpp.db.photos import bulk_upsert_photos

        pp_db_path = os.path.join(workdir, "photopicker.db")
        pp_conn = init_db(pp_db_path)

        if self._cancelled.is_set():
            self._emit({"type": "cancelled"})
            return

        valid = self._score(images, config, db_path)

        if self._cancelled.is_set():
            self._emit({"type": "cancelled"})
            return

        # Flush all results to DB after subprocess completes
        if valid:
            try:
                bulk_upsert_photos(pp_conn, valid)
                self._emit({"type": "batch_ready", "count": len(valid)})
            except Exception:
                log.warning("DB flush after scoring failed", exc_info=True)
                self._emit({"type": "warning", "message": "DB write failed"})

        normalize_blur_scores(valid)
        compute_aggregate(valid, config)

        # Save analysis.json (legacy, kept for backwards compat)
        from bpp.utils.retry import retry_io

        results_path = os.path.join(workdir, "analysis.json")
        retry_io(_write_analysis_json, results_path, valid, label="write_analysis_json")

        # Write final results with normalized scores to DB. The counts
        # are filled in phase by phase, so a failure in a later phase
        # keeps what the earlier ones reported.
        counts = {"faces_found": 0, "face_clusters": 0, "clip_computed": 0}
        db_write_failed = False
        try:
            store_scoring_results(pp_conn, valid, sidecar_records, self._emit)
            self._downstream_phases(pp_conn, valid, config, counts)
        except Exception:
            db_write_failed = True
            log.warning("Failed to write to DB", exc_info=True)
            self._emit(
                {
                    "type": "warning",
                    "message": "Scores were computed but couldn't all be saved to the "
                    "library; see server.log, then run Analyze again",
                }
            )
        faces_found = counts["faces_found"]
        face_clusters = counts["face_clusters"]
        clip_computed = counts["clip_computed"]

        # Sensitive-photo alert + in-worker finalize — extracted to
        # analyze_finalize.py (LOC gate). Finalize runs HERE, not in
        # the SSE generator's on_done, so a headless analyze (no
        # progress-stream consumer) still invalidates the analysis
        # cache and reloads CLIP (observed stale 2026-06-12).
        from bpp.web.analyze_finalize import count_sensitive_alert, finalize_in_worker

        sensitive_flagged, sensitive_new = count_sensitive_alert(pp_conn)

        self.results = valid
        finalize_in_worker(clip_computed, ctx=self.ctx)

        self._emit(
            {
                "type": "done",
                "total": total,
                "processed": len(valid),
                "faces_found": faces_found,
                "face_clusters": face_clusters,
                "clip_computed": clip_computed,
                "sidecars_skipped": len(sidecar_records),
                "sensitive_flagged": sensitive_flagged,
                "sensitive_new": sensitive_new,
                "db_write_failed": db_write_failed,
            }
        )

    def _scan(
        self, input_dir: str, config: dict[str, Any], extensions: list[str], recursive: bool
    ) -> list[str]:
        """Step 1: find the images, with visible progress."""
        # Surface a "scanning" phase so a 50k-photo recursive scan
        # doesn't look like a black-box stall to the user. Progress
        # callback ticks every ~500 files inspected.
        self._emit(
            {
                "type": "phase",
                "phase": "scanning",
                "label": "Scanning files",
                "step": 1,
                "of": 5,
            }
        )

        def _on_scan_progress(scanned: int, matched: int) -> None:
            self._emit(
                {
                    "type": "scan_progress",
                    "scanned": scanned,
                    "matched": matched,
                }
            )

        return scan_images(
            input_dir,
            extensions=extensions,
            follow_symlinks=cfg_get(config, "follow_symlinks"),
            recursive=recursive,
            on_progress=_on_scan_progress,
        )

    def _score(
        self, images: list[str], config: dict[str, Any], db_path: str
    ) -> list[dict[str, Any]]:
        """Phase 1: scoring in a subprocess.

        All ML models load in the child process. When the child exits,
        the OS reclaims all model memory — guaranteed, no allocator
        fragmentation.
        """
        total = len(images)
        self._emit(
            {
                "type": "phase",
                "phase": "scoring",
                "label": "Scoring images",
                "step": 3,
                "of": 5,
            }
        )
        log.info("Starting scoring subprocess for %d images", total)
        # Pass our threading-side cancel directly. The runner builds
        # a ProcessCancellation + bridge so the spawn child sees it
        # within ~100 ms. Previously we created a stub mp.Event and then
        # manually checked + set it AFTER scoring returned — which meant
        # cancel-during-scoring did nothing.
        valid, child_pid = run_scoring_subprocess(
            images=images,
            config=config,
            db_path=db_path,
            cancel_event=self._cancelled,
            progress_callback=lambda msg: self._emit(msg),
        )
        log.info(
            "Scoring subprocess done (pid=%s): %d results from %d images",
            child_pid,
            len(valid),
            total,
        )
        return valid

    def _downstream_phases(
        self,
        conn: Any,
        valid: list[dict[str, Any]],
        config: dict[str, Any],
        counts: dict[str, int],
    ) -> None:
        """Phases 2 and 3 (faces, CLIP), then the derived-state recovery job.

        Fills ``counts`` (``faces_found`` / ``face_clusters`` /
        ``clip_computed``) as each phase completes.
        """

        # Update the in-memory analysis cache now so concurrent
        # readers see fresh rows. The derived-recovery pipeline
        # (hashes -> sidecars -> clustering -> refresh) is kicked
        # AFTER the CLIP phase below — Moments cluster over CLIP
        # embeddings, so clustering before CLIP finishes "recovers"
        # to garbage (the wipe-incident race, S4 2026-06-12).
        # The worker thread has no request, so the request-bound
        # ``get_ctx`` would find nothing; ``self.ctx`` is the app state
        # the pool handed this worker.
        # The scoring dicts carry no row ids; publishing them would make
        # the recompute endpoint fail for the whole faces + CLIP window.
        # Drop the cache instead: readers reload the stored rows (and the
        # thumbnail map picks up the new photos).
        ctx = self.ctx
        if ctx is not None:
            invalidate = getattr(ctx, "invalidate_analysis", None)
            if invalidate is not None:
                invalidate()
            else:  # a bare test double
                with ctx.lock:
                    ctx.state["analysis"] = None

        # --- Phase 2: Face extraction + clustering ---
        if config.get("face_recognition_available") and not self._cancelled.is_set():
            self._emit(
                {
                    "type": "phase",
                    "phase": "faces",
                    "label": "Extracting faces",
                    "step": 4,
                    "of": 5,
                }
            )
            try:
                counts["faces_found"], counts["face_clusters"] = run_face_phase(
                    conn, valid, config, emit=self._emit, cancel_event=self._cancelled
                )
            finally:
                if ctx is not None:
                    ctx.invalidate_face_cluster_map()

        # --- Phase 3: CLIP embedding extraction ---
        if cfg_get(config, "model_clip") and not self._cancelled.is_set():
            self._emit(
                {
                    "type": "phase",
                    "phase": "clip",
                    "label": "Indexing for search",
                    "step": 5,
                    "of": 5,
                }
            )
            counts["clip_computed"] = run_clip_phase(
                conn, valid, emit=self._emit, cancel_event=self._cancelled
            )

        # Derived-state recovery — one ordered background job
        # (hashes -> sidecar tags -> dup clusters -> Moments ->
        # smart-album refresh). Runs after faces + CLIP so every
        # input the clustering steps read is fresh.
        if ctx is not None and not self._cancelled.is_set():
            ctx.precompute_phashes(valid)
