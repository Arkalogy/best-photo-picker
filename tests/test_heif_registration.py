"""Regression tests for HEIF/HEIC registration in the web app.

The web app must call ``pillow_heif.register_heif_opener()`` at boot
when pillow_heif is available — otherwise ``PIL.Image.open()`` on
``.HEIC`` source files raises ``UnidentifiedImageError`` and the
``/photo/<hash>`` route returns a broken image to the lightbox.

This was a real bug: register_heif_opener() lived only in lazy paths
(face crop coords, scoring), so /photo serving failed silently for
HEIC photos until the user inspected the image and saw a broken icon.
"""

from __future__ import annotations

import os

import pytest


def _heic_available() -> bool:
    try:
        import pillow_heif  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not _heic_available(), reason="pillow_heif not installed")
def test_create_app_registers_heif_opener(tmp_path):
    """After create_app(), PIL must recognize HEIF as a registered format."""
    from PIL import Image

    from bpp.web.app import create_app

    workdir = str(tmp_path / "workdir")
    os.makedirs(workdir)
    app = create_app(workdir=workdir)
    app.config["TESTING"] = True

    assert "HEIF" in Image.OPEN, (
        "HEIF opener not registered after create_app() — /photo will fail "
        "for HEIC source files. Boot-time register_heif_opener() missing."
    )


@pytest.mark.skipif(not _heic_available(), reason="pillow_heif not installed")
def test_pillow_version_supports_pillow_heif():
    """pillow_heif 1.x sets ``self._mode`` and relies on Pillow 10+'s
    ``mode`` property. With Pillow <10, _open() succeeds but
    ``self.mode`` stays empty, so PIL raises 'not identified by this
    driver'. This test catches the silent version skew that broke HEIC
    on a previously-working install.
    """
    import PIL
    import pillow_heif

    pillow_major = int(PIL.__version__.split(".")[0])
    heif_major = int(pillow_heif.__version__.split(".")[0])

    if heif_major >= 1:
        assert pillow_major >= 10, (
            f"pillow_heif {pillow_heif.__version__} requires Pillow >= 10 "
            f"(have {PIL.__version__}). HEIC files will fail to open with "
            "'not identified by this driver'."
        )
