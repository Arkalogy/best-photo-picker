"""Background face embedding extraction with progress queue for SSE streaming."""

from __future__ import annotations

# Pin native-library thread pools to 1 BEFORE numpy / cv2 / dlib /
# onnxruntime / mediapipe import. See the matching block in
# `bpp/web/analyze_worker.py` for the full rationale — TL;DR: the
# face-extraction subprocess re-imports this module via
# `from bpp.web.face_worker import extract_and_cluster_faces` in the
# spawn child, and OpenMP/OpenBLAS/MKL grab CPU-count threads at
# C-extension import time. With workers >= 2 in the Python
# ThreadPoolExecutor, the nested oversubscription deterministically
# SIGSEGV's. setdefault so the operator can opt out per-machine.
import os as _os

for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    _os.environ.setdefault(_var, "1")

# C-extension imports below MUST stay after the env-var pinning above.
# See the matching block in `bpp/web/analyze_worker.py` for the full
# rationale. Per-line noqa preserves E402 lint coverage for any new
# top-level statement that doesn't need this ordering.
from typing import Any  # noqa: E402

import numpy as np  # noqa: E402

from bpp.db.connection import init_db  # noqa: E402
from bpp.scoring.aggregate import load_and_downscale  # noqa: E402
from bpp.scoring.face_embed import extract_face_embeddings  # noqa: E402
from bpp.utils.logging import get_logger  # noqa: E402
from bpp.web.base_worker import BackgroundWorker  # noqa: E402

log = get_logger(__name__)


def _validate_bbox(bx: int, by: int, bw: int, bh: int) -> tuple[int, int, int, int] | None:
    """Validate and clamp bbox values. Returns None if invalid (zero/negative size)."""
    bx = max(0, bx)
    by = max(0, by)
    if bw <= 0 or bh <= 0:
        return None
    return (bx, by, bw, bh)


_MIN_EMBEDDING_NORM = 1e-6  # below this we treat the vector as effectively zero


def _validate_embedding(emb: np.ndarray) -> bool:
    """Validate that an embedding is a 128-dim float array with no NaN/Inf
    and a non-degenerate magnitude.

    L3: a zero-vector embedding sails through the shape + finiteness checks
    but poisons clustering — its distance to every centroid is identical
    (the centroid's own norm), so dlib's threshold-based clustering snaps
    it onto whatever cluster it sees first. The magnitude check rejects
    the degenerate case before it can pollute downstream state.
    """
    if emb.shape != (128,):
        return False
    if not bool(np.isfinite(emb).all()):
        return False
    return bool(np.linalg.norm(emb) >= _MIN_EMBEDDING_NORM)


def _extract_one(
    filepath: str,
    max_long_side: int,
    min_confidence: float = 0.2,
    embedding_confidence: float = 0.65,
    min_embedding_quality: float = 0.25,
    method: str | None = None,
) -> list[dict] | None:
    """Extract face embeddings from a single image (process-safe).

    ModelIntegrityError MUST propagate —
    YuNet/SFace cached-file verification raise on tampered bytes,
    but this broad `except Exception` was eating the error and
    treating every photo as "no faces detected" — exactly the
    silent-degrade pattern the integrity check defends against.

    ``method`` is resolved upstream from the user's
    ``face_embedding_method`` setting (or ``None`` to auto-detect by
    model availability). Passing it to every worker call is what
    makes the toggle actually take effect on extraction — pre-fix it
    only affected the audit-trail row in settings.
    """
    from bpp.scoring.model_base import ModelIntegrityError

    try:
        img = load_and_downscale(filepath, max_long_side)
        if img is None:
            return None
        return extract_face_embeddings(
            img,
            min_confidence=min_confidence,
            embedding_confidence=embedding_confidence,
            min_embedding_quality=min_embedding_quality,
            method=method,
        )
    except ModelIntegrityError:
        log.error("Face model integrity failure on %s", filepath, exc_info=True)
        raise
    except Exception:
        log.warning("Face extraction failed for %s, skipping", filepath, exc_info=True)
        return None


# ── Re-exports kept for backward compatibility ──
# The orchestrator + per-phase journal bookkeeping lives in
# bpp.web.face_orchestrator. Recovery handlers
# (recover_pending_face_extractions, register_*_recovery) live in
# bpp.web.face_recovery. Identity reconstruction + remap helpers come
# from bpp.db.face_identity_remap. Read-only face cluster queries +
# the face-crop generator from bpp.db.face_queries and
# bpp.web.face_crop. We re-export everything here so existing call
# sites (state_lifecycle, analyze_face_extract, ~10 tests that patch
# face_worker._extract_one, etc.) keep working unchanged after the
# LOC-cap split.
from bpp.db.face_identity_remap import (  # noqa: E402, F401
    _MIN_ASSIGN_PX,
    _assign_new_faces,
    _bbox_quality,
    _is_default_person_name,
    _reconstruct_identities,
    _remap_names_and_tags,
    _snapshot_cluster_photos,
)
from bpp.db.face_queries import (  # noqa: E402, F401
    count_stale_faces,
    has_face_data,
    load_face_cluster_map,
    load_face_clusters,
    needs_face_clustering,
)
from bpp.web.face_crop import generate_face_crop  # noqa: E402, F401
from bpp.web.face_orchestrator import extract_and_cluster_faces  # noqa: E402, F401
from bpp.web.face_recovery import (  # noqa: E402, F401
    recover_pending_face_extractions,
    register_face_clustering_recovery,
    register_face_extraction_retry_recovery,
)


