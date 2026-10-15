#!/usr/bin/env bash
# Build the Python bpp server as a standalone binary using PyInstaller.
# The output goes to src-tauri/binaries/ where Tauri expects sidecars.
#
# Usage:
#   cd desktop && ./build-sidecar.sh
#
# Prerequisites:
#   pip install pyinstaller

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BINARIES_DIR="$SCRIPT_DIR/src-tauri/binaries"

# Detect target triple for Tauri sidecar naming
ARCH="$(uname -m)"
OS="$(uname -s)"

case "$OS" in
  Darwin)
    case "$ARCH" in
      arm64) TARGET="aarch64-apple-darwin" ;;
      x86_64) TARGET="x86_64-apple-darwin" ;;
      *) echo "Unsupported arch: $ARCH"; exit 1 ;;
    esac
    ;;
  Linux)
    case "$ARCH" in
      x86_64) TARGET="x86_64-unknown-linux-gnu" ;;
      aarch64) TARGET="aarch64-unknown-linux-gnu" ;;
      *) echo "Unsupported arch: $ARCH"; exit 1 ;;
    esac
    ;;
  *)
    echo "Unsupported OS: $OS"; exit 1
    ;;
esac

SIDECAR_NAME="bpp-server-${TARGET}"

echo "Building sidecar for target: $TARGET"
echo "Project root: $PROJECT_ROOT"

# Run PyInstaller from the project root
cd "$PROJECT_ROOT"

# Files PyInstaller's import analysis can't see, so they must be named:
#  - mediapipe's Tasks API loads mediapipe/tasks/c/libmediapipe.dylib via
#    ctypes at runtime (BlazeFace, landmarks, pose, segmentation). Without
#    it every MediaPipe detector fails to load in the app.
#  - bpp's last-resort face detector reads OpenCV's frontal-face Haar
#    cascade from cv2.data.haarcascades; OpenCV's hook doesn't collect it.
# Deliberately left out:
#  - dlib / face_recognition / face_recognition_models. face_recognition
#    loads the face_recognition_models weights at import time, and one of
#    them (the 68-point landmark model) was trained on the iBUG 300-W
#    dataset, whose creators ask that it not be used in commercial
#    products. Arkalogy doesn't redistribute those weights, so the app
#    leaves this optional face path out entirely; bpp treats it as not
#    installed. pip installs can still add it with bppicker[faces].
HAAR_CASCADE="$(python -c 'import cv2, os; print(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))')"
test -f "$HAAR_CASCADE" || { echo "Haar cascade not found at $HAAR_CASCADE"; exit 1; }

python -m PyInstaller \
  --onefile \
  --name "bpp-server" \
  --specpath "build/pyinstaller" \
  --hidden-import "bpp" \
  --hidden-import "bpp.web" \
  --hidden-import "bpp.web.app" \
  --hidden-import "bpp.db" \
  --hidden-import "flask" \
  --collect-all "bpp" \
  --collect-binaries "mediapipe" \
  --add-data "${HAAR_CASCADE}:cv2/data" \
  --exclude-module "face_recognition" \
  --exclude-module "face_recognition_models" \
  --exclude-module "dlib" \
  --exclude-module "_dlib_pybind11" \
  --noconfirm \
  --clean \
  --log-level WARN \
  desktop/sidecar_entry.py

# Copy to Tauri binaries dir with target-triple suffix
mkdir -p "$BINARIES_DIR"
cp "dist/bpp-server" "$BINARIES_DIR/$SIDECAR_NAME"
chmod +x "$BINARIES_DIR/$SIDECAR_NAME"

echo ""
echo "Sidecar built: $BINARIES_DIR/$SIDECAR_NAME"
echo "Size: $(du -sh "$BINARIES_DIR/$SIDECAR_NAME" | cut -f1)"
