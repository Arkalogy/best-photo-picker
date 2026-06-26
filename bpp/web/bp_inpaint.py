"""AI inpainting + auto-straighten endpoints.

Extracted from bp_photos_manage.py during the v0.1 cleanup. Both
endpoints sit between "photo editing" and "AI features" — they take
a single photo, run a CV/AI op (LaMa inpaint or Hough-line skew
detect), and return the result. Splitting them out lets the
optional [inpaint] extra carry its own logical home in the
codebase, and keeps bp_photos_manage focused on lifecycle +
non-destructive edits.

Both endpoints @requires_local_app — only the desktop / Tauri owner
can run them; LAN-paired phones don't get to mutate photos.
"""

from __future__ import annotations

import os

from flask import Blueprint, Response, jsonify, request

from bpp.utils.logging import get_logger
from bpp.web.share import requires_local_app
from bpp.web.state import get_ctx

log = get_logger(__name__)

bp = Blueprint("inpaint", __name__)


@bp.get("/api/v1/inpaint/status")
def api_inpaint_status() -> tuple[Response, int]:
    """Check if inpainting is available."""
    from bpp.ai.inpainting import is_available

    return jsonify({"available": is_available()}), 200


@bp.post("/api/v1/photos/<int:photo_id>/inpaint")
@requires_local_app
def api_inpaint(photo_id: int) -> tuple[Response, int]:
    """Apply AI object removal to a photo.

    Accepts JSON: {mask: "<base64 PNG>"}
    Returns the inpainted image as PNG.
    """
    import base64

    from bpp.ai.inpainting import is_available

    # P7: use the BppError handler instead of ad-hoc jsonify-error.
    from bpp.errors import (
        FeatureUnavailableError,
        NotFoundError,
        ValidationError,
    )

    if not is_available():
        raise FeatureUnavailableError(
            "Inpainting not available. Install with: pip install bppicker[inpaint]",
        )

    ctx = get_ctx()
    conn = ctx.get_conn()
    photo = conn.execute(
        "SELECT id, filepath, sha256 FROM photos WHERE id = ?", (photo_id,)
    ).fetchone()
    if not photo:
        raise NotFoundError("Photo not found", photo_id=photo_id)

    data = request.get_json(silent=True) or {}
    mask_b64 = data.get("mask", "")
    if not mask_b64:
        raise ValidationError("mask (base64 PNG) required")

    try:
        mask_bytes = base64.b64decode(mask_b64)
    except Exception as e:
        # User sees a clean validation message; the server log gets the
        # base64 decode error detail via diagnostic_message.
        raise ValidationError(
            "Invalid base64 mask",
            diagnostic_message=f"base64 decode failed: {e}",
        ) from e

    # Load the source image
    filepath = photo["filepath"]
    if not os.path.isfile(filepath):
        # diagnostic_message carries the path; user response stays clean.
        raise NotFoundError(
            "Source file not found on disk",
            diagnostic_message=f"missing source file: {filepath}",
            photo_id=photo_id,
        )

    try:
        from io import BytesIO

        from PIL import Image

        from bpp.ai.inpainting import inpaint

        # Context managers release the file / buffer handles before
        # inpaint() runs (which holds the converted Image objects).
        with Image.open(filepath) as img_in:
            image = img_in.convert("RGB")
        with Image.open(BytesIO(mask_bytes)) as mask_in:
            mask = mask_in.convert("L")
        result = inpaint(image, mask)

        # Save as non-destructive edit variant keyed by content SHA-256
        # so the cache survives batch renames (filepath changes, content doesn't)
        if ctx.thumbs:
            from bpp.constants import HASH_PREFIX_LEN, PHOTO_CACHE_SUFFIX_INPAINTED

            sha = photo["sha256"]
            cache_key = sha[:HASH_PREFIX_LEN] if sha else ctx.thumbs.get_hash(filepath)
            cache_dir = ctx.thumbs.cache_dir
            inpaint_path = os.path.join(cache_dir, f"{cache_key}{PHOTO_CACHE_SUFFIX_INPAINTED}.png")
            result.save(inpaint_path, format="PNG")

        # Return the inpainted image as base64 PNG
        buf = BytesIO()
        result.save(buf, format="PNG")
        result_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        return jsonify({"image": result_b64, "photo_id": photo_id}), 200

    except ValueError as e:
        # P7: raise into the handler so the response shape matches the
        # other failure branches in this endpoint. diagnostic_message
        # carries the original ValueError text; user-facing stays clean.
        raise ValidationError(
            "Invalid inpainting parameters",
            diagnostic_message=f"inpaint validation error photo_id={photo_id}: {e}",
            photo_id=photo_id,
        ) from e
    except Exception as e:
        # Anything else — inpainting model crashed, OOM, etc. Surface
        # as a generic 500 (BppError default). diagnostic_message
        # captures the actual exception for the log; user sees the
        # generic "Inpainting failed" message.
        # RuntimeError used to be its own branch pre-P7 review; now
        # collapsed into the catch-all since it's a subclass of
        # Exception and was treated the same way.
        from bpp.errors import BppError

        raise BppError(
            "Inpainting failed",
            diagnostic_message=f"inpaint runtime error photo_id={photo_id}: {e}",
            photo_id=photo_id,
        ) from e


