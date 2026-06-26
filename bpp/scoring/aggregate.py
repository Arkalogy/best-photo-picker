"""Aggregate scoring pipeline with caching via SQLite."""

from __future__ import annotations

import json
import os
from typing import Any

import cv2
import numpy as np
from tqdm import tqdm

from bpp.config import DEFAULTS

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass

# Decompression-bomb ceiling, made explicit + owned (release-audit hardening).
# PIL ships a ~89MP default that already blocks billion-pixel images, but
# pinning it here (a) documents the decision so a future "support huge
# panoramas" change can't silently set it to None and reopen the hole, and
# (b) sets a deliberate ~200MP bound — comfortably above any real camera /
# panorama, while PIL still raises DecompressionBombError above 2x (~400MP).
# This module is the canonical decode entry (load_and_downscale) on the
# untrusted-import path, so configuring PIL here covers the bulk of decodes.
try:
    from PIL import Image as _PILImage

    _PILImage.MAX_IMAGE_PIXELS = 200_000_000
except ImportError:
    pass

from bpp.db.connection import get_db
from bpp.exif_utils import extract_exif_metadata, get_date
from bpp.scoring.aggregate_video import analyze_single_video
from bpp.scoring.blur import score_blur_raw
from bpp.scoring.composition import score_composition
from bpp.scoring.exposure import score_exposure
from bpp.scoring.face import detect_faces, score_face
from bpp.scoring.skin import score_skin_exposure
from bpp.utils.concurrency import get_worker_count, parallel_map
from bpp.utils.json_utils import safe_json_loads
from bpp.utils.logging import get_logger

log = get_logger(__name__)

_DEFAULT_FACE_CONF = DEFAULTS["face_detection_confidence"]
DB_NAME = "analysis_cache.db"


