"""Cross-cutting constants used across the bpp codebase.

Module-specific constants stay in their own files.  This module collects
values shared by multiple packages (db, web, scoring, utils).
"""

from __future__ import annotations

# ── SQLite ──
SQLITE_TIMEOUT_S = 30
# WHERE clause for active (non-deleted, non-missing, non-hidden, non-sidecar) photos.
#
# is_live_photo_sidecar = 0 excludes iPhone Live Photo motion sidecars
# (IMG_xxxx_1.HEIC / IMG_xxxx_1.MOV) from all user-facing views.
# Sidecars are stored in the DB for the parent→child link but are
# invisible in the grid, smart albums, scoring, and picking flows.
# See bpp/db/live_photo.py for the full detection assumptions.
ACTIVE_PHOTO_SQL = (
    "missing=0 AND deleted_at IS NULL AND hidden_at IS NULL AND is_live_photo_sidecar = 0"
)

_ACTIVE_COLUMNS = ("missing", "deleted_at", "hidden_at", "is_live_photo_sidecar")


def active_photo_sql(alias: str = "") -> str:
    """Return the active-photo WHERE clause with an optional table alias.

    >>> active_photo_sql()
    'missing=0 AND deleted_at IS NULL AND hidden_at IS NULL AND is_live_photo_sidecar = 0'
    >>> active_photo_sql("p")
    'p.missing=0 AND p.deleted_at IS NULL AND p.hidden_at IS NULL AND p.is_live_photo_sidecar = 0'
    """
    prefix = f"{alias}." if alias else ""
    return (
        f"{prefix}missing=0 AND {prefix}deleted_at IS NULL "
        f"AND {prefix}hidden_at IS NULL AND {prefix}is_live_photo_sidecar = 0"
    )


def visible_photo_conditions(
    *,
    alias: str = "",
    include_missing: bool = False,
    include_deleted: bool = False,
    include_hidden: bool = False,
) -> list[str]:
    """Build the visible-photo WHERE conditions with per-flag opt-outs.

    The ONE place the "what may a user-facing photo list contain" rule is
    assembled — every photo pager builds its conditions here so a new
    pager can't silently drop a clause (the 2026-06-12 Library leak was
    get_photos_page hand-rolling its list and forgetting the sidecar
    filter). Live Photo sidecar exclusion is unconditional: there is no
    opt-in flag, by design.
    """
    prefix = f"{alias}." if alias else ""
    conditions = [f"{prefix}is_live_photo_sidecar = 0"]
    if not include_missing:
        conditions.append(f"{prefix}missing=0")
    if not include_deleted:
        conditions.append(f"{prefix}deleted_at IS NULL")
    if not include_hidden:
        conditions.append(f"{prefix}hidden_at IS NULL")
    return conditions


# ── Sensitive-photo flag (NudeNet score + user override, schema v43) ──
#
# A photo is "sensitive" when the user said so (sensitive_override = 1),
# or when the user said nothing and the model score clears the threshold.
# An explicit override of 0 always wins over the model. These two
# definitions — the SQL fragment below and the Python derivation in
# bpp/web/photo_dict.py (is_sensitive_item) — MUST stay in agreement;
# tests/test_sensitive.py runs the full override x score matrix through
# both layers.
#
# 0.3 calibration: NudeNet 320n confidences on real family-library
# content sit ~0.45 for clear exposed-genitalia detections and below
# ~0.2 for noise; 0.3 catches real flags without sweeping in bath/beach
# noise at scale.
# 0.5, raised from 0.3 (2026-06-12): on a real infant-photo library the
# 0.3 cut flagged 43 photos, ~80% false positives by user review — the
# scoring formula lets a single secondary-class detection (breast/
# buttocks classes, which NudeNet misfires on baby skin) reach 0.3 with
# zero primary evidence. At 0.5, 32/43 unflag including every reviewed
# FP; the rest stay one-click dismissible via the "May be sensitive"
# chip. Threshold applies at read time — no rescore needed.
# 0.7, raised from 0.5 (2026-06-16): at 0.5 the same library still flagged
# 11 photos, ALL false positives the user had already cleared. Diagnosis
# (re-ran NudeNet on the 11): every one was a single MALE/FEMALE_GENITALIA
# _EXPOSED detection at 0.50-0.62 confidence — the model misfiring on a
# baby boy's diaper/bath photos. Genuine explicit content scores far higher
# (~0.85+), so 0.7 sits in the empty gap: clears all 11 FPs while still
# catching real flags. This is now the DEFAULT — runtime-tunable via the
# `sensitive_nudity_threshold` config key (Settings → Content filter) so a
# library needing a different cut adjusts a slider, not code.
SENSITIVE_NUDITY_THRESHOLD = 0.7

