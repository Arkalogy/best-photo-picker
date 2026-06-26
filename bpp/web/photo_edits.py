"""Non-destructive photo edits engine.

Extracted from bp_media.py during the v0.1 cleanup. This module
owns the open-transpose-edit-save pipeline that backs
``_generate_cached_image`` (the project's canonical entry-point for
producing cached edit variants) plus all of the ``_apply_*``
operations that compose into ``_apply_edits``:

* ``_apply_crop``       — normalized 0..1 crop box
* ``_apply_orientation`` — rotation in 90° steps
* ``_apply_basic_color`` — brightness / contrast / saturation
* ``_apply_advanced``   — shadows / highlights / vibrance / etc.
* ``_apply_perspective`` — vert / horiz keystone correction
* ``_apply_redeye``     — red-eye removal at supplied points
* ``_perspective_coefficients`` — solver for the perspective xform

bp_media.py re-exports ``_generate_cached_image`` for backwards
compatibility with the project rule that names
``bpp.web.bp_media`` as the canonical entry-point. New callers can
import directly from this module.
"""

from __future__ import annotations

import contextlib
import os

from PIL import Image, ImageOps

from bpp.constants import JPEG_QUALITY_CROP, JPEG_QUALITY_FULL
from bpp.utils.logging import get_logger
from bpp.utils.retry import retry_io

log = get_logger(__name__)


def _get_edits_for_path(ctx: object, filepath: str) -> dict | None:
    """Look up edits for a photo by filepath."""
    from bpp.db.edits import get_photo_edits
    from bpp.db.photos import get_photo_by_path

    conn = ctx.get_conn()
    photo = get_photo_by_path(conn, filepath)
    if not photo:
        return None
    return get_photo_edits(conn, photo["id"])


def _generate_cached_image(
    filepath: str,
    output_path: str,
    *,
    edits: dict | None = None,
    thumb_size: bool = False,
) -> bool:
    """Open, transpose, apply edits, and save a cached JPEG.

    Args:
        filepath: source image path
        output_path: destination cache path
        edits: edit parameters to apply (or None)
        thumb_size: if True, downscale to THUMBNAIL_MAX_SIZE

    Returns True on success, False on failure (logs warning).
    """
    tmp_path = output_path + ".tmp"
    try:
        from bpp.constants import THUMBNAIL_MAX_SIZE

        quality = JPEG_QUALITY_CROP if thumb_size else JPEG_QUALITY_FULL
        img = retry_io(Image.open, filepath, label="cached_img_open")
        with img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            if edits:
                img = _apply_edits(img, edits)
            if thumb_size:
                img.thumbnail((THUMBNAIL_MAX_SIZE, THUMBNAIL_MAX_SIZE), Image.LANCZOS)
            img.save(tmp_path, "JPEG", quality=quality)
        os.replace(tmp_path, output_path)
        log.debug("Generated cached image: %s", output_path)
        return True
    except Exception as e:
        log.warning("Failed to generate %s from %s: %s", output_path, filepath, e)
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        return False


def _apply_crop(img: Image.Image, edits: dict) -> Image.Image:
    """Crop the image based on normalized 0..1 ``crop_{x,y,w,h}`` keys."""
    cx = edits.get("crop_x")
    cy = edits.get("crop_y")
    cw = edits.get("crop_w")
    ch = edits.get("crop_h")
    if cx is None or cy is None or cw is None or ch is None:
        return img
    w, h = img.size
    return img.crop(
        (
            int(cx * w),
            int(cy * h),
            int((cx + cw) * w),
            int((cy + ch) * h),
        )
    )


def _apply_orientation(img: Image.Image, edits: dict) -> Image.Image:
    """Flip + straighten + 90°-snapped rotation."""
    if edits.get("flip_h"):
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    if edits.get("flip_v"):
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
    straighten = edits.get("straighten", 0.0)
    if straighten:
        img = img.rotate(-straighten, resample=Image.BICUBIC, expand=True, fillcolor=(0, 0, 0))
    rotation = edits.get("rotation", 0)
    # PIL rotates counter-clockwise, so negate for CW.
    if rotation == 90:
        img = img.transpose(Image.ROTATE_270)
    elif rotation == 180:
        img = img.transpose(Image.ROTATE_180)
    elif rotation == 270:
        img = img.transpose(Image.ROTATE_90)
    return img


def _apply_basic_color(img: Image.Image, edits: dict) -> Image.Image:
    """PIL ImageEnhance pass for brightness / contrast / saturation / sharpness."""
    from PIL import ImageEnhance

    if edits.get("brightness", 1.0) != 1.0:
        img = ImageEnhance.Brightness(img).enhance(edits["brightness"])
    if edits.get("contrast", 1.0) != 1.0:
        img = ImageEnhance.Contrast(img).enhance(edits["contrast"])
    if edits.get("saturation", 1.0) != 1.0:
        img = ImageEnhance.Color(img).enhance(edits["saturation"])
    if edits.get("sharpness", 1.0) != 1.0:
        img = ImageEnhance.Sharpness(img).enhance(edits["sharpness"])
    return img


