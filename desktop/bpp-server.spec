# PyInstaller build of the desktop app's bpp server. Run through
# desktop/build-sidecar.sh, which checks the build prerequisites first.
#
# Layout: a one-folder build wrapped in a background-only helper app,
# bpp-server.app, which the Tauri bundle carries at
# Contents/Helpers/bpp-server.app. Nothing is unpacked when the server
# starts. (Up to 0.2.3 the server was a one-file build that unpacked
# about 1 GB into a new temp folder on every launch, and macOS checked
# those fresh copies each time. A start took 30-45 s and could outlast
# the app's wait.)
#
# Signing: with BPP_CODESIGN_IDENTITY set (the release workflow sets it
# to the Developer ID), PyInstaller signs every collected binary and the
# helper app with that identity, the hardened runtime and a secure
# timestamp, as notarization requires. The Tauri bundler does not sign
# what it copies into Contents/Helpers. Without it, local builds are
# ad-hoc signed. The entitlements are the app's own file, unchanged.

import os
import re
from pathlib import Path

import cv2
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 (SPECPATH is set by PyInstaller)
DESKTOP = ROOT / "desktop"

# Modules bpp loads indirectly (import_gated / importlib) are invisible
# to PyInstaller's import scan and must be named here, or the app fails at
# their first use (re-clustering and the face-model switch need scipy's
# linkage). tests/test_sidecar_hidden_imports.py keeps this list in step
# with the code, and build-sidecar.sh checks that each scipy one imports.
HIDDEN_IMPORTS = [
    "bpp",
    "bpp.web",
    "bpp.web.app",
    "bpp.db",
    "flask",
    "scipy.cluster.hierarchy",
]

# dlib / face_recognition / face_recognition_models are left out on
# purpose. face_recognition loads the face_recognition_models weights at
# import time, and one of them (the 68-point landmark model) was trained
# on the iBUG 300-W dataset, whose creators ask that it not be used in
# commercial products. Arkalogy doesn't redistribute those weights, so the
# app leaves this optional face path out entirely; bpp treats it as not
# installed. pip installs can still add it with bppicker[faces].
EXCLUDES = ["face_recognition", "face_recognition_models", "dlib", "_dlib_pybind11"]

# Files import analysis can't see:
#  - mediapipe's Tasks API loads mediapipe/tasks/c/libmediapipe.dylib via
#    ctypes at runtime (BlazeFace, landmarks, pose, segmentation).
#  - bpp's last-resort face detector reads OpenCV's frontal-face Haar
#    cascade from cv2.data.haarcascades; OpenCV's hook doesn't collect it.
HAAR_CASCADE = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")

VERSION = re.search(
    r'^__version__ = "([^"]+)"', (ROOT / "bpp" / "__init__.py").read_text(), re.M
).group(1)
IDENTITY = os.environ.get("BPP_CODESIGN_IDENTITY") or None
ENTITLEMENTS = str(DESKTOP / "src-tauri" / "Entitlements.plist")

bpp_datas, bpp_binaries, bpp_hidden = collect_all("bpp")

a = Analysis(  # noqa: F821
    [str(DESKTOP / "sidecar_entry.py")],
    pathex=[],
    binaries=bpp_binaries + collect_dynamic_libs("mediapipe"),
    datas=bpp_datas + [(HAAR_CASCADE, "cv2/data")],
    hiddenimports=HIDDEN_IMPORTS + bpp_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="bpp-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed binaries can't be signed for notarization
    console=True,  # a server, not a window; implies LSBackgroundOnly
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=IDENTITY,
    entitlements_file=ENTITLEMENTS,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="bpp-server",
)
app = BUNDLE(  # noqa: F821
    coll,
    name="bpp-server.app",
    icon=str(DESKTOP / "src-tauri" / "icons" / "icon.icns"),
    bundle_identifier="com.arkalogy.bpp.server",
    version=VERSION,
    info_plist={
        # Started by the app, never by the user: no Dock icon, no menu bar.
        "LSBackgroundOnly": True,
        "CFBundleDisplayName": "Best Photo Picker server",
    },
    # BUNDLE signs the helper app and re-signs every binary in it with the
    # EXE's codesign_identity and entitlements_file.
)
