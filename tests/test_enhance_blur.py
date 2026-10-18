"""The "definition" and "noise reduction" edits blur without scipy (only
the faces extra installs it), each colour channel on its own: colours stay
true (the old scipy call also blurred across R, G and B, dulling them)."""

from __future__ import annotations

import numpy as np
import pytest

from bpp.web.photo_edits import _gaussian_filter

scipy_ndimage = pytest.importorskip("scipy.ndimage")


@pytest.mark.parametrize("sigma", [10, 3.0, 1.5, 1.1, 0.3])
@pytest.mark.parametrize("shape", [(64, 64, 3), (97, 131, 3), (40, 50, 4)])
def test_the_blur_matches_scipy(shape, sigma):
    arr = (np.random.default_rng(0).random(shape) * 255).astype(np.float32)
    expected = scipy_ndimage.gaussian_filter(arr, sigma=(sigma, sigma, 0))
    np.testing.assert_allclose(_gaussian_filter(arr, sigma), expected, atol=1e-3)


def test_the_edits_never_import_scipy():
    import pathlib
    import re

    import bpp.web.photo_edits as edits

    source = pathlib.Path(edits.__file__).read_text()
    assert not re.search(r"^\s*(from scipy|import scipy)|import_gated\(.scipy", source, re.M)


def test_a_saturated_colour_stays_saturated():
    red = np.zeros((32, 32, 3), np.float32)
    red[..., 0] = 255
    out = _gaussian_filter(red, 3.0)
    assert np.allclose(out[..., 0], 255, atol=1e-3)
    assert np.allclose(out[..., 1:], 0, atol=1e-3)