# Keys handled by the numpy-based advanced pipeline. Listed here so
# ``_apply_advanced`` can short-circuit cheaply when none are set.
_ADVANCED_KEYS = (
    "warmth",
    "highlights",
    "shadows",
    "vignette",
    "grain",
    "fade",
    "exposure",
    "brilliance",
    "black_point",
    "vibrance",
    "tint",
    "definition",
    "noise_reduction",
)


def _apply_advanced(img: Image.Image, edits: dict) -> Image.Image:
    """All numpy-array based adjustments. Operates on a single numpy
    buffer so we don't pay the PIL→numpy round-trip per knob."""
    if not any(edits.get(k, 0.0) for k in _ADVANCED_KEYS):
        return img

    import numpy as np

    arr = np.array(img, dtype=np.float32)

    exposure_val = edits.get("exposure", 0.0)
    if exposure_val:
        # +1 EV = double, -1 EV = halve
        arr = np.clip(arr * (2.0**exposure_val), 0, 255)

    brilliance_val = edits.get("brilliance", 0.0)
    if brilliance_val:
        lum = (arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114) / 255.0
        shadow_boost = np.clip(1.0 - lum, 0, 1) * brilliance_val * 60
        highlight_compress = np.clip(lum - 0.5, 0, 0.5) * brilliance_val * -40
        arr = np.clip(arr + (shadow_boost + highlight_compress)[..., np.newaxis], 0, 255)

    black_point_val = edits.get("black_point", 0.0)
    if black_point_val:
        crush = black_point_val * 80
        arr = np.clip(arr - crush, 0, 255) * (255.0 / max(255 - crush, 1))
        arr = np.clip(arr, 0, 255)

    warmth = edits.get("warmth", 0.0)
    if warmth:
        shift = warmth * 40
        arr[..., 0] = np.clip(arr[..., 0] + shift, 0, 255)
        arr[..., 2] = np.clip(arr[..., 2] - shift, 0, 255)

    tint_val = edits.get("tint", 0.0)
    if tint_val:
        shift = tint_val * 30
        arr[..., 1] = np.clip(arr[..., 1] - shift, 0, 255)  # negative = more green
        arr[..., 0] = np.clip(arr[..., 0] + shift * 0.5, 0, 255)
        arr[..., 2] = np.clip(arr[..., 2] + shift * 0.5, 0, 255)

    vibrance_val = edits.get("vibrance", 0.0)
    if vibrance_val:
        lum = arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114
        sat = np.max(arr, axis=2) - np.min(arr, axis=2)
        weight = 1.0 - (sat / 255.0)
        factor = 1.0 + vibrance_val * weight * 0.8
        lum_3d = lum[..., np.newaxis]
        arr = lum_3d + (arr - lum_3d) * factor[..., np.newaxis]
        arr = np.clip(arr, 0, 255)

    highlights_val = edits.get("highlights", 0.0)
    if highlights_val:
        lum = (arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114) / 255.0
        mask = np.clip(lum * 2 - 1, 0, 1)
        arr = np.clip(arr + mask[..., np.newaxis] * highlights_val * 80, 0, 255)

    shadows_val = edits.get("shadows", 0.0)
    if shadows_val:
        lum = (arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114) / 255.0
        mask = np.clip(1 - lum * 2, 0, 1)
        arr = np.clip(arr + mask[..., np.newaxis] * shadows_val * 80, 0, 255)

    fade_amt = edits.get("fade", 0.0)
    if fade_amt:
        bp = fade_amt * 64
        arr = bp + arr * (255 - bp) / 255

    definition_val = edits.get("definition", 0.0)
    if definition_val:
        from scipy.ndimage import gaussian_filter

        blurred = gaussian_filter(arr, sigma=10)
        detail = arr - blurred
        arr = np.clip(arr + detail * definition_val * 1.5, 0, 255)

    noise_reduction_val = edits.get("noise_reduction", 0.0)
    if noise_reduction_val:
        from scipy.ndimage import gaussian_filter

        sigma = noise_reduction_val * 3.0  # 0-3 pixel radius
        arr = gaussian_filter(arr, sigma=sigma)
        arr = np.clip(arr, 0, 255)

    vignette_amt = edits.get("vignette", 0.0)
    if vignette_amt:
        h_img, w_img = arr.shape[:2]
        cy_v, cx_v = h_img / 2.0, w_img / 2.0
        y_coord, x_coord = np.ogrid[:h_img, :w_img]
        dist = np.sqrt((x_coord - cx_v) ** 2 + (y_coord - cy_v) ** 2)
        max_dist = np.sqrt(cx_v**2 + cy_v**2)
        dist_norm = dist / max_dist
        falloff = 1.0 - vignette_amt * np.clip((dist_norm - 0.3) / 0.7, 0, 1)
        arr *= np.clip(falloff, 0, 1)[..., np.newaxis]

    grain_amt = edits.get("grain", 0.0)
    if grain_amt:
        rng = np.random.RandomState(42)
        noise = rng.normal(0, grain_amt * 50, arr.shape).astype(np.float32)
        arr = np.clip(arr + noise, 0, 255)

    return Image.fromarray(arr.astype(np.uint8))


