"""Auto-enhance (magic pop) algorithm for photos."""

from __future__ import annotations

from PIL import Image, ImageEnhance, ImageStat

from bpp.constants import (
    ENHANCE_DOWNSAMPLE_SIZE,
    ENHANCE_SHARPNESS_DEFAULT,
    JPEG_QUALITY_FULL,
)


def auto_enhance(filepath: str) -> dict:
    """Analyze an image and compute optimal enhancement parameters.

    Returns dict with brightness, contrast, saturation, sharpness factors.
    All factors are multipliers where 1.0 = no change.
    """
    with Image.open(filepath) as img:
        img = img.convert("RGB")
        # Downsample for faster stats
        if max(img.size) > ENHANCE_DOWNSAMPLE_SIZE:
            img.thumbnail((ENHANCE_DOWNSAMPLE_SIZE, ENHANCE_DOWNSAMPLE_SIZE), Image.LANCZOS)
        stat = ImageStat.Stat(img)

    mean_lum = sum(stat.mean) / 3.0  # 0-255

    # Brightness: target ~128 luminance midpoint
    # Dark photos get a bigger boost, bright photos stay close to 1.0
    if mean_lum < 10:
        brightness = 1.6
    elif mean_lum < 80:
        brightness = 1.0 + (120 - mean_lum) / 200.0
    elif mean_lum > 200:
        brightness = max(0.9, 1.0 - (mean_lum - 200) / 300.0)
    else:
        brightness = 1.0 + (120 - mean_lum) / 400.0

    # Contrast: based on stddev — low stddev = flat image, needs contrast
    mean_std = sum(stat.stddev) / 3.0
    if mean_std < 30:
        contrast = 1.15 + (30 - mean_std) / 100.0
    elif mean_std < 50:
        contrast = 1.05 + (50 - mean_std) / 200.0
    else:
        contrast = 1.0

    # Saturation: measure color spread (max channel - min channel mean)
    channel_spread = max(stat.mean) - min(stat.mean)
    if channel_spread < 5:
        # Very desaturated / gray
        saturation = 1.25
    elif channel_spread < 15:
        saturation = 1.15
    elif channel_spread < 30:
        saturation = 1.08
    else:
        saturation = 1.0

    # Sharpness: always a light boost
    sharpness = ENHANCE_SHARPNESS_DEFAULT

    # Clamp all values
    brightness = max(0.8, min(2.0, brightness))
    contrast = max(1.0, min(1.5, contrast))
    saturation = max(1.0, min(1.5, saturation))
    sharpness = max(1.0, min(1.5, sharpness))

    return {
        "brightness": round(brightness, 3),
        "contrast": round(contrast, 3),
        "saturation": round(saturation, 3),
        "sharpness": round(sharpness, 3),
    }


def apply_enhance(src: str, dest: str, params: dict) -> None:
    """Apply enhancement parameters to an image and save result."""
    with Image.open(src) as img:
        img = img.convert("RGB")

        if params.get("brightness", 1.0) != 1.0:
            img = ImageEnhance.Brightness(img).enhance(params["brightness"])
        if params.get("contrast", 1.0) != 1.0:
            img = ImageEnhance.Contrast(img).enhance(params["contrast"])
        if params.get("saturation", 1.0) != 1.0:
            img = ImageEnhance.Color(img).enhance(params["saturation"])
        if params.get("sharpness", 1.0) != 1.0:
            img = ImageEnhance.Sharpness(img).enhance(params["sharpness"])

        img.save(dest, "JPEG", quality=JPEG_QUALITY_FULL)
