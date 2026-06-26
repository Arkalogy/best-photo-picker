#!/usr/bin/env bash
# Dev launcher for macOS — wraps the debug binary in a .app bundle
# so macOS dock shows "Best Photo Picker" instead of "bpp-desktop".
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DESKTOP_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_DIR="$(dirname "$DESKTOP_DIR")"
TAURI_DIR="$DESKTOP_DIR/src-tauri"
APP_NAME="Best Photo Picker"
APP_PATH="/tmp/${APP_NAME}.app"
BINARY_NAME="bpp-desktop"
BINARY_PATH="$TAURI_DIR/target/debug/$BINARY_NAME"

cleanup() {
  # Kill the server we started (if we started it)
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# 1. Start Python server if port 5001 is not in use
if ! lsof -i:5001 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[dev] Starting Python server on :5001..."
  pkill -f 'bpp serve' 2>/dev/null || true
  sleep 0.5
  cd "$PROJECT_DIR"
  .venv/bin/bpp serve --library ~/Pictures/BestPhotoPicker --no-browser &
  SERVER_PID=$!
  sleep 2
else
  echo "[dev] Server already running on :5001"
fi

# 2. Build the Cargo binary
echo "[dev] Building Tauri binary..."
cd "$TAURI_DIR"
cargo build 2>&1

# 3. Create a minimal .app bundle
rm -rf "$APP_PATH"
mkdir -p "$APP_PATH/Contents/MacOS"
mkdir -p "$APP_PATH/Contents/Resources"

cat > "$APP_PATH/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Best Photo Picker</string>
    <key>CFBundleDisplayName</key>
    <string>Best Photo Picker</string>
    <key>CFBundleIdentifier</key>
    <string>com.arkalogy.bpp.dev</string>
    <key>CFBundleVersion</key>
    <string>0.1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>bpp-desktop</string>
    <key>CFBundleIconFile</key>
    <string>icon</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSSupportsAutomaticGraphicsSwitching</key>
    <true/>
</dict>
</plist>
PLIST

# Symlink the binary and icon
ln -sf "$BINARY_PATH" "$APP_PATH/Contents/MacOS/$BINARY_NAME"
cp "$TAURI_DIR/icons/icon.icns" "$APP_PATH/Contents/Resources/icon.icns"

# 4. Launch the .app (foreground — script waits for it to exit)
echo "[dev] Launching ${APP_NAME}..."
open -W "$APP_PATH"
