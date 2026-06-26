"""Full-coverage face detection for the embedding extractors.

Both built-in embedders (SFace, buffalo_s) need YuNet's 5-point landmark
row to align a crop before extracting an embedding. Historically they
detected with YuNet *alone* — but the scoring phase counts faces with the
full multi-detector pipeline (SCRFD + YuNet + BlazeFace + MediaPipe), so
extraction systematically dropped faces scoring had already found. On real
group photos YuNet alone found 1 of 8 faces the full pipeline found; the
result was thousands of detected-but-never-embedded faces and people
silently missing from the library.

This module closes that gap. :func:`detect_face_rows` runs the full
pipeline, then recovers a real YuNet landmark row for every box by
re-running YuNet on a padded crop around it (a face too small for YuNet at
full scale is usually detectable once cropped, where it fills more of the
frame). Boxes that YuNet still can't confirm even when cropped are dropped
— which doubles as a false-positive filter.

Recall-favoring by design (the product wants every real face to reach
clustering; a spurious face is one dismiss-click, a missing person is
invisible): faces YuNet finds at full scale keep the calibrated
``embedding_confidence`` gate (no precision regression for the easy case);
crop-recovered faces are admitted on landmark geometry + the downstream
quality gate instead of a high detector-confidence bar.
"""

from __future__ import annotations

import numpy as np

from bpp.constants import MIN_FACE_IMAGE_PX
from bpp.utils.logging import get_logger

log = get_logger(__name__)

# Padding around a detected box (as a fraction of its long side) before the
# YuNet crop re-detect. 0.6 gives YuNet enough context to fire on a face it
# missed at full image scale without pulling in neighbouring faces.
_CROP_PAD_FRAC = 0.6
# Low floor for the crop re-detect: the geometry validator + quality gate are
# the precision guards, so the crop pass only needs to *find* the face.
_CROP_REDETECT_CONF = 0.10
# YuNet raw row coordinate indices (see face_yunet._yunet_detect_raw):
#   x at 0; landmark x at 4,6,8,10,12 — y at 1; landmark y at 5,7,9,11,13.
_X_IDX = (0, 4, 6, 8, 10, 12)
_Y_IDX = (1, 5, 7, 9, 11, 13)
# How much more confident the 180°-rotated re-detect must be before we
# trust it over the original detection. YuNet fires on upside-down faces
# with hallucinated upright landmarks (verified on real bath photos:
# 0.86 on the inverted view vs 0.94 rotated); a margin keeps genuinely
# upright faces from flapping on detector noise.
_INVERSION_CONF_MARGIN = 0.05
# The rotated re-detect must land where the original face is (after
# rotating coordinates) — rejects a *different* face inside the padded crop.
_INVERSION_MIN_IOU = 0.3


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix0, iy0 = max(ax, bx), max(ay, by)
    ix1, iy1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter == 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _recover_landmarks_via_crop(
    image: np.ndarray, box: tuple[int, int, int, int]
) -> np.ndarray | None:
    """Re-run YuNet on a padded crop around *box* to recover its landmark row.

    Returns a YuNet-format 15-element row in full-image coordinates, or None
    if YuNet can't confirm a face in the crop (extreme profile / false box).
    """
    from bpp.scoring.face_yunet import _yunet_detect_raw

    x, y, w, h = box
    H, W = image.shape[:2]
    pad = int(_CROP_PAD_FRAC * max(w, h))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    crop = image[y0:y1, x0:x1]
    if crop.shape[0] < MIN_FACE_IMAGE_PX or crop.shape[1] < MIN_FACE_IMAGE_PX:
        return None
    raw = _yunet_detect_raw(crop, min_confidence=_CROP_REDETECT_CONF)
    if raw is None or len(raw) == 0:
        return None
    # The crop may contain more than one face; pick the detection whose centre
    # is closest to the original box centre (in crop coordinates).
    cx, cy = (x - x0) + w / 2.0, (y - y0) + h / 2.0
    best = min(raw, key=lambda f: (f[0] + f[2] / 2.0 - cx) ** 2 + (f[1] + f[3] / 2.0 - cy) ** 2)
    row = best.astype(np.float32).copy()
    for xi in _X_IDX:
        row[xi] += x0
    for yi in _Y_IDX:
        row[yi] += y0
    return row


