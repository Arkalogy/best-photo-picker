#!/usr/bin/env bash
# Build the desktop app's Python bpp server with PyInstaller.
#
# Output: src-tauri/helpers/bpp-server.app, a background-only helper app
# that tauri.conf.json copies to Contents/Helpers/ in the Mac app. The
# build itself is desktop/bpp-server.spec (layout, hidden imports,
# excluded modules and signing are explained there).
#
# Usage:
#   cd desktop && ./build-sidecar.sh
#
# Prerequisites:
#   pip install ".[web,heic,faces,raw,inpaint]" pyinstaller
#
# Signing: set BPP_CODESIGN_IDENTITY to a Developer ID identity in the
# keychain to sign for distribution; without it the build is ad-hoc
# signed, which is enough to run it on this Mac.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
HELPERS_DIR="$SCRIPT_DIR/src-tauri/helpers"

# The desktop app ships for macOS only; other platforms use the pip install.
if [ "$(uname -s)" != "Darwin" ]; then
  echo "The desktop app's server builds on macOS only."
  exit 1
fi

echo "Building the server helper app ($(uname -m))"
echo "Project root: $PROJECT_ROOT"

cd "$PROJECT_ROOT"

# A hidden import PyInstaller cannot find is only a warning, so check
# that each one bpp needs at runtime imports here first: a build without
# it must fail, not ship. tests/test_sidecar_hidden_imports.py checks that
# every scipy hidden import in the spec is checked here.
python -c "import scipy.cluster.hierarchy" || { echo "scipy is required for the app build (re-clustering)"; exit 1; }
python -c 'import cv2, os, sys; sys.exit(0 if os.path.isfile(os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")) else 1)' \
  || { echo "OpenCV's Haar cascade is missing (the last-resort face detector needs it)"; exit 1; }

python -m PyInstaller desktop/bpp-server.spec \
  --distpath dist \
  --workpath build/pyinstaller \
  --noconfirm \
  --clean \
  --log-level WARN

# Stage the helper app where tauri.conf.json picks it up. ditto keeps the
# bundle's symlinks and signatures intact.
rm -rf "$HELPERS_DIR/bpp-server.app"
mkdir -p "$HELPERS_DIR"
ditto "dist/bpp-server.app" "$HELPERS_DIR/bpp-server.app"
codesign --verify --deep --strict "$HELPERS_DIR/bpp-server.app"

echo ""
echo "Server built: $HELPERS_DIR/bpp-server.app"
echo "Size: $(du -sh "$HELPERS_DIR/bpp-server.app" | cut -f1)"
