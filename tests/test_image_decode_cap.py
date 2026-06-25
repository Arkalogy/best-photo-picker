"""Regression guard for the cv2 decompression-bomb cap (audit finding D1).

`bpp/__init__.py` sets ``OPENCV_IO_MAX_IMAGE_PIXELS`` before any ``import
cv2`` so a crafted huge image can't OOM the analyze/phash workers. The PIL
``MAX_IMAGE_PIXELS`` pin in ``bpp/scoring/aggregate.py`` does NOT cover the
cv2 path (cv2.imread is tried first), so this env var is the load-bearing
guard for the cv2 decoders. Both tests run in subprocesses because OpenCV
reads the limit once at C-extension load time.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap


def test_importing_bpp_pins_opencv_pixel_cap():
    """Importing bpp sets the cap when it isn't already in the env."""
    env = {k: v for k, v in os.environ.items() if k != "OPENCV_IO_MAX_IMAGE_PIXELS"}
    out = subprocess.run(
        [sys.executable, "-c", "import bpp, os; print(os.environ['OPENCV_IO_MAX_IMAGE_PIXELS'])"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert out.returncode == 0, f"import bpp failed: {out.stderr}"
    assert out.stdout.strip() == "200000000", (
        f"expected the cv2 pixel cap to be pinned to 200000000, got {out.stdout.strip()!r}"
    )


def test_read_image_for_scoring_blocks_image_over_cap(tmp_path):
    """The production decode path returns None (skips the photo) for an
    image above the cv2 cap, instead of OOMing.

    Over the cap, cv2.imread raises cv2.error rather than returning None;
    read_image_for_scoring's broad except turns that into a logged None,
    and the cv2 raise bypasses the PIL fallback (so PIL doesn't then
    decode the oversized image either). This exercises the real worker
    entry-point, not cv2 in isolation."""
    try:
        import cv2
        import numpy as np
    except ImportError:  # pragma: no cover - cv2 is a core dep
        import pytest

        pytest.skip("cv2/numpy required")

    # 1500x1500 = 2.25M pixels; written under the normal (large) cap.
    img_path = tmp_path / "over_cap.png"
    cv2.imwrite(str(img_path), np.zeros((1500, 1500, 3), dtype=np.uint8))

    # Child process pins a 1M-pixel cap BEFORE importing cv2 / bpp.
    code = textwrap.dedent(
        f"""
        import os
        os.environ["OPENCV_IO_MAX_IMAGE_PIXELS"] = "1000000"
        from bpp.scoring.aggregate import read_image_for_scoring
        result = read_image_for_scoring({str(img_path)!r})
        print("NONE" if result is None else "DECODED")
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, f"subprocess failed: {out.stderr}"
    assert out.stdout.strip() == "NONE", (
        "read_image_for_scoring decoded a 2.25MP image under a 1MP cap instead of "
        f"skipping it — the cv2 pixel cap isn't protecting the worker; got "
        f"{out.stdout.strip()!r}\n{out.stderr}"
    )
