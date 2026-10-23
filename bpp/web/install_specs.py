"""What the in-app installer may install: each key's pip specs.

The packages each key's feature loads through (held until a restart once
pip runs) are in :mod:`bpp.scoring.install_hold`; a test keeps the two key
sets equal.
"""

from __future__ import annotations

# Whitelist of pip packages that can be installed at runtime.
# Maps short key → list of pip install specs. Using direct package names
# (instead of `bppicker[extra]`) so this works for users running from a
# local editable install (`pip install -e .`), where bppicker's extras
# don't resolve against PyPI.
INSTALLABLE_PACKAGES: dict[str, list[str]] = {
    "faces": [
        "face_recognition>=1.3",
        "scipy>=1.10",
        "mediapipe>=0.10",
        "setuptools<81",
        "ai-edge-litert>=2.1; sys_platform != 'darwin' or platform_machine != 'x86_64'",
    ],
    "onnxruntime": ["onnxruntime"],
    # Pin to 0.1.0 (matches the pyproject extra). 0.1.1 pins
    # pillow<11; 0.1.2 pins pillow<10 (breaks HEIC). 0.1.0 has no
    # upper bound so pip resolves cleanly against our existing
    # Pillow >= 10 install. With this pin, --no-deps is no longer
    # required, so the runtime installer can pull torch /
    # torchvision / numpy on a base venv that doesn't already
    # have them via [faces] / [clip].
    "inpaint": ["simple-lama-inpainting==0.1.0"],
}