@bp.get("/api/v1/photos/<int:photo_id>/auto_straighten")
@requires_local_app
def api_auto_straighten(photo_id: int) -> tuple[Response, int]:
    """Detect the dominant rotation angle for auto-straighten.

    Uses Hough line detection on a downscaled grayscale image to estimate
    the skew angle. Returns angle in degrees (negative = rotate CW).
    """
    import math

    import cv2
    import numpy as np

    ctx = get_ctx()
    conn = ctx.get_conn()
    from bpp.errors import BppError, NotFoundError

    photo = conn.execute("SELECT filepath FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if not photo:
        raise NotFoundError("Photo not found", photo_id=photo_id)

    filepath = photo["filepath"]
    if not os.path.isfile(filepath):
        raise NotFoundError("File not found on disk", photo_id=photo_id)

    try:
        from PIL import Image

        with Image.open(filepath) as img_in:
            img = img_in.convert("RGB")
        # Downscale for fast analysis (max 512px on long side)
        img.thumbnail((512, 512), Image.LANCZOS)
        arr = np.array(img)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(gray, 50, 150)

        lines = cv2.HoughLinesP(
            edges, 1, math.pi / 180, threshold=50, minLineLength=30, maxLineGap=10
        )
        if lines is None or len(lines) == 0:
            return jsonify({"angle": 0.0, "confidence": "low"}), 200

        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            if x2 == x1:
                continue
            angle_deg = math.degrees(math.atan2(y2 - y1, x2 - x1))
            # Normalize to [-45, 45] range (ignore steep vertical lines)
            if abs(angle_deg) > 45:
                angle_deg = angle_deg - math.copysign(90, angle_deg)
            angles.append(angle_deg)

        if not angles:
            return jsonify({"angle": 0.0, "confidence": "low"}), 200

        angle = float(np.median(angles))
        # Round to 1 decimal, clamp to [-10, 10] for safety
        angle = max(-10.0, min(10.0, round(angle, 1)))
        # Negate: positive angle = tilt right = needs CCW correction
        angle = -angle
        confidence = "high" if len(angles) >= 10 else "low"
        return jsonify({"angle": angle, "confidence": confidence}), 200

    except Exception as e:
        # The BppError handler logs the diagnostic + traceback;
        # diagnostic_message carries the original exception text to
        # the server log, user_message stays generic.
        raise BppError(
            "Auto-straighten failed",
            user_message="Auto-straighten failed",
            diagnostic_message=f"auto-straighten error for photo {photo_id}: {e!s}",
            photo_id=photo_id,
        ) from e