# Minimum detector confidence for a pet detection to surface in any
# user-facing view (lightbox chips, Pets view, pet smart albums). The
# DETECTION threshold stays low (0.2 — recall for clustering), but on a
# real library the 0.2-0.5 band was almost entirely fur-texture false
# positives: sheepskin rugs and plush toys read as "dog 43%" (58 dog
# detections, only 13 at >=0.5; cats: 222/146). Stored rows are kept —
# this floor applies at read time, so tuning it never needs a rescan.
PET_DISPLAY_CONFIDENCE = 0.5


def sensitive_photo_sql(threshold: float = SENSITIVE_NUDITY_THRESHOLD) -> str:
    """SQL predicate for 'this photo is sensitive' at ``threshold``.

    The SQL twin of ``is_sensitive_item`` (bpp/web/photo_dict.py) — BOTH
    must use the same threshold at the same moment or the per-photo flag
    and the Sensitive-album membership drift. Callers resolve the runtime
    value (config key ``sensitive_nudity_threshold``, default
    ``SENSITIVE_NUDITY_THRESHOLD``) and pass it in; conn-only sites use
    ``bpp.db.settings.resolve_sensitive_threshold(conn)``.
    ``threshold`` is a schema-validated float, safe to interpolate.
    """
    return (
        "(sensitive_override = 1 OR (sensitive_override IS NULL"
        f" AND nudity_score >= {float(threshold)}))"
    )


# Back-compat constant at the default threshold. Prefer sensitive_photo_sql()
# with the resolved runtime threshold; this fixed-default string is kept for
# any importer that doesn't have a config/conn handy.
SENSITIVE_PHOTO_SQL = sensitive_photo_sql()

SQLITE_BUSY_TIMEOUT_MS = 30_000

# ── JPEG quality ──
JPEG_QUALITY_FULL = 92  # full-size photo conversions, enhanced output
JPEG_QUALITY_THUMB = 80  # gallery thumbnails
JPEG_QUALITY_CROP = 85  # face/pet crop thumbnails
JPEG_QUALITY_SPRITE = 75  # video sprite sheets

# ── Thumbnail / image sizes ──
THUMBNAIL_MAX_SIZE = 400  # default px for grid thumbnails
FACE_CROP_SIZE = 150  # face crop thumbnail px
FACE_CROP_PADDING = 0.3  # fraction of bbox added as margin
PET_CROP_SIZE = 200  # pet crop thumbnail px
PET_CROP_PADDING = 0.15  # fraction of bbox added as margin
HASH_PREFIX_LEN = 32  # characters of sha256 hex used for path hashes (128-bit)

# ── Photo cache suffixes (single source of truth for all cleanup paths) ──
# Cleanup in `ThumbnailCache.remove_for_hash` is glob-based so any
# future suffix gets swept automatically — the registry below is
# kept as DOCUMENTATION + a single-source-of-truth for create
# sites (so adding a variant is one new constant + one tuple entry,
# not grep-and-edit across blueprints).
PHOTO_CACHE_SUFFIX_FULL = "_full"
PHOTO_CACHE_SUFFIX_EDITED = "_edited"
PHOTO_CACHE_SUFFIX_EDITED_THUMB = "_edited_thumb"
PHOTO_CACHE_SUFFIX_INPAINTED = "_inpainted"
PHOTO_CACHE_SUFFIX_SPRITE = "_sprite"
PHOTO_CACHE_SUFFIXES = (
    "",
    PHOTO_CACHE_SUFFIX_FULL,
    PHOTO_CACHE_SUFFIX_EDITED,
    PHOTO_CACHE_SUFFIX_EDITED_THUMB,
    PHOTO_CACHE_SUFFIX_INPAINTED,
    PHOTO_CACHE_SUFFIX_SPRITE,
)

# ── Video ──
VIDEO_SPRITE_FRAMES = 8
VIDEO_SPRITE_WIDTH = 160
VIDEO_SPRITE_START = 0.05  # skip first 5% of video
VIDEO_SPRITE_END = 0.95  # skip last 5% of video

# ── Batch / concurrency ──
DB_FLUSH_BATCH_SIZE = 50  # photos flushed to DB per batch during analysis
SQL_BATCH_SIZE = 500  # rows per WHERE IN (...) batch (SQLite limit ~999)
MAX_WORKER_PROCESSES = 8  # upper bound on ProcessPoolExecutor workers
SEQUENTIAL_THRESHOLD = 2  # fall back to sequential if items <= this