def detect_inverted_face(image: np.ndarray, face: np.ndarray) -> np.ndarray | None:
    """Detect whether *face* is actually 180°-upside-down in *image*.

    YuNet detects upside-down faces but hallucinates UPRIGHT landmark
    geometry on them (eyes-above-nose-above-mouth — passes every
    validator), so SFace/ArcFace alignment produces an inverted crop
    whose embedding lands far from the person's upright cluster. The
    landmarks can't reveal the inversion; a rotated re-detect can:
    re-run YuNet on the 180°-rotated padded crop, and if it finds the
    same face *more confidently* (by :data:`_INVERSION_CONF_MARGIN`)
    with valid geometry, the face is inverted.

    Returns the rotated detection as a YuNet row in **180°-rotated
    full-image coordinates** (align against ``image[::-1, ::-1]``), or
    ``None`` when the face is upright (or the check is inconclusive —
    safe default: keep current behavior).
    """
    from bpp.scoring.face_embed_landmarks import _validate_yunet_landmarks
    from bpp.scoring.face_yunet import _yunet_detect_raw

    orig_conf = float(face[-1])
    if orig_conf + _INVERSION_CONF_MARGIN >= 1.0:
        return None  # rotated view can't beat it — skip the extra detect

    x, y, w, h = int(face[0]), int(face[1]), int(face[2]), int(face[3])
    H, W = image.shape[:2]
    pad = int(_CROP_PAD_FRAC * max(w, h))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
    cw, ch = x1 - x0, y1 - y0
    if cw < MIN_FACE_IMAGE_PX or ch < MIN_FACE_IMAGE_PX:
        return None

    crop_rot = np.ascontiguousarray(image[y0:y1, x0:x1][::-1, ::-1])
    raw = _yunet_detect_raw(crop_rot, min_confidence=_CROP_REDETECT_CONF)
    if raw is None or len(raw) == 0:
        return None

    # Where the original face sits inside the rotated crop.
    expected = (cw - (x - x0) - w, ch - (y - y0) - h, w, h)
    best = max(
        raw,
        key=lambda f: _iou((int(f[0]), int(f[1]), int(f[2]), int(f[3])), expected),
    )
    bbox = (int(best[0]), int(best[1]), int(best[2]), int(best[3]))
    if _iou(bbox, expected) < _INVERSION_MIN_IOU:
        return None
    if float(best[-1]) < orig_conf + _INVERSION_CONF_MARGIN:
        return None
    if not _validate_yunet_landmarks(best):
        return None

    # Map crop_rot coords → rotated-full-image coords: crop_rot's origin
    # within image[::-1, ::-1] is (W - x1, H - y1) — pure translation.
    row = best.astype(np.float32).copy()
    for xi in _X_IDX:
        row[xi] += W - x1
    for yi in _Y_IDX:
        row[yi] += H - y1
    log.info(
        "Inverted face at (%d,%d,%d,%d): rotated re-detect conf %.2f > %.2f — "
        "aligning against the 180°-rotated view",
        x,
        y,
        w,
        h,
        float(best[-1]),
        orig_conf,
    )
    return row


def detect_face_rows(
    image: np.ndarray,
    min_confidence: float,
    embedding_confidence: float,
    model_toggles: dict[str, bool] | None = None,
) -> list[np.ndarray]:
    """Return YuNet-format landmark rows for every face in *image*.

    Direct full-image YuNet detections keep the calibrated
    ``embedding_confidence`` gate. Faces only the wider pipeline finds get a
    real landmark row recovered via a YuNet crop re-detect and are admitted
    on geometry (``_validate_yunet_landmarks``) — the downstream quality gate
    in the extractor is the final filter.

    Rows are deduplicated by bbox overlap (a face found both directly and via
    the pipeline is kept once, preferring the direct row).
    """
    from bpp.scoring.face import detect_faces_with_confidence
    from bpp.scoring.face_embed_landmarks import _validate_yunet_landmarks
    from bpp.scoring.face_yunet import _yunet_detect_raw

    rows: list[np.ndarray] = []
    seen: list[tuple[int, int, int, int]] = []

    # 1. Direct YuNet on the full image — real landmarks, calibrated gate.
    direct = _yunet_detect_raw(image, min_confidence=min_confidence)
    if direct is not None:
        for f in direct:
            if float(f[-1]) < embedding_confidence:
                continue
            box = (int(f[0]), int(f[1]), int(f[2]), int(f[3]))
            rows.append(f.astype(np.float32))
            seen.append(box)

    # 2. Full pipeline — recover landmarks for any face YuNet missed at scale.
    pipeline = detect_faces_with_confidence(
        image, min_confidence=min_confidence, model_toggles=model_toggles
    )
    for x, y, w, h, _conf in pipeline:
        box = (int(x), int(y), int(w), int(h))
        if any(_iou(box, s) >= 0.3 for s in seen):
            continue  # already have a row for this face
        row = _recover_landmarks_via_crop(image, box)
        if row is None:
            continue
        if not _validate_yunet_landmarks(row):
            continue
        # Dedup on the RECOVERED bbox, not the pipeline box: crop re-detect can
        # land on a face we already have (overlapping pipeline boxes, or a
        # neighbouring face inside the padded crop).
        rbox = (int(row[0]), int(row[1]), int(row[2]), int(row[3]))
        if any(_iou(rbox, s) >= 0.3 for s in seen):
            continue
        rows.append(row)
        seen.append(rbox)

    return rows
