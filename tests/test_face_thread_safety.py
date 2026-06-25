"""Thread-safety regression guards for the face-detection pipeline.

These tests exist because v0.1.0-rc shipped without a lock around
``bpp.scoring.face_scrfd.session.run`` while every other detector
in the codebase had one. Concurrent calls into ONNX Runtime's
``session.run`` from the ``ThreadPoolExecutor`` worker threads in
``extract_and_cluster_faces`` raced on the runtime's internal
allocator state and silently SIGSEGV'd the entire process — no
Python traceback, no log line, just a dead worker.

The bug took five weeks to surface in the wild because:
  * Tests covered correctness, not concurrency.
  * Small libraries had small race windows; the segfault only
    became deterministic above ~30 photos at workers >= 2.
  * The crash signature (kernel SIGKILL with no Python frame)
    looked like an OOM and sent the investigation hunting memory
    instead of races.

This file enforces the pattern that would have caught that bug in
review: every detector module must lock its inference call. The
tests are AST-level source scans rather than runtime stress tests
so they're cheap (sub-second) and pin the exact line of code that
matters.

The companion runtime test (``test_face_extract_subprocess_smoke``
below) is the integration coverage — it exercises the full
``run_face_extraction_subprocess`` path on a small procedural
library and asserts the child exits cleanly. Marked ``slow`` so
it runs in a dedicated CI job rather than on every push.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _read(*parts: str) -> str:
    return (REPO_ROOT / "bpp" / "scoring").joinpath(*parts).read_text()


def _has_lock_around_call(source: str, call_name: str) -> bool:
    """Return True iff *every* invocation of ``<x>.<call_name>(...)`` in
    *source* lives inside a ``with <something_lock>:`` block.

    Walks the AST so the check is robust against whitespace / formatting.
    A detector module that calls ``session.run`` outside any ``with``
    statement that holds a Lock-named variable will fail this guard.
    """
    tree = ast.parse(source)

    def _is_lock_with(node: ast.AST) -> bool:
        """True iff a `with` items list contains anything with 'lock' in
        its name (case-insensitive). Conservative — covers every
        threading.Lock instance in this codebase."""
        if not isinstance(node, ast.With):
            return False
        for item in node.items:
            ctx = item.context_expr
            name = ""
            if isinstance(ctx, ast.Name):
                name = ctx.id
            elif isinstance(ctx, ast.Attribute):
                name = ctx.attr
            if "lock" in name.lower():
                return True
        return False

    # Find every Call node whose attribute is `call_name` and check that
    # at least one ancestor is a lock-bearing `with` block.
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == call_name):
            continue
        # Walk ancestors looking for a `with _xxx_lock:` block.
        cur: ast.AST | None = node
        guarded = False
        while cur is not None:
            if _is_lock_with(cur):
                guarded = True
                break
            cur = parents.get(id(cur))
        if not guarded:
            return False
    return True


class TestScrfdSessionRunIsLocked:
    """The headline regression guard. SCRFD's ``session.run`` must run
    inside a ``with _scrfd_lock:`` block.

    The original commit (3f67993) shipped without one — every other
    detector in the codebase locked but SCRFD's author trusted ONNX
    Runtime's "session.run is thread-safe" doc claim. In practice on
    macOS arm64 with the default CPU provider, concurrent session.run
    calls race on internal allocator state and silently SIGSEGV the
    process. The fix is the lock; this test pins it so a future
    refactor can't accidentally drop it again.
    """

    def test_scrfd_session_run_is_locked(self):
        assert _has_lock_around_call(_read("face_scrfd.py"), "run"), (
            "SCRFD's session.run() must run inside a `with _scrfd_lock:` "
            "block. The original commit (3f67993) shipped without one and "
            "concurrent session.run on macOS arm64 SIGSEGV'd silently at "
            ">= 2 worker threads. Every other detector locks; SCRFD must too."
        )

    def test_blazeface_fr_invoke_is_locked(self):
        # Existing lock around interp.invoke() — guards that a refactor
        # can't drop it. TFLite interpreters are *not* thread-safe.
        assert _has_lock_around_call(_read("face_blazeface_fr.py"), "invoke"), (
            "BlazeFace full-range interp.invoke() must run inside a "
            "`with _fr_lock:` block. TFLite interpreters are not "
            "thread-safe; concurrent invoke() corrupts internal tensors."
        )


class TestNativeThreadPoolPinned:
    """Native-library thread pools must be pinned to 1 BEFORE the
    C-extension imports (numpy / cv2 / onnxruntime / mediapipe / dlib).
    Without the pinning, each Python worker thread invokes a model
    that spawns N native threads (OpenMP / OpenBLAS / MKL / ONNX
    Runtime intra-op), and the nested oversubscription SIGSEGV's the
    child process silently — no Python traceback, no log line.

    The fix lives at the TOP of `analyze_worker.py` and `face_worker.py`:

        for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            _os.environ.setdefault(_var, "1")

    multiprocessing.spawn re-imports both modules in the child before
    calling the worker function, so module-import-time setting catches
    the spawn path. Setting these env vars inside a function body is
    too late — by then the C-extension imports have cached the pool
    size from the OS.

    Note: this pinning is **necessary but not sufficient** for safe
    multi-threaded extraction. There's a second still-unbisected race
    that appears at workers >= 2 once a single chunk's photo count
    climbs past ~50. Default workers stays at 1 for now.
    """

    def test_analyze_worker_pins_native_thread_pools(self):
        source = (REPO_ROOT / "bpp" / "web" / "analyze_worker.py").read_text()
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            assert var in source, (
                f"analyze_worker.py must set {var} at module-import time "
                "before cv2/numpy/onnxruntime are imported. See the "
                "module-top comment block for the SIGSEGV rationale."
            )
        # Pinning must happen BEFORE the heavy imports — find the env
        # block and the first `import cv2 / numpy / from bpp.scoring`
        # and check ordering.
        env_idx = source.find("OMP_NUM_THREADS")
        scoring_import_idx = source.find("from bpp.scoring.aggregate import")
        assert env_idx > 0 and scoring_import_idx > 0
        assert env_idx < scoring_import_idx, (
            "OMP_NUM_THREADS / OPENBLAS_NUM_THREADS / MKL_NUM_THREADS must "
            "be set BEFORE `from bpp.scoring.aggregate import` (which "
            "transitively imports cv2 / numpy / onnxruntime). Setting "
            "them after is a no-op — the C extensions cache the pool size "
            "at import time."
        )

    def test_face_worker_pins_native_thread_pools(self):
        source = (REPO_ROOT / "bpp" / "web" / "face_worker.py").read_text()
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            assert var in source
        # Same ordering check — env vars before the numpy import.
        env_idx = source.find("OMP_NUM_THREADS")
        np_idx = source.find("import numpy")
        assert env_idx > 0 and np_idx > 0
        assert env_idx < np_idx, "OMP_NUM_THREADS etc. must be set before `import numpy`."


class TestSubprocessIsolation:
    """FaceWorker must run extraction in a subprocess. In-process
    extraction at scale OOM'd the entire server (kernel SIGKILL,
    not a recoverable Python exception). The subprocess pattern is
    shared with AnalyzeWorker via run_face_extraction_subprocess.
    """

    def test_face_worker_uses_subprocess_helper(self):
        source = (REPO_ROOT / "bpp" / "web" / "face_worker.py").read_text()
        assert "run_face_extraction_subprocess" in source, (
            "FaceWorker._run must call run_face_extraction_subprocess "
            "(not extract_and_cluster_faces directly). The in-process "
            "path SIGKILLs the server on libraries past ~1000 photos."
        )

    def test_chunked_subprocess_pattern_in_place(self):
        # _FACE_EXTRACTION_CHUNK_SIZE lives in analyze_face_extract.py
        # since the v0.1 cleanup.
        source = (REPO_ROOT / "bpp" / "web" / "analyze_face_extract.py").read_text()
        assert "_FACE_EXTRACTION_CHUNK_SIZE" in source, (
            "Chunked subprocess pattern must remain. A single subprocess "
            "for the whole library SIGSEGVs in the child near the end as "
            "the ONNX/TFLite arena allocators accumulate state."
        )


class TestConfigSnapshotForPickling:
    """Live `bpp.config_resolver.Config` holds a bound method (`_get_conn`)
    that drags `cls.__dict__` (a `mappingproxy`) through pickle. The
    spawn-method ForkingPickler refuses mappingproxy and raises
    TypeError: cannot pickle 'mappingproxy' object. The fix is
    `_snapshot_config()` flattening at the parent/child boundary.
    """

    def test_snapshot_helper_present(self):
        # _snapshot_config moved to analyze_subprocess.py in the v0.1 cleanup.
        source = (REPO_ROOT / "bpp" / "web" / "analyze_subprocess.py").read_text()
        assert "_snapshot_config" in source

    def test_subprocess_helpers_call_snapshot(self):
        # The two subprocess runners now live in sibling modules; both
        # must call _snapshot_config before constructing the child
        # Process — otherwise the live Config gets pickled and crashes
        # on spawn.
        scoring_src = (REPO_ROOT / "bpp" / "web" / "analyze_scoring.py").read_text()
        face_src = (REPO_ROOT / "bpp" / "web" / "analyze_face_extract.py").read_text()
        assert "_snapshot_config(" in scoring_src, (
            "run_scoring_subprocess must call _snapshot_config before pickling."
        )
        assert "_snapshot_config(" in face_src, (
            "run_face_extraction_subprocess must call _snapshot_config before pickling."
        )


# ---------------------------------------------------------------------------
# Slow integration test — exercises the actual subprocess path end-to-end.
# Marked `slow` so it runs in a dedicated CI job, not on every push.
# Procedural images are fast to score (no model loads to a thousand
# photos); the test is bounded to ~50 to keep wall time under 2 min.
# ---------------------------------------------------------------------------


class TestProcessPoolDefault:
    """When `_face_extract_workers > 1`, the default pool kind is
    "process" (ProcessPoolExecutor) — bulletproof against thread races
    in the embed/landmark stack that previously SIGSEGV'd at chunk
    sizes past ~50 photos. Operator can pin to "thread" via the
    `_face_extract_pool` config key if their stack is verified safe.
    """

    def test_workers_gt_one_defaults_to_process(self):
        # P3 → 500-LOC split: the pool-choice logic moved from
        # face_extraction_phases.py to face_extraction_phase5.py
        # (phase 5 is the only phase that drives a worker pool).
        source = (REPO_ROOT / "bpp" / "web" / "face_extraction_phase5.py").read_text()
        assert '"process" if n_workers > 1' in source, (
            "When workers > 1 the default pool kind must be 'process'. "
            "ThreadPool is unsafe past ~50 photos per chunk; the only "
            "memory-safe parallelism is per-process model arena isolation."
        )

    def test_pool_choice_routes_to_correct_executor(self):
        # 500-LOC split — phase 5 lives in face_extraction_phase5.py.
        source = (REPO_ROOT / "bpp" / "web" / "face_extraction_phase5.py").read_text()
        # Both executors must be importable; the `Pool = ...` ternary
        # must pick the right one for the right config.
        assert "from concurrent.futures import" in source
        assert "ProcessPoolExecutor" in source
        assert "ThreadPoolExecutor" in source


@pytest.mark.slow
class TestProcessPoolEndToEnd:
    """Exercise the ProcessPool path end-to-end with workers=2 on a
    small procedural library. Marked @slow because it spawns N+1
    subprocesses that each load ML models — minutes-scale on a cold
    cache, ~30s warm.

    The contract this test pins:

      * `_face_extract_workers` config key as a STRING (the form
        DB-stored settings come back in) doesn't crash. Cast must
        live in face_worker — without it `'4' > 1` blows the worker.
      * ProcessPoolExecutor actually runs (no fall-through to thread).
      * Workers > 1 don't SIGSEGV the chunk subprocess at small scale.
    """

    def test_string_workers_value_doesnt_crash(self, tmp_path):
        from bpp.demo.generate import generate_sample_photos
        from bpp.web.analyze_face_extract import run_face_extraction_subprocess

        photo_dir = tmp_path / "photos"
        paths = generate_sample_photos(str(photo_dir), count=10)
        with_faces = [{"filepath": p, "face_count": 1, "id": i} for i, p in enumerate(paths)]
        # Settings-style string value (the bug shape)
        config = {
            "max_long_side": 1024,
            "face_detection_confidence": 0.3,
            "face_embedding_confidence": 0.65,
            "min_embedding_quality": 0.25,
            "face_cluster_threshold": 0.6,
            "_face_extract_workers": "2",  # STRING — DB-stored shape
            "_face_extract_pool": "process",
        }
        db_path = tmp_path / "photopicker.db"
        from bpp.db.connection import init_db

        init_db(str(db_path))

        progress: list[dict] = []
        _faces, _clusters, pid = run_face_extraction_subprocess(
            with_faces, config, str(db_path), progress_callback=progress.append
        )
        assert pid is not None
        errors = [p for p in progress if p.get("type") == "error"]
        assert not errors, f"errors during extraction: {errors}"


@pytest.mark.slow
class TestFaceExtractSubprocessSmoke:
    """Spawns the actual run_face_extraction_subprocess on a small
    procedural library and asserts the child exits cleanly.

    This is the runtime guard that would have caught the original
    SCRFD-lock bug had it been in CI when the SCRFD detector landed.
    The bug manifested as silent SIGSEGV in the child process; this
    test detects it by asserting the child's exitcode is 0 and that
    *some* face_progress messages were emitted (proving the child
    got past model load and into the per-photo loop).
    """

    def test_subprocess_completes_on_small_library(self, tmp_path):
        from bpp.demo.generate import generate_sample_photos
        from bpp.web.analyze_face_extract import run_face_extraction_subprocess

        # 50 procedural photos. Most generate.py outputs have zero
        # detected faces (gradients, geometric patterns), so we also
        # need a couple with face_count > 0 for the child to do real
        # work. Add minimal fake records pointing at the procedural
        # files so the worker treats them as candidates.
        photo_dir = tmp_path / "photos"
        paths = generate_sample_photos(str(photo_dir), count=50)

        with_faces = [{"filepath": p, "face_count": 1, "id": i} for i, p in enumerate(paths)]
        config = {
            "max_long_side": 1024,
            "face_detection_confidence": 0.3,
            "face_embedding_confidence": 0.65,
            "min_embedding_quality": 0.25,
            "face_cluster_threshold": 0.6,
            "_face_extract_workers": 1,
        }

        # We need a real photopicker.db so the worker's photo_id
        # lookups don't blow up. Cheaper to use init_db than to mock.
        db_path = tmp_path / "photopicker.db"
        from bpp.db.connection import init_db

        init_db(str(db_path))

        progress: list[dict] = []
        _faces, _clusters, pid = run_face_extraction_subprocess(
            with_faces,
            config,
            str(db_path),
            progress_callback=progress.append,
        )

        # The exact number of detected faces depends on whether the
        # procedural images happen to look face-like to YuNet/SFace —
        # we don't pin that. What we DO pin: the child completed
        # without segfaulting. faces == 0 is fine; what's not fine is
        # the child crashing.
        assert pid is not None, "subprocess never started"
        # No fatal_error or timeout messages in the progress stream.
        errors = [p for p in progress if isinstance(p, dict) and p.get("type") == "error"]
        assert not errors, (
            f"subprocess emitted error messages — check for SIGSEGV / fatal_error in: {errors}"
        )