def init_analysis_db(db_path: str) -> None:
    """Create the legacy analysis_cache.db image_analysis table.

    Used by the standalone CLI (`bpp analyze`). The web/server stack
    uses the main schema in `bpp.db.schema` instead — this lives here
    only because `bpp analyze` predates the unified DB.
    """
    conn = get_db(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS image_analysis (
            filepath TEXT PRIMARY KEY,
            file_size INTEGER,
            file_mtime REAL,
            result_json TEXT
        )
    """)
    conn.commit()


def _cache_key(filepath: str) -> tuple[str, int, float]:
    from bpp.utils.retry import retry_io

    stat = retry_io(os.stat, filepath, label="stat")
    return (filepath, stat.st_size, stat.st_mtime)


def _get_cached(db_path: str, filepath: str, size: int, mtime: float) -> dict | None:
    conn = get_db(db_path)
    row = conn.execute(
        "SELECT result_json FROM image_analysis WHERE filepath=? AND file_size=? AND file_mtime=?",
        (filepath, size, mtime),
    ).fetchone()
    if row:
        return safe_json_loads(row[0], context="analysis cache")
    return None


def _save_cached(db_path: str, filepath: str, size: int, mtime: float, result: dict) -> None:
    conn = get_db(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO image_analysis"
        " (filepath, file_size, file_mtime, result_json) VALUES (?, ?, ?, ?)",
        (filepath, size, mtime, json.dumps(result)),
    )
    conn.commit()


def read_image_for_scoring(filepath: str) -> np.ndarray | None:
    """Load an image as a BGR ndarray for downstream scoring / embedding.

    Tries cv2.imread first (fast, ignores EXIF). Falls back to PIL with
    EXIF transpose for formats cv2 doesn't grok (HEIC, etc.). Wraps the
    whole thing in retry_io so transient NAS / network-FS flakes get
    exponential-backoff retries instead of an immediate failure
    (project rule: retry_io must wrap any flaky-FS read).

    Returns None on persistent failure with a log.warning. Use this as
    the canonical entry-point for any scoring / embedding code that
    needs full-resolution pixels — do NOT call cv2.imread or
    Image.open directly.
    """
    from PIL import Image, ImageOps

    from bpp.utils.retry import retry_io

    def _load() -> np.ndarray | None:
        img = cv2.imread(filepath)
        if img is not None:
            return img
        # cv2 couldn't decode (HEIC, exotic format) — fall back to PIL.
        # Context manager releases the file handle before the array copy.
        with Image.open(filepath) as pil_img:
            transposed = ImageOps.exif_transpose(pil_img).convert("RGB")
            return np.array(transposed)[:, :, ::-1]  # RGB → BGR

    try:
        # OSError is handled inside retry_io (transient → backoff +
        # retry, persistent → re-raise). Non-OSError exceptions (PIL
        # decode failures on a corrupt file, ValueError from a 0-byte
        # input, etc.) propagate out — we want both treated the same:
        # log + return None.
        return retry_io(_load, label="read_image_for_scoring")
    except Exception as e:
        log.warning("Failed to load %s for scoring: %s", filepath, e)
        return None


def load_and_downscale(filepath: str, max_long_side: int) -> np.ndarray | None:
    """Load image and downscale to max_long_side.

    Retries on transient I/O errors (NAS flakes, stale handles).
    """
    import time

    from bpp.utils.retry import is_transient

    for attempt in range(3):
        try:
            # Always use Pillow: cv2.imread ignores EXIF orientation,
            # which causes sideways face detection on rotated phone photos.
            from PIL import Image, ImageOps

            from bpp.media_types import MediaKind, media_kind_from_path

            if media_kind_from_path(filepath) is MediaKind.RAW:
                from bpp.utils.raw import open_raw_as_pil

                pil_img = open_raw_as_pil(filepath)
                if pil_img is None:
                    return None
            else:
                # Context manager releases the file handle before the
                # array copy; exif_transpose() returns a new Image, so
                # the open file isn't pinned past the `with` block.
                with Image.open(filepath) as raw:
                    pil_img = ImageOps.exif_transpose(raw)
            pil_img = pil_img.convert("RGB")
            img = np.array(pil_img)[:, :, ::-1]  # RGB -> BGR

            h, w = img.shape[:2]
            long_side = max(h, w)
            if long_side > max_long_side:
                scale = max_long_side / long_side
                new_w = int(w * scale)
                new_h = int(h * scale)
                img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
            return img
        except OSError as e:
            if attempt < 2 and is_transient(e):
                time.sleep(0.5 * (2**attempt))
                continue
            log.debug("Failed to load %s: %s", filepath, e)
            return None
        except Exception as e:
            log.debug("Failed to load %s: %s", filepath, e)
            return None


def analyze_single_image(
    filepath: str,
    max_long_side: int = 1024,
    face_detection_confidence: float = _DEFAULT_FACE_CONF,
    model_toggles: dict[str, bool] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Analyze a single image and return feature dict.

    *model_toggles* controls which ML models run.  Keys are
    ``model_<name>`` booleans; missing keys default to True.
    *config* passes through user settings (pet thresholds, etc.).
    """
    mt = model_toggles or {}
    config = config or {}
    img = load_and_downscale(filepath, max_long_side)
    if img is None:
        return None

    # Extract features — detect faces first so blur can weight face regions
    _mfaf = float(config.get("min_face_area_pct", 0.20)) / 100.0
    faces = detect_faces(
        img,
        min_confidence=face_detection_confidence,
        min_face_area_frac=_mfaf,
        model_toggles=mt,
    )
    blur_raw = score_blur_raw(img, faces=faces)
    exposure = score_exposure(img)
    face_result = score_face(
        img,
        min_confidence=face_detection_confidence,
        min_face_area_frac=_mfaf,
        faces=faces,
        model_toggles=mt,
    )
    comp = score_composition(img, faces, model_toggles=mt)

    dt = get_date(filepath)

    skin = score_skin_exposure(img)

    result = {
        "filepath": filepath,
        "date": dt.isoformat(),
        "date_day": dt.strftime("%Y-%m-%d"),
        "date_month": dt.strftime("%Y-%m"),
        "blur_raw": blur_raw,
        "exposure_score": exposure,
        "face_score": face_result["face_score"],
        "face_count": face_result["face_count"],
        "largest_face_ratio": face_result["largest_face_ratio"],
        "face_center_dist": face_result["face_center_dist"],
        "composition_score": comp,
        "skin_score": skin,
    }

    # EXIF metadata
    exif_meta = extract_exif_metadata(filepath)
    if exif_meta:
        result["exif_json"] = json.dumps(exif_meta)
        # lift GPS coords out of the JSON blob into stable
        # columns. Map view + album-stats query both filter on these
        # — putting them in indexed columns kills the json_extract
        # full-table scan.
        if "gps_lat" in exif_meta:
            result["gps_lat"] = exif_meta["gps_lat"]
        if "gps_lon" in exif_meta:
            result["gps_lon"] = exif_meta["gps_lon"]

    # Optional ML scorers (nudity, pets, future) — registry-driven dispatch.
    # Adding a new optional ML scorer = one new registry entry; this
    # block stays unchanged. See bpp/scoring/registry.py.
    from bpp.scoring.registry import run_optional_scorers

    result.update(run_optional_scorers(img, filepath, mt, config))

    return result


def process_one(args: tuple) -> dict[str, Any] | None:
    """Worker: (filepath, max_long_side, db_path[, face_confidence[, model_toggles[, config]]])."""
    from bpp.media_types import MediaKind, media_kind_from_path

    if len(args) >= 6:
        filepath, max_long_side, db_path, face_confidence, model_toggles, config = args
    elif len(args) >= 5:
        filepath, max_long_side, db_path, face_confidence, model_toggles = args
        config = {}
    elif len(args) >= 4:
        filepath, max_long_side, db_path, face_confidence = args
        model_toggles = {}
        config = {}
    else:
        filepath, max_long_side, db_path = args
        face_confidence = _DEFAULT_FACE_CONF
        model_toggles = {}
        config = {}
    from bpp.scoring.model_base import ModelIntegrityError

    try:
        fp, size, mtime = _cache_key(filepath)
        cached = _get_cached(db_path, fp, size, mtime)
        if cached:
            return cached
        if media_kind_from_path(filepath) is MediaKind.VIDEO:
            result = analyze_single_video(
                filepath,
                max_long_side,
                model_toggles=model_toggles,
            )
        else:
            result = analyze_single_image(
                filepath,
                max_long_side,
                face_confidence,
                model_toggles,
                config=config,
            )
        if result:
            _save_cached(db_path, fp, size, mtime, result)
        return result
    except ModelIntegrityError:
        # Model integrity failures (tampered/corrupt SHA mismatch)
        # MUST propagate. Swallowing them here would degrade every photo
        # to "no nudity / no detection" silently — the exact failure mode
        # the SHA pin is supposed to prevent. Re-raise so the worker
        # surfaces a loud error and aborts.
        log.error("Model integrity failure aborting analysis of %s", filepath)
        raise
    except Exception as e:
        log.warning("Failed to process %s: %s", filepath, e)
        return None


def _blur_log_sigmoid(raw: float, mid: float = 200.0, k: float = 1.5) -> float:
    """Map blur_raw to 0..1 via log-sigmoid.

    Calibrated so that:
      - raw ~50  (soft selfie)       → ~11%
      - raw ~200 (decent sharpness)  → ~50%
      - raw ~1000 (very sharp)       → ~92%
    """
    import math

    if raw <= 0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-k * (math.log(raw) - math.log(mid))))