class FaceWorker(BackgroundWorker):
    """Extracts face embeddings in a background thread, then clusters."""

    _worker_name = "Face extraction"

    def start(
        self,
        analysis: list[dict[str, Any]],
        db_path: str,
        config: dict[str, Any],
    ) -> bool:
        """Start background face extraction. Returns False if already running."""
        return self._start_thread(analysis, db_path, config)

    def _run(
        self,
        analysis: list[dict[str, Any]],
        db_path: str,
        config: dict[str, Any],
    ) -> None:
        # Server-log breadcrumb: face extraction is a 6+ minute run on
        # large libraries. Without start/end log lines, a maintainer
        # debugging a stuck worker can't tell from server.log alone
        # whether extraction started or finished — the SSE progress
        # events only reach the client. Project convention: nothing
        # should be silent.
        import time as _time

        _t0 = _time.perf_counter()
        log.info("Face extraction starting: %d candidate photo(s)", len(analysis))
        conn = init_db(db_path)

        # Build filepath -> photo_id map
        photo_map: dict[str, int] = {}
        for item in analysis:
            pid = item.get("id")
            if pid is not None:
                photo_map[item["filepath"]] = pid

        # Process images that scoring found faces in, PLUS any that already
        # have embeddings in the DB (handles face_count=0 inconsistency —
        # the face worker detects independently of the scoring pipeline).
        photos_with_embeddings: set[int] = set()
        for r in conn.execute("SELECT DISTINCT photo_id FROM face_embeddings").fetchall():
            photos_with_embeddings.add(r[0])

        with_faces = [
            a
            for a in analysis
            # `.get(key, 0)` only defaults when the key is absent. When the
            # photo row is enriched from a DB row that has face_count=NULL
            # (e.g. early-stage library where import_folder inserted with
            # the default before analyze persisted counts), the value is
            # present-as-None and `None > 0` blows up. `or 0` covers both.
            if (a.get("face_count") or 0) > 0
            or photo_map.get(a["filepath"]) in photos_with_embeddings
        ]
        total = len(with_faces)
        self._emit({"type": "start", "total": total})

        if total == 0:
            log.info(
                "Face extraction done: 0 with-face candidates, 0 faces, 0 clusters in %.1fs",
                _time.perf_counter() - _t0,
            )
            self._emit({"type": "done", "total": 0, "faces_found": 0, "clusters": 0})
            return

        # Free any scoring-phase models the SERVER process happens to be
        # holding before we spawn the child. These are RSS the kernel
        # accounts against the parent — releasing them lets the parent
        # stay slim while the child does the heavy work.
        from bpp.scoring.face import _fr_detector, _scrfd_model

        _scrfd_model.reset()
        _fr_detector.reset()

        # Pre-download face detection + recognition models in the parent
        # (cheap — just files-on-disk checks). Spinning model files into
        # the cache before the child starts means the child doesn't pay
        # cold-start latency hidden behind a "starting…" status.
        from bpp.scoring.face import ensure_face_models
        from bpp.scoring.face_embed import ensure_sface_model

        for warn in ensure_face_models():
            self._emit({"type": "warning", "message": warn})
        for warn in ensure_sface_model():
            self._emit({"type": "warning", "message": warn})

        # Subprocess isolation. extract_and_cluster_faces loads SCRFD +
        # BlazeFace + dlib + SFace + HandLandmarker — collectively ~600-
        # 900 MB of pinned model RAM plus per-photo decoded ndarrays.
        # Running this in the server process at 1500+ photos reliably
        # SIGKILLs the entire server (no traceback, every in-flight
        # request dies). The subprocess pattern is identical to what
        # AnalyzeWorker already does for the scoring + face phases —
        # see run_face_extraction_subprocess for the full rationale.
        from bpp.web.analyze_face_extract import run_face_extraction_subprocess

        # P1: thread the FaceWorker's threading-side cancel into the
        # subprocess runner — same plumbing as AnalyzeWorker.
        faces_found, n_clusters, _pid = run_face_extraction_subprocess(
            with_faces,
            config,
            db_path,
            progress_callback=self._emit,
            cancel_event=self._cancelled,
        )

        # Clear any pending `face_extraction_retry` journal entries.
        # api_faces_retry creates one before wiping face data; we close
        # the loop here so the recovery handler doesn't re-fire on next
        # startup. Best-effort: a DB error here doesn't change the fact
        # that extraction completed successfully — the user-visible
        # state is correct, and worst case the recovery handler runs on
        # next startup and finds nothing to do (handlers are
        # idempotent).
        try:
            from bpp.db.journal import journal_complete, pending_journals

            for entry in pending_journals(conn, kind="face_extraction_retry"):
                journal_complete(conn, entry["id"])
        except Exception:
            log.warning(
                "Failed to clear face_extraction_retry journal entries — "
                "next startup may re-fire recovery (idempotent, harmless)",
                exc_info=True,
            )

        log.info(
            "Face extraction done: %d with-face candidates, %d faces found, %d clusters in %.1fs",
            total,
            faces_found,
            n_clusters,
            _time.perf_counter() - _t0,
        )
        self._emit(
            {
                "type": "done",
                "total": total,
                "faces_found": faces_found,
                "clusters": n_clusters,
            }
        )
