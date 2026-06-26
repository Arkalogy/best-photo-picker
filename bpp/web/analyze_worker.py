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

When P2 lands BoundedSubprocessRunner, it MUST preserve this ordering
in any spawn target — see refactor-plan.md P2 risk callout.
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

from bpp.config import DEFAULTS  # noqa: E402
from bpp.db.photos import get_photo_id_map_by_paths  # noqa: E402
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
from bpp.web.analyze_scoring import run_scoring_subprocess  # noqa: E402
from bpp.web.analyze_subprocess import _write_analysis_json  # noqa: E402
from bpp.web.base_worker import BackgroundWorker  # noqa: E402

log = get_logger(__name__)

_DEFAULT_FACE_CONF = DEFAULTS["face_detection_confidence"]


class AnalyzeWorker(BackgroundWorker):
    """Runs image analysis in a background thread, reporting progress via a queue."""

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

        # M15: surface a "scanning" phase so a 50k-photo recursive scan
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

        images = scan_images(
            input_dir,
            extensions=extensions,
            follow_symlinks=config.get("follow_symlinks", False),
            recursive=recursive,
            on_progress=_on_scan_progress,
        )

        # Pre-scan: pull phash-confirmed Live Photo sidecars out of the
        # to-score list so the expensive scoring/face/CLIP passes never run
        # on them (they're near-identical motion frames nobody composed —
        # roughly half of an iCloud export). Confirmation is by perceptual
        # hash, never filename alone, so a genuinely distinct beach_2.jpg is
        # never dropped. The skipped sidecars are stored as minimal rows
        # (below) carrying the hashes computed here; the phash backfill
        # thread then tags + links them via detect_and_link.
        sidecar_records: list[dict[str, Any]] = []
        try:
            from bpp.db.live_photo import split_scan_for_confirmed_sidecars

            def _on_sidecar_progress(done: int, of: int) -> None:
                self._emit(
                    {
                        "type": "status",
                        "message": f"Checking Live Photo duplicates {done}/{of}…",
                    }
                )

            # Parents the DB says are gone (soft-deleted / hidden / sidecars
            # themselves) can never anchor a hidden child — detect_and_link
            # refuses to tag against them, so a candidate skipped from
            # scoring here would linger as a visible 0% ghost. (Real case: a
            # duplicate review keeps the `_1` copy and trashes the parent —
            # the kept copy is the user's photo now and must be scored.)
            dead_parents: set[str] = set()
            _pp_db = os.path.join(workdir, "photopicker.db")
            if os.path.exists(_pp_db):
                try:
                    from bpp.db.connection import init_db as _init_db

                    _ro = _init_db(_pp_db)
                    dead_parents = {
                        r[0]
                        for r in _ro.execute(
                            "SELECT filepath FROM photos WHERE deleted_at IS NOT NULL "
                            "OR hidden_at IS NOT NULL OR is_live_photo_sidecar = 1"
                        )
                    }
                except Exception:
                    log.warning(
                        "Could not load dead-parent set for sidecar pre-scan; "
                        "pre-scan will use filename+phash only",
                        exc_info=True,
                    )

            images, sidecar_records = split_scan_for_confirmed_sidecars(
                images,
                on_progress=_on_sidecar_progress,
                parent_alive=(lambda p: p not in dead_parents) if dead_parents else None,
            )
            if sidecar_records:
                log.info(
                    "Pre-scan skipped %d phash-confirmed Live Photo sidecar(s) from scoring",
                    len(sidecar_records),
                )
                self._emit(
                    {
                        "type": "status",
                        "message": (
                            f"Skipping {len(sidecar_records)} Live Photo duplicate(s) from scoring"
                        ),
                    }
                )
        except Exception:
            # Pre-scan is an optimization, never a gate. On any failure fall
            # back to scoring everything — the phash thread still tags sidecars
            # afterward, so correctness holds; we just don't save the compute.
            log.warning("Live Photo sidecar pre-scan failed; scoring all images", exc_info=True)
            sidecar_records = []

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
        from bpp.web.analyze_model_preflight import preflight_models

        preflight_models(self._emit)

        # ── Phase 1: Scoring in subprocess ──
        # All ML models load in the child process. When the child exits,
        # the OS reclaims all model memory — guaranteed, no allocator fragmentation.
        from bpp.db.albums import sync_all_photos_album
        from bpp.db.connection import init_db
        from bpp.db.photos import bulk_upsert_photos

        pp_db_path = os.path.join(workdir, "photopicker.db")
        pp_conn = init_db(pp_db_path)

        if self._cancelled.is_set():
            self._emit({"type": "cancelled"})
            return

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
        # P1: pass our threading-side cancel directly. The runner builds
        # a ProcessCancellation + bridge so the spawn child sees it
        # within ~100 ms. Before P1 we created a stub mp.Event and then
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

        # Write final results with normalized scores to DB
        faces_found = 0
        face_clusters = 0
        clip_computed = 0
        try:
            conn = pp_conn
            bulk_upsert_photos(conn, valid)

            # Store the phash-confirmed Live Photo sidecars the pre-scan
            # pulled out of scoring. They land as minimal rows WITH the phash
            # the pre-scan computed (is_live_photo_sidecar still 0 here). They
            # do NOT get tagged here: bulk_upsert_photos NULLs the PARENT
            # phash, so a require_phash_match tag would fail until the phash
            # thread recomputes the parent hashes. That thread runs
            # detect_and_link(require_phash_match=True) once both sides have
            # hashes — see precompute_phashes in state_init.py — which is the
            # single place sidecar linking lives.
            #
            # LOAD-BEARING INVARIANT (do not break): every stored sidecar row
            # is un-tagged here and is hidden ONLY by that phash thread. The
            # thread early-exits when nothing needs hashing. So a stored
            # sidecar MUST be accompanied by a parent that needs a hash, or the
            # row never gets tagged and lingers as a visible, score-0 "ghost"
            # photo. This holds because `split_scan_for_confirmed_sidecars`
            # only confirms a sidecar when its parent is in the SAME scan batch
            # — that parent is in `valid`, bulk_upsert NULLs its phash, so the
            # phash thread always runs and always tags. The guarantee is
            # enforced by test_live_photo.py::...every_record_has_parent_in_to_score;
            # if you change the pre-scan to emit a sidecar whose parent isn't
            # scored, restore the tagging on this path too.
            if sidecar_records:
                try:
                    bulk_upsert_photos(conn, sidecar_records)
                    log.info(
                        "Stored %d pre-scan Live Photo sidecar row(s) (unscored)",
                        len(sidecar_records),
                    )
                except Exception:
                    log.warning("Failed to store pre-scan sidecar rows", exc_info=True)

            sync_all_photos_album(conn)

            # Save pet detections (batched)
            from bpp.db.pets import (
                assign_pet_clusters,
                bulk_upsert_pet_detections,
            )

            photo_map = get_photo_id_map_by_paths(conn, [i["filepath"] for i in valid])
            pet_items: list[tuple[int, list]] = []
            for item in valid:
                dets = item.get("pet_detections", [])
                pid = photo_map.get(item["filepath"])
                if dets and pid is not None:
                    pet_items.append((pid, dets))
            pet_count = bulk_upsert_pet_detections(conn, pet_items)
            if pet_count > 0:
                try:
                    assign_pet_clusters(conn)
                except Exception:
                    log.warning("Pet clustering failed", exc_info=True)
                    self._emit(
                        {
                            "type": "warning",
                            "message": "Pet clustering failed"
                            " — detections saved but may be mismatched",
                        }
                    )
                log.info("Saved %d pet detections to DB", pet_count)

            log.info("Wrote %d photos to DB", len(valid))

            # Plugin event bus: post-analyze fires after the DB write
            # committed but before downstream phases (faces / CLIP) start.
            # Plugin failures are swallowed and logged — see event_hooks.
            from bpp.db.event_hooks import dispatch_post_analyze

            dispatch_post_analyze(conn, valid)

            # Update the in-memory analysis cache now so concurrent
            # readers see fresh rows. The derived-recovery pipeline
            # (hashes -> sidecars -> clustering -> refresh) is kicked
            # AFTER the CLIP phase below — Moments cluster over CLIP
            # embeddings, so clustering before CLIP finishes "recovers"
            # to garbage (the wipe-incident race, S4 2026-06-12).
            from bpp.web.state import get_ctx_or_none

            ctx = get_ctx_or_none()
            if ctx is not None:
                with ctx.lock:
                    ctx.state["analysis"] = valid

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
                faces_found, face_clusters = run_face_phase(
                    conn, valid, config, emit=self._emit, cancel_event=self._cancelled
                )

            # --- Phase 3: CLIP embedding extraction ---
            if config.get("model_clip", True) and not self._cancelled.is_set():
                self._emit(
                    {
                        "type": "phase",
                        "phase": "clip",
                        "label": "Indexing for search",
                        "step": 5,
                        "of": 5,
                    }
                )
                clip_computed = run_clip_phase(
                    conn, valid, emit=self._emit, cancel_event=self._cancelled
                )

            # Derived-state recovery — one ordered background job
            # (hashes -> sidecar tags -> dup clusters -> Moments ->
            # smart-album refresh). Runs after faces + CLIP so every
            # input the clustering steps read is fresh.
            if ctx is not None and not self._cancelled.is_set():
                ctx.precompute_phashes(valid)

        except Exception:
            log.warning("Failed to write to DB", exc_info=True)

        # Sensitive-photo alert + in-worker finalize — extracted to
        # analyze_finalize.py (LOC gate). Finalize runs HERE, not in
        # the SSE generator's on_done, so a headless analyze (no
        # progress-stream consumer) still invalidates the analysis
        # cache and reloads CLIP (observed stale 2026-06-12).
        from bpp.web.analyze_finalize import count_sensitive_alert, finalize_in_worker

        sensitive_flagged, sensitive_new = count_sensitive_alert(pp_conn)

        self.results = valid
        finalize_in_worker(clip_computed)

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
            }
        )