def normalize_blur_scores(results: list[dict[str, Any]]) -> None:
    """Normalize blur_raw to blur_score 0..1 using log-sigmoid scaling.

    Uses an absolute scale (not dataset-relative percentiles) so that
    a "decent" photo always scores ~50% regardless of what other photos
    are in the library.
    """
    for r in results:
        r["blur_score"] = _blur_log_sigmoid(r["blur_raw"])


def compute_aggregate(results: list[dict[str, Any]], config: dict[str, Any]) -> None:
    """Compute weighted aggregate score for each result."""
    from bpp.scoring.registry import get_weighted_scorers

    weighted = get_weighted_scorers()
    weights = {s.key: config.get(s.weight_key, s.default_weight) for s in weighted}
    total_w = sum(weights.values())
    if total_w == 0:
        total_w = 1.0

    for r in results:
        agg = (
            sum(weights[s.key] * r.get(f"{s.key}_score", s.aggregate_default) for s in weighted)
            / total_w
        )
        r["aggregate_score"] = round(agg, 6)


def analyze_all(
    image_paths: list[str],
    workdir: str,
    config: dict[str, Any],
    workers: int = 0,
    seed: int = 42,
) -> dict[str, Any]:
    """Run full analysis pipeline on all images."""
    db_path = os.path.join(workdir, DB_NAME)
    init_analysis_db(db_path)
    max_long_side = config.get("max_long_side", 1024)
    face_confidence = float(config.get("face_detection_confidence", _DEFAULT_FACE_CONF))

    args_list = [(fp, max_long_side, db_path, face_confidence) for fp in image_paths]

    n_workers = get_worker_count(workers)
    results: list[dict[str, Any] | None] = []
    skipped = 0

    if n_workers <= 1 or len(image_paths) <= 2:
        for args in tqdm(args_list, desc="Analyzing", unit="img"):
            r = process_one(args)
            results.append(r)
    else:
        results = parallel_map(process_one, args_list, workers=workers)

    valid = [r for r in results if r is not None]
    skipped = len(results) - len(valid)

    # Normalize blur scores across dataset
    normalize_blur_scores(valid)
    compute_aggregate(valid, config)

    # Save full results
    from bpp.utils.retry import retry_io

    def _write():
        with open(results_path, "w") as f:
            json.dump(valid, f, indent=2)

    results_path = os.path.join(workdir, "analysis.json")
    retry_io(_write, label="write_analysis_json")

    return {"processed": len(valid), "skipped": skipped, "results": valid}


def load_analysis(workdir: str) -> list[dict[str, Any]]:
    """Load previously saved analysis results."""
    from bpp.utils.retry import retry_io

    results_path = os.path.join(workdir, "analysis.json")
    if not os.path.exists(results_path):
        return []

    def _read():
        with open(results_path) as f:
            data = f.read()
        return safe_json_loads(data, [], context="analysis.json")

    return retry_io(_read, label="load_analysis_json")