def _apply_perspective(img: Image.Image, edits: dict) -> Image.Image:
    """Apply vertical/horizontal keystone correction. After numpy ops
    so the perspective transform sees the corrected pixels."""
    persp_v = edits.get("perspective_v", 0.0)
    persp_h = edits.get("perspective_h", 0.0)
    if not (persp_v or persp_h):
        return img
    w_img, h_img = img.size
    coeffs = _perspective_coefficients(w_img, h_img, persp_v, persp_h)
    return img.transform((w_img, h_img), Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC)


def _apply_redeye(img: Image.Image, edits: dict) -> Image.Image:
    """Apply red-eye removal at the user-marked points (if any)."""
    redeye_points = edits.get("redeye_points")
    if not redeye_points:
        return img
    return _apply_redeye_fix(img, redeye_points)


def _apply_edits(img: Image.Image, edits: dict) -> Image.Image:
    """Apply all edits in order: crop → orient → basic color → advanced
    numpy adjustments → perspective → redeye.

    M12.b: this used to be a 195-LOC function with eight inline phases
    plus a deeply nested numpy block. Each phase is now its own
    helper; ``_apply_edits`` is a flat pipeline that's easy to reorder
    or extend (a new tool just plugs in another helper).
    """
    img = _apply_crop(img, edits)
    img = _apply_orientation(img, edits)
    img = _apply_basic_color(img, edits)
    img = _apply_advanced(img, edits)
    img = _apply_perspective(img, edits)
    img = _apply_redeye(img, edits)
    return img


def _perspective_coefficients(w: int, h: int, vert: float, horiz: float) -> tuple[float, ...]:
    """Compute 8-tuple perspective transform coefficients.

    vert: -1..+1 vertical keystoning (tilt forward/backward)
    horiz: -1..+1 horizontal keystoning (tilt left/right)
    """
    import numpy as np

    # Source corners: top-left, top-right, bottom-right, bottom-left
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float64)

    # Destination corners shifted by perspective amounts
    vshift = vert * 0.15 * w  # vertical perspective shifts top corners horizontally
    hshift = horiz * 0.15 * h  # horizontal perspective shifts left corners vertically

    dst = np.array(
        [
            [0 + vshift, 0 + hshift],
            [w - vshift, 0 - hshift],
            [w + vshift, h - hshift],
            [0 - vshift, h + hshift],
        ],
        dtype=np.float64,
    )

    # Solve for 8 perspective coefficients
    # Based on the perspective transform equation:
    # x' = (a*x + b*y + c) / (g*x + h*y + 1)
    # y' = (d*x + e*y + f) / (g*x + h*y + 1)
    matrix = []
    for s, d in zip(src, dst, strict=True):
        matrix.append([d[0], d[1], 1, 0, 0, 0, -s[0] * d[0], -s[0] * d[1]])
        matrix.append([0, 0, 0, d[0], d[1], 1, -s[1] * d[0], -s[1] * d[1]])

    A = np.array(matrix, dtype=np.float64)
    B = np.array([s for pair in src for s in pair], dtype=np.float64)

    try:
        res = np.linalg.solve(A, B)
    except np.linalg.LinAlgError:
        return (1, 0, 0, 0, 1, 0, 0, 0)

    return tuple(res.tolist())


def _apply_redeye_fix(img: Image.Image, points: list[dict]) -> Image.Image:
    """Fix red-eye at specified normalized coordinates."""
    import numpy as np

    arr = np.array(img, dtype=np.float32)
    h, w = arr.shape[:2]

    for pt in points:
        px = int(pt["x"] * w)
        py = int(pt["y"] * h)
        radius = int(pt.get("radius", 0.03) * max(w, h))

        y_min = max(0, py - radius)
        y_max = min(h, py + radius)
        x_min = max(0, px - radius)
        x_max = min(w, px + radius)

        region = arr[y_min:y_max, x_min:x_max]
        if region.size == 0:
            continue

        r, g, b = region[..., 0], region[..., 1], region[..., 2]

        # Detect red-dominant pixels: R > G*1.4 and R > B*1.4 and R > 60
        red_mask = (r > g * 1.4) & (r > b * 1.4) & (r > 60)

        if red_mask.any():
            avg_gb = (g[red_mask] + b[red_mask]) / 2.0
            region[..., 0][red_mask] = avg_gb * 0.7

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