# ── Progress / SSE ──
PROGRESS_QUEUE_TIMEOUT_S = 30  # seconds to block on progress_queue.get()

# ── Worker lifecycle ──
WORKER_JOIN_TIMEOUT_S = 10.0  # seconds to wait for worker thread to stop
SUBPROCESS_GRACEFUL_JOIN_S = 30  # graceful shutdown window for analysis subprocesses
SUBPROCESS_FORCE_JOIN_S = 5  # final wait after .kill() before giving up

# ── Retry ──
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY_S = 0.5

# ── Logging ──
LOG_MAX_BYTES = 5_000_000  # rotating log file max size
LOG_BACKUP_COUNT = 3  # number of rotated log backups

# ── Face clustering sentinel values ──
CLUSTER_UNASSIGNED = -1  # face not yet assigned to a cluster
CLUSTER_DISMISSED = -2  # face dismissed (not a real face / ignored)

# ── Significant-person threshold ──
# Apple-Photos-style rule: a cluster matters when the user named it OR it
# has at least this many photos. The People view uses it for its Included
# tab (JS mirror: FACE_MIN_PHOTOS in static/js/modules/constants.mjs —
# keep the two in sync); group detection uses it so co-occurrence groups
# only form between significant people, not unmerged cluster fragments.
FACE_MIN_PHOTOS = 4

# ── Face clustering threshold ──
# Empirical default for the dlib-128 distance metric: same person typically
# clusters under ~0.55, different people above ~0.9. The user-tunable
# `face_cluster_threshold` config key seeds from this when a fresh DB is
# initialized; `Config.get(..., FACE_CLUSTER_THRESHOLD_FALLBACK)` callers
# use it when DEFAULTS / YAML / DB are all silent.
FACE_CLUSTER_THRESHOLD_FALLBACK = 0.55

# ── Scoring (face) ──
FACE_AREA_MULTIPLIER = 10.0  # ~10% of frame area = perfect score
FACE_EDGE_MULTIPLIER = 10.0  # ~10% margin from edge = fine
FACE_COUNT_PENALTY = 0.1  # per face distance from ideal (1)
FACE_SCORE_AREA_W = 0.25
FACE_SCORE_CENTER_W = 0.20
FACE_SCORE_EDGE_W = 0.15
FACE_SCORE_COUNT_W = 0.15
FACE_SCORE_EXPRESSION_W = 0.25  # blink/smile/frontality from FaceLandmarker

# ── Scoring (face confidence) ──
FACE_GOOD_CONFIDENCE = 0.7  # above this, detection is considered strong
FACE_CONFIDENCE_FLOOR = 0.05  # lowest threshold for iterative relaxation

# ── Scoring (face detection) ──
HAAR_SCALE_FACTOR = 1.1
HAAR_MIN_NEIGHBORS = 5
HAAR_MIN_FACE_SIZE = (30, 30)
FACE_IOU_THRESHOLD = 0.3
FACE_OVERLAP_RATIO = 0.5
MIN_FACE_IMAGE_PX = 40  # min image dimension for face detection
MIN_FACE_AREA_FRAC = 0.002  # min face area as fraction of image (~40x40 at 1024px)

# ── Face NMS (Non-Maximum Suppression) ──
FACE_NMS_SCORE_THRESH = 0.15  # low — upstream min_confidence already filters
FACE_NMS_IOU_THRESH = 0.3  # standard for face detection
DLIB_DEFAULT_CONFIDENCE = 0.75  # synthetic score for dlib HOG (no native score)
HAAR_DEFAULT_CONFIDENCE = 0.5  # synthetic score for Haar cascade (weakest detector)

# ── Enhance (magic pop) ──
ENHANCE_DOWNSAMPLE_SIZE = 800
ENHANCE_JPEG_QUALITY = 92
ENHANCE_SHARPNESS_DEFAULT = 1.1

# ── Optimizer ──
OPTIMIZE_FACE_COVERAGE_W = 0.6
OPTIMIZE_QUALITY_W = 0.4
OPTIMIZE_MAX_FACE_OVERLAP = 3  # cap for face boost calculation

# ── CLIP ──
CLIP_MODEL_NAME = "ViT-B-32"

# ── Model toggles ──
MODEL_TOGGLE_KEYS = (
    "model_scrfd",
    "model_blazeface_fr",
    "model_face_landmarker",
    "model_hand_landmarker",
    "model_segmentation",
    "model_pose",
    "model_nudity",
    "model_pets",
    "model_clip",
)
