#!/usr/bin/env bash
#
# Multi-pass Playwright e2e runner.
#
# The e2e suite spans three library shapes that can't coexist in one
# server process:
#   - synthetic : ~12 procedural photos (scripts/setup_e2e_library.py).
#                 The default pass — every spec that isn't library-pinned.
#   - empty     : a brand-new empty library so onboarding fires on
#                 first_run. Targets the `@empty`-tagged specs.
#   - demo      : the real demo library (~3,842 photos). Targets the
#                 `@demo`-tagged specs. LOCAL ONLY — CI has no demo lib.
#
# Each pass starts its own server on :5001, waits for it, runs the
# matching Playwright grep, then tears the server down before the next.
#
# Usage:
#   scripts/run_e2e.sh [synthetic|empty|demo|ci|all]
#
#   ci  = synthetic + empty   (what .github/workflows/e2e.yml runs)
#   all = synthetic + empty + demo
#
# Env overrides:
#   BPP_E2E_LIBRARY        synthetic library dir (default: mktemp)
#   BPP_DEMO_LIBRARY       demo library dir (default: ~/Pictures/BestPhotoPickerDemo)
#   BPP_ACCEPTANCE_LOG_PATH  isolated acceptance log (default: mktemp).
#                          Keeps the registry-picker accept tests
#                          deterministic and off a dev's real ~/.config/bpp.
set -euo pipefail

MODE="${1:-synthetic}"
PORT=5001
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Resolve the bpp + python binaries — local dev uses .venv/bin/*; CI
# installs into system Python (no venv). Prefer the venv, fall back to
# PATH (python3, since `python` may be absent on macOS/CI).
if [ -x .venv/bin/bpp ]; then
  BPP_BIN=".venv/bin/bpp"
else
  BPP_BIN="$(command -v bpp)"
fi
if [ -x .venv/bin/python ]; then
  PY_BIN=".venv/bin/python"
else
  PY_BIN="$(command -v python3 || command -v python)"
fi

# Isolate the acceptance log unless the caller already pinned one.
if [ -z "${BPP_ACCEPTANCE_LOG_PATH:-}" ]; then
  BPP_ACCEPTANCE_LOG_PATH="$(mktemp -t bpp_e2e_accept.XXXXXX)"
fi
export BPP_ACCEPTANCE_LOG_PATH
echo "Acceptance log: $BPP_ACCEPTANCE_LOG_PATH"

# Server log: honor BPP_E2E_SERVER_LOG if set (CI pins it to a known path
# so the failure-artifact upload can find it — `mktemp -t` honors $TMPDIR,
# which on some runners isn't /tmp, so a bare mktemp can land where the
# artifact glob won't match). Falls back to mktemp for local runs.
SERVER_LOG="${BPP_E2E_SERVER_LOG:-$(mktemp -t bpp_e2e_server.XXXXXX)}"
echo "Server log: $SERVER_LOG"
SERVER_PID=""

cleanup() {
  stop_server
  pkill -f "bpp serve" 2>/dev/null || true
}
trap cleanup EXIT

start_server() {
  local lib="$1"
  echo "── Starting server on :$PORT  (library: $lib)"
  pkill -f "bpp serve" 2>/dev/null || true
  sleep 1
  nohup "$BPP_BIN" serve --library "$lib" --no-browser >"$SERVER_LOG" 2>&1 &
  SERVER_PID=$!
  local i
  for i in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
      echo "── Server up after ${i}s (pid $SERVER_PID)"
      return 0
    fi
    sleep 1
  done
  echo "::error::server failed to start within 60s"
  tail -100 "$SERVER_LOG"
  return 1
}

stop_server() {
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
  fi
  pkill -f "bpp serve" 2>/dev/null || true
  sleep 1
}

# ── Passes ──────────────────────────────────────────────────────────

pass_synthetic() {
  local lib="${BPP_E2E_LIBRARY:-$(mktemp -d)/bpp_e2e_library}"
  echo "════ PASS: synthetic  ($lib)"
  "$PY_BIN" scripts/setup_e2e_library.py --library "$lib" --reset
  start_server "$lib"
  # Everything except the library-pinned specs (they self-skip here
  # anyway, but skipping the grep keeps the run focused + faster).
  npx playwright test --grep-invert "@demo|@empty" --reporter=list
  stop_server
}

pass_empty() {
  local lib
  lib="$(mktemp -d)/bpp_e2e_empty"
  mkdir -p "$lib"
  echo "════ PASS: empty  ($lib)"
  start_server "$lib"
  npx playwright test --grep "@empty" --reporter=list
  stop_server
}

pass_demo() {
  local lib="${BPP_DEMO_LIBRARY:-$HOME/Pictures/BestPhotoPickerDemo}"
  echo "════ PASS: demo  ($lib)"
  if [ ! -d "$lib" ]; then
    echo "::error::demo library not found at $lib — set BPP_DEMO_LIBRARY"
    return 1
  fi
  start_server "$lib"
  npx playwright test --grep "@demo" --reporter=list
  stop_server
}

case "$MODE" in
  synthetic) pass_synthetic ;;
  empty)     pass_empty ;;
  demo)      pass_demo ;;
  ci)        pass_synthetic; pass_empty ;;
  all)       pass_synthetic; pass_empty; pass_demo ;;
  *)
    echo "Unknown mode: $MODE" >&2
    echo "Usage: $0 [synthetic|empty|demo|ci|all]" >&2
    exit 2
    ;;
esac

echo "✔ e2e mode '$MODE' complete."
