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

python -m PyInstaller \
  --onefile \
  --name "bpp-server" \
  --hidden-import "bpp" \
  --hidden-import "bpp.web" \
  --hidden-import "bpp.web.app" \
  --hidden-import "bpp.db" \
  --hidden-import "flask" \
  --collect-all "bpp" \
  --noconfirm \
  --clean \
  --log-level WARN \
  bpp/cli.py

# Copy to Tauri binaries dir with target-triple suffix
mkdir -p "$BINARIES_DIR"
cp "dist/bpp-server" "$BINARIES_DIR/$SIDECAR_NAME"
chmod +x "$BINARIES_DIR/$SIDECAR_NAME"

echo ""
echo "Sidecar built: $BINARIES_DIR/$SIDECAR_NAME"
echo "Size: $(du -sh "$BINARIES_DIR/$SIDECAR_NAME" | cut -f1)"
