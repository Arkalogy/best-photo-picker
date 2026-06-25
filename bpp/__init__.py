"""Best Photo Picker: Local-first photo curation tool."""

# Cap OpenCV's decode size before cv2 is imported anywhere. OpenCV reads
# OPENCV_IO_MAX_IMAGE_PIXELS once at C-extension load time, so it MUST be
# set before the first `import cv2` in the process — this package __init__
# runs before any `bpp.*` submodule (and thus before their cv2 imports),
# in the server, the CLI, and every multiprocessing-spawn child. Without
# this, the PIL MAX_IMAGE_PIXELS pin in bpp/scoring/aggregate.py does NOT
# protect the cv2 decode path (cv2.imread is tried first), leaving a
# decompression-bomb hole: a valid ~50000x50000 image decodes fine in cv2 and
# OOMs the analyze/phash worker. 200M matches the PIL pin.
import os as _os

_os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(200_000_000))

__version__ = "0.1.0"
APP_NAME = "Best Photo Picker"

# Eagerly import the model registry so the Batch-3 download chokepoint
# (item 18 of the legal-posture rollout) installs before any BPP code
# can import a third-party package with auto-download behavior. The
# chokepoint patches the upstream downloader if the package is
# already loaded, and registers a meta-path hook for packages loaded
# later. Importing it from the top-level bpp package ensures any
# ``from bpp...`` import path triggers installation.
import bpp.registry  # noqa: E402, F401  — side-effecting import
