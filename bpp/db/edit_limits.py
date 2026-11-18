"""The ranges saved photo edits may take, applied on save and on read.

Edit values are stored in the library database, which can come from
someone else. Rendering trusts them: a crop box outside the picture makes
the output larger than the photo, a huge noise-reduction value makes the
blur kernel as large. Every value is therefore brought inside the range
its editor control has (``EDITOR_SLIDERS`` in editor-constants.mjs and
the straighten / perspective sliders), and anything that isn't a number
becomes the control's default.
"""

from __future__ import annotations

import math
from typing import Any

#: key -> (min, max, default). Mirrors the editor's sliders; a test keeps
#: the two in step.
EDIT_RANGES: dict[str, tuple[float, float, float]] = {
    "exposure": (-2.0, 2.0, 0.0),
    "brilliance": (-1.0, 1.0, 0.0),
    "brightness": (0.2, 2.0, 1.0),
    "contrast": (0.5, 2.0, 1.0),
    "highlights": (-1.0, 1.0, 0.0),
    "shadows": (-1.0, 1.0, 0.0),
    "black_point": (-1.0, 1.0, 0.0),
    "saturation": (0.0, 2.0, 1.0),
    "vibrance": (-1.0, 1.0, 0.0),
    "warmth": (-1.0, 1.0, 0.0),
    "tint": (-1.0, 1.0, 0.0),
    "sharpness": (0.0, 3.0, 1.0),
    "definition": (-1.0, 1.0, 0.0),
    "noise_reduction": (0.0, 1.0, 0.0),
    "vignette": (0.0, 1.0, 0.0),
    "fade": (0.0, 1.0, 0.0),
    "grain": (0.0, 1.0, 0.0),
    "straighten": (-45.0, 45.0, 0.0),
    "perspective_v": (-1.0, 1.0, 0.0),
    "perspective_h": (-1.0, 1.0, 0.0),
}

_ROTATIONS = (0, 90, 180, 270)
_CROP_KEYS = ("crop_x", "crop_y", "crop_w", "crop_h")
#: The smallest crop side kept, as a fraction of the picture.
_MIN_CROP = 0.001


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _crop(edits: dict) -> dict[str, float | None]:
    """The crop box, inside the picture; no crop when any side is unusable."""
    none: dict[str, float | None] = dict.fromkeys(_CROP_KEYS)
    x, y, w, h = (_number(edits.get(k)) for k in _CROP_KEYS)
    if x is None or y is None or w is None or h is None:
        return none
    x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
    w, h = min(w, 1.0 - x), min(h, 1.0 - y)
    if w < _MIN_CROP or h < _MIN_CROP:
        return none
    return {"crop_x": x, "crop_y": y, "crop_w": w, "crop_h": h}


def clean_edits(edits: dict) -> dict:
    """A copy of *edits* with every value inside its range. Keys that are
    absent stay absent (callers apply their own defaults); other keys
    (red-eye points, the auto-enhance flag) pass through."""
    out = dict(edits)
    for key, (low, high, default) in EDIT_RANGES.items():
        if key in edits:
            value = _number(edits[key])
            out[key] = default if value is None else min(max(value, low), high)
    if any(k in edits for k in _CROP_KEYS):
        out.update(_crop(edits))
    if "rotation" in edits:
        out["rotation"] = edits["rotation"] if edits["rotation"] in _ROTATIONS else 0
    for key in ("flip_h", "flip_v"):
        if key in edits:
            out[key] = (
                bool(edits[key])
                if _number(edits[key]) is not None or isinstance(edits[key], bool)
                else False
            )
    if "filter_name" in edits and not isinstance(edits["filter_name"], str):
        out["filter_name"] = None
    return out
