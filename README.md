# Best Photo Picker

[![CI](https://github.com/Arkalogy/best-photo-picker/actions/workflows/ci.yml/badge.svg)](https://github.com/Arkalogy/best-photo-picker/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/bppicker.svg)](https://pypi.org/project/bppicker/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://github.com/Arkalogy/best-photo-picker/blob/main/LICENSE)
[![Python 3.11](https://img.shields.io/badge/python-3.11-green.svg)](https://www.python.org/downloads/)

**Drowning in 15,000 family photos?** Apple Photos and Google Photos bury your best shots in their feed. Best Photo Picker runs entirely on your machine, scores every photo on sharpness, lighting, faces, and composition, and lets you curate the perfect 50 in minutes once it's analyzed — no cloud, no subscription, no compromise.

It **auto-scores your whole library for curation without uploading anything**, **clusters the near-duplicates**, and lets you **boost specific people** so the final pick favors the faces that matter — your kid, for the grandparents.

<p align="center">
  <img src="https://raw.githubusercontent.com/Arkalogy/best-photo-picker/main/docs/screenshots/demo.gif" width="720" alt="Demo: a library of thousands of photos, open one child's photos, BPP Picks narrows them to her best shots, then the top pick opens with its sharpness, exposure, faces and composition scores">
  <br><sub>The recording and the screenshots show a sample library built from stock photos of models (<a href="https://www.pexels.com/license/">Pexels</a>) plus generated test images, not the author's family.</sub>
</p>

> **Try it — no photos needed** (needs Python 3.11: `brew install python@3.11` on a Mac, or use the [desktop app](#desktop-app-macos-apple-silicon)):
> ```bash
> pipx install --python python3.11 "bppicker[web]" && bpp demo
> ```
> Generates a sample library, runs the full UI locally, and deletes the sample library when you stop it (Ctrl+C). **The first analysis asks before downloading the on-device AI models (up to ~660 MB, mostly CLIP for visual search); they're cached, so that's a one-time download.** Your photos are never uploaded. bpp still makes a few small network calls of its own (a signed model-registry check at startup, an update check, map tiles) — all listed under [Privacy](#privacy).

*By Arkadiy Birger at [Arkalogy](https://arkalogy.com) — a PM-directed, AI-built product; the architectural decisions are captured in [`docs/adr/`](https://github.com/Arkalogy/best-photo-picker/tree/main/docs/adr). [Why & how it was built →](#about)*

More screenshots (faces, calendar, map, duplicates, the editor) and an annotated walkthrough of import → adjust → pick → export: **[docs/quickstart-gallery.md](https://github.com/Arkalogy/best-photo-picker/blob/main/docs/quickstart-gallery.md)**.

📣 **Found a bug or have a feature idea?** [Open an issue](https://github.com/Arkalogy/best-photo-picker/issues/new/choose) · [Start a discussion](https://github.com/Arkalogy/best-photo-picker/discussions) · [Contributing guide](https://github.com/Arkalogy/best-photo-picker/blob/main/CONTRIBUTING.md). Security issues: please use [private vulnerability reporting](https://github.com/Arkalogy/best-photo-picker/security/advisories/new), not a public issue.

## Why Best Photo Picker?

Where it fits next to what you already use:

- **Apple Photos / Google Photos** are great at storing and browsing. bpp ranks and curates — and runs alongside them.
- **Lightroom, Aftershoot, Excire** are built for photographers culling a shoot. bpp is built for a family library that spans years — and it's free and open source.
- **digiKam / Immich** organize and manage your collection. bpp answers one question: which fifty are the best of *this person*?

If you live in the Apple/Google ecosystem and your collection fits their feeds, those tools work great. **bpp exists for the case they're bad at: you have thousands of photos of a specific subject — your kid, your dog, last summer's trip — and you want to find the actual best fifty without uploading anything anywhere.**

## How it's built

Claude writes the code. I own the product and the architecture, and nothing ships without my sign-off. What keeps that honest:

- 5,500+ Python and 1,700+ UI tests run in CI on every pull request to `main`, plus 68 end-to-end tests that click through the real app on every release tag
- Key decisions written up in [`docs/adr/`](https://github.com/Arkalogy/best-photo-picker/tree/main/docs/adr)
- A signed, notarized Mac app and releases on PyPI
- I use it on my own photo library, and the bugs it finds become tests

## Known limits

- The pip install needs Python 3.11 exactly
- The Mac app is Apple Silicon only; Windows should work via pip but hasn't been tested end to end
- The first analysis downloads up to about 660 MB of models (it asks first); after that they're cached
- Face detection occasionally finds a face in a dog, a plate of food, or a flower bed. One click on **Not a face** fixes it.

## Features

- **Quality scoring** — sharpness, exposure, face detection (YuNet + MediaPipe BlazeFace + dlib, plus the opt-in research-only SCRFD detector), composition
- **Smart deduplication** — perceptual hashing (dHash + aHash) and CLIP semantic similarity
- **Temporal diversity** — per-day caps and monthly coverage for balanced selection
- **Face recognition** — automatic clustering, per-person smart albums, merge, dismiss, reassign, drag-to-fix mis-detected bboxes
- **Library management** — import by copying to managed library with SHA-256 dedup
- **Album system** — manual + 20 smart album types (person, time, score, duplicates, pets, etc.)
- **Interactive web UI** — real-time slider tuning, photo grid, lightbox, compare view, batch operations
- **Desktop app** — native macOS app via Tauri v2 (wraps the web UI in a native window)
- **Soft delete** — 30-day recovery, Recently Deleted album
- **Demo mode** — try instantly with generated sample photos
- **Privacy** — local-first, no telemetry, no analytics, and your photos are never uploaded. The few network calls bpp makes (a signed model-registry check each time bpp starts, model downloads (the app asks first), OpenStreetMap tiles when a map is on screen, an update check against GitHub Releases, optional LAN sharing, optional `pip install` of extra features) are all listed under [Privacy](#privacy) below.

## Quick Start

### Install

> **Requires Python 3.11** (3.12+ is not yet supported — it's pinned to match
> the desktop sidecar). On macOS, Homebrew ships a newer Python by default;
> install 3.11 with `brew install python@3.11` or
> [`pyenv`](https://github.com/pyenv/pyenv), or use the no-Python
> [desktop app](#desktop-app-macos-apple-silicon) below.

Recommended — [pipx](https://pipx.pypa.io/) keeps bpp in its own
environment and makes updating one command:

```bash
pipx install --python python3.11 "bppicker[web]"

# Update to the latest release later:
pipx upgrade bppicker
```

`--python python3.11` matters: pipx otherwise uses whatever Python it
was installed with (Homebrew's is newer), and the install ends with
"No matching distribution found for bppicker".

Plain pip works too:

```bash
python3.11 -m pip install "bppicker[web]"

# Optional: face recognition
pip install "bppicker[web,faces]"

# Optional: HEIC support
pip install "bppicker[heic]"
```

Two optional features can also be installed from inside the running
app: face recognition (offered when you start an analysis without
it) and AI object removal (offered by the editor's **Remove object**
tool). HEIC and RAW import need their pip extras.
To pre-install everything at once:
`pip install "bppicker[heic,faces,raw,inpaint]"`.

### Desktop app (macOS, Apple Silicon)

Prefer a dock icon over a terminal? Each release ships a standalone
Mac app. No Python, no terminal:

1. Download **[BestPhotoPicker-macOS-arm64.dmg](https://github.com/Arkalogy/best-photo-picker/releases/latest/download/BestPhotoPicker-macOS-arm64.dmg)** (always the latest release).
2. Double-click the `.dmg` and drag **Best Photo Picker** into **Applications**.
3. Open it from Applications or Spotlight.

The DMG and the app inside are signed with a Developer ID and notarized
by Apple, so it opens normally. The app comes with HEIC, RAW, face
recognition and AI object removal built in (it leaves out the
optional dlib face models) — see [License](#license) for what that bundles. To
update, download the newer `.dmg` the same way; your photo library and
settings live outside the app and carry over untouched.

> Apple Silicon (M1/M2/M3 and later) only. On an Intel Mac, or on
> Windows/Linux, use the `pipx install --python python3.11 "bppicker[web]"` path above.

### Try the Demo

```bash
bpp demo
```

Generates sample photos and launches the web UI — no configuration needed.

### Library Mode (Recommended)

```bash
# Start the photo management server (default library: ~/Pictures/BestPhotoPicker)
bpp serve

# Or specify a custom library path
bpp serve --library ~/Photos/2024
```

Your browser opens the app. If it doesn't, use the link the terminal prints: it carries a one-time key, so other programs on the machine can't open your library (pass `--open-loopback` to skip the key). Import folders, adjust scoring weights with sliders, and export your curated selection.

### CLI Mode (Batch Processing)

```bash
# One-shot: analyze + select best 50 photos
bpp run --input ~/Photos/MyKid --k 50 --out ~/curated --gallery

# Or step-by-step:
bpp analyze --input ~/Photos/MyKid --out ~/workdir
bpp select --workdir ~/workdir --k 50 --out ~/curated --gallery
```

## How It Works

```
Import → Analyze → Score → Deduplicate → Select → Export
```

1. **Import**: photos copied to managed library with SHA-256 dedup
2. **Analyze**: parallel feature extraction (blur, exposure, faces, composition) cached in SQLite
3. **Score**: weighted combination of sub-scores, tunable in real-time via sliders
4. **Deduplicate**: perceptual hash clustering removes burst duplicates; optional CLIP semantic dedup
5. **Select**: greedy selection with per-day caps and monthly coverage for temporal diversity
6. **Export**: copy/hardlink/symlink selected photos with optional HTML gallery

## Web UI

| Feature | Description |
|---------|-------------|
| Photo grid | Thumbnail grid with score badges, zoom control, sort & filter |
| Lightbox | Full-size viewer with keyboard navigation |
| Sliders | Real-time weight tuning (blur, exposure, face, composition) |
| BPP Picks | Sidebar sub-item picks best K photos across the full library; toolbar chip filters picks in the current album/view |
| Albums | Manual and smart albums (by time, score, person) |
| Faces | Auto-clustered face detection with merge, dismiss, and per-person albums |
| Batch ops | Multi-select with Cmd/Ctrl-click, bulk include/exclude/favorite |
| Import | Drag folders or archives into the library |
| Export | Copy or link selected photos to an output directory |

## Tuning Knobs

| Parameter | Default | Description |
|-----------|---------|-------------|
| `blur_weight` | 0.30 | Weight for sharpness in aggregate score |
| `exposure_weight` | 0.20 | Weight for exposure quality |
| `face_weight` | 0.35 | Weight for face detection & framing |
| `composition_weight` | 0.15 | Weight for composition (rule of thirds) |
| `max_long_side` | 1024 | Downscale images to this before analysis |
| `hash_distance_threshold` | 20 | dHash Hamming distance for "same" image within the burst time window |
| `global_hash_distance_threshold` | 10 | dHash Hamming distance for duplicates across the whole library |
| `time_window_seconds` | 30 | Max seconds between shots to count as one burst |
| `max_per_day` | 3 | Max selected photos per calendar day |
| `min_per_month` | 1 | Try to include at least 1 per month |

## Hardware

bpp runs all ML inference on CPU by default. ONNX-based models (CLIP semantic search, YOLOv11n pet detection,
the opt-in SCRFD face detector) can opt
into hardware acceleration through the `BPP_ONNX_PROVIDERS` env var:

```bash
# Apple Silicon (M1/M2/M3) — use the Apple Neural Engine via CoreML
BPP_ONNX_PROVIDERS="CoreMLExecutionProvider,CPUExecutionProvider" \
    bpp serve --library ~/Pictures/BestPhotoPicker

# NVIDIA GPU (Linux) — install onnxruntime-gpu first
pip uninstall -y onnxruntime && pip install onnxruntime-gpu
BPP_ONNX_PROVIDERS="CUDAExecutionProvider,CPUExecutionProvider" \
    bpp serve --library ~/Pictures/BestPhotoPicker

# Windows DirectML
BPP_ONNX_PROVIDERS="DmlExecutionProvider,CPUExecutionProvider" \
    bpp serve --library ~/Pictures/BestPhotoPicker
```

CPU is always appended as the final fallback so a missing provider
in your wheel falls through cleanly with a warning rather than
crashing. Ordering matters — providers are tried in the order listed.

### Tested vs supported

| Surface | Tested in CI | Supported | Notes |
|---|---|---|---|
| **macOS arm64** (M1/M2/M3) — CPU | no (tested locally on the dev Mac) | yes | Primary dev target. Tauri desktop app ships only this. |
| **Linux x86_64** — CPU | yes (`ubuntu-latest`) | yes | Default for PyPI install. |
| **Linux arm64** — CPU | no | yes | A Dockerfile ships, but the image has never been built in CI; runtime not exercised in CI. |
| **Windows** — CPU | no | yes | Code path exists; never run end-to-end. Reports welcome via GitHub issues. |
| **macOS arm64** — CoreMLExecutionProvider (ANE) | no | yes | Code path exists. Expected 5-10× face inference speedup; correctness depends on the specific ONNX graph and ONNX Runtime version. |
| **Linux x86_64** — CUDAExecutionProvider | no | yes | Requires `pip install onnxruntime-gpu` (replaces base `onnxruntime`). Free-tier CI doesn't have a GPU runner. |
| **Windows** — DmlExecutionProvider | no | yes | Same caveat as Windows CPU — code path exists, no end-to-end run. |

"Supported" means the code path accepts the configuration and
shouldn't crash; report bugs via GitHub Issues if it does. "Tested in
CI" means we run the full test suite on every pull request to `main` for that surface.
Anything between the two is on the user's risk surface — your
hardware, your driver stack, your call. We'd love to expand the
tested column as users report working setups; PRs welcome that add
matrix entries to `.github/workflows/ci.yml`.

### Throughput tuning

Face extraction is single-threaded per subprocess by default
(`_face_extract_workers=1`). On a 6,000-photo library that's ~50 min
on Apple Silicon (roughly 0.5 s/photo). Two config knobs let operators
trade RAM for time:

```yaml
# settings table or YAML config
_face_extract_workers: 4        # number of parallel workers
_face_extract_pool: process     # "process" | "thread"
```

Recommended combos:

| Setup | `workers` | `pool` | Expected throughput | Peak RAM |
|---|---|---|---|---|
| Default (safe everywhere) | 1 | thread | 1× baseline (~50 min / 6k photos) | ~700 MB |
| Power user, 16+ GB RAM | 4 | process | ~3-4× (~13 min / 6k photos) | ~3 GB |

_Measured on Apple M-series; results vary by photo resolution and system load._

The `process` pool is the only memory-safe parallel option — there is
a known thread-safety crash in the native face-landmark code when it
runs in a multi-threaded worker. ProcessPool gives each worker its own model
arena so any race is isolated. Trade-off: each worker pays a ~5-10 s
cold start for model loads, so it's only worthwhile for libraries with
hundreds of photos or more.

The native-thread-pool pinning (OMP_NUM_THREADS / OPENBLAS_NUM_THREADS
/ MKL_NUM_THREADS = 1) at `bpp/web/face_worker.py` and
`bpp/web/analyze_worker.py` module-import time applies regardless of
pool choice — required to prevent nested-thread oversubscription that
would crash the child silently. See `tests/test_face_thread_safety.py`
for the full regression guard suite.

## Share on your local network

Open the library on a phone, tablet, or another computer connected
to the same Wi-Fi. Two-step flow: phone scans QR, owner approves on
Mac. After that, the device is trusted and reconnects silently.

### Setup

1. Open **Settings → Share** on the host machine
2. Toggle **LAN sharing** on. **First time only**: if the server
   started with sharing off, it bound to loopback only and the
   toggle replies **"Restart required"**. Quit and relaunch bpp once
   — the next start picks up the persisted flag and binds the LAN
   interface. Subsequent toggles flip instantly.
3. The pane fills in with a share URL, a scannable QR code, and a
   live Devices list

### Pairing a new device

1. On phone: point the camera at the QR code → tap the link
2. Phone shows a **"Waiting for the owner to approve…"** page
3. On Mac: a new card appears under **Pending requests** (e.g.
   "iPhone — 192.168.1.42") within a few seconds
4. Click **Approve** → phone auto-reloads into the full app
5. The phone moves into **Trusted devices** and stays there. Future
   visits skip the approval step entirely.

### Revoking access

- **Trusted devices → Revoke** kicks the device immediately. The
  phone shows "Access revoked" within ~3 seconds.
- **Pending requests → Block** rejects a request you didn't make
  (e.g. someone else scanned the QR by accident).
- The phone never silently re-requests access. To ask again, the
  phone user has to actively tap **"Request access again"** on
  the revoked page.

### Revoking the share link itself

If the URL leaked entirely (someone screenshotted it, you sent it
to the wrong person):

- **Settings → Share → Revoke** rotates the share token. All
  current URLs stop working immediately. A new URL + QR replaces
  the old one. Already-trusted devices keep working until you also
  revoke them individually.

### What you can see at a glance

- **Pending requests** with badge count when phones are waiting
- **Trusted devices** with last-seen timestamps and a "previously
  revoked" tag if you'd kicked them before
- **Recent access** — last 10 share-link auth events (deduped per
  10-minute window)

### Security model

⚠️ Anyone on your local network whose device the owner has approved
can browse your photos. The token is in the URL; the LAN itself
is treated as a trusted boundary. **Only enable on networks you
trust** — home Wi-Fi, not coffee shops or hotel networks. This is
LAN-only: there is no cloud relay and no internet exposure.

For the threat model, schema, and OSS extension hooks (HTTPS,
ProxyFix, future user accounts), see [docs/security.md](https://github.com/Arkalogy/best-photo-picker/blob/main/docs/security.md).

## CLI Reference

`--host` defaults to `127.0.0.1` (loopback only) when LAN sharing is
off, and `0.0.0.0` when sharing is on at startup. The canonical way
to enable LAN access is **Settings → Share**, not the flag — pass
`--host` explicitly only for reverse-proxy / Docker setups.

A double-clickable launcher template ships at
`launch.command.template`; copy to `launch.command`, mark executable
(`chmod +x`), and Finder-double-click to start the server + Tauri
window with the documented default library path. Override via
`BPP_LIBRARY` env var.

A `Dockerfile` ships at the repo root for self-hosters who want to
build their own image (`docker build -t bppicker .`). No prebuilt
image is published and the Dockerfile is not built in CI, so treat
it as untested. It's a multi-stage build that installs the
`[web,faces,heic]` extras, and it keeps downloaded models and
model-license acceptances on the `/data` volume
(`BPP_CACHE_DIR=/data/.cache/bpp`, `XDG_CONFIG_HOME=/data/.config`)
so they survive re-creating the container. The server binds
`0.0.0.0` inside the container (needed for port mapping); publish
only to host loopback:
`docker run -p 127.0.0.1:5001:5001 -v /path/to/library:/data bppicker`.
The image sets `BPP_TRUSTED_PROXIES` to the Docker bridge ranges, so
requests through that mapping are treated as the owner — publishing
to all interfaces (`-p 5001:5001`) can give other machines on your
network owner access. LAN sharing is not supported from a container.
Behind a reverse proxy, `X-Forwarded-For` is honored only if you also
set `behind_proxy: true` in the config, and the proxy's host name must
be listed in `BPP_TRUSTED_HOSTS` (the server answers only to its own
addresses); see
[docs/security.md](https://github.com/Arkalogy/best-photo-picker/blob/main/docs/security.md).

```
bpp serve [--library PATH] [--host HOST] [--port 5001] [--no-browser] [--open-loopback] [--config FILE] [--debug]
bpp demo [--port 5001] [--no-browser] [--open-loopback] [--keep]
bpp web [--input DIR] [--workdir DIR] [--host HOST] [--port 5001] [--no-browser] [--open-loopback]
bpp analyze --input DIR --out WORKDIR [--config FILE] [--max N] [--workers K]
bpp select --workdir DIR --k N --out DIR [--copy|--hardlink|--symlink] [--gallery]
bpp run --input DIR --k N --out DIR [all options from analyze + select]
bpp pick LIBRARY [--top N] [--boost-face NAME]... [--out DIR] [--json|--paths-only]
                 [--quality original|high|medium|low] [--dry-run]
bpp model {list|accept|accept-attribution|accepted|use-context|byom|remove|registry}
                 — text-mode parity with the GUI click-through for
                   restricted-license models. Use when running headless,
                   in CI, or scripting the acceptance flow.
                   `accept-attribution` accepts every built-in
                   Apache 2.0 / BSD-style model in one step.
bpp db restore-backup --library PATH [--previous] [--yes] [--accept-stale] [--force]

Global: --seed INT (default 42), --extensions STR (default "jpg,jpeg,png,heic")
```

## Privacy

This tool is **local-first** — your photos never leave your machine
for analysis, scoring, or selection. There is no telemetry and no
cloud account.

Network usage is limited to the following. None of them uploads your
photos; the only one that reveals anything about them is map tiles
(approximate locations of geotagged photos):
- **Model registry check** (automatic, every time bpp starts) — each
  bpp process (every CLI command, including `bpp --version`, each
  server start, and — on macOS and Windows — each background worker
  process the server spawns) fetches a small signed file from
  `arkalogy.github.io/bppicker-registry/registry.json` so license or
  status changes to a model (for example, blocking one after a
  takedown) can reach you without a new release. It is a plain GET
  with a fixed User-Agent and no library data; it isn't cached on
  disk (only the number of the newest file applied is kept, so an
  older one can't be replayed), times out after 10 seconds, and falls
  back to the built-in list on any failure. Turn it off by setting the environment
  variable `BPP_DISABLE_REMOTE_REGISTRY=1` (see
  [docs/configuration.md](https://github.com/Arkalogy/best-photo-picker/blob/main/docs/configuration.md)); there is no
  Settings toggle.
- **Model downloads** (once per model, cached afterwards) — face
  detection, CLIP, etc. In the app, nothing downloads until you
  approve a prompt that lists each model, its size and its host; the
  CLI (`bpp analyze`, `bpp run`) downloads any missing model it needs
  without asking. There is no switch that turns downloads off; to
  stay offline, copy already-downloaded models into the models
  directory (`BPP_MODELS_DIR`). Hosts: Hugging Face (`huggingface.co`
  — third-party re-hosts of CLIP and, if you accept it, the research-only SCRFD detector), Google
  (`storage.googleapis.com`, MediaPipe), the OpenCV opencv_zoo on
  `media.githubusercontent.com` (YuNet, SFace), OpenAI's CDN for the
  CLIP tokenizer vocab (`openaipublic.azureedge.net`), GitHub
  releases for Ultralytics/YOLO (`github.com/ultralytics`), and, only
  for opt-in features, `raw.githubusercontent.com/notAI-tech`
  (NudeNet), `github.com/deepinsight` (an opt-in research-only face embedder,
  after its license click-through) and `github.com/enesmsahin` (a
  re-host of the LaMa inpainting weights, fetched the first time you
  use **Remove object**). Downloads may redirect to those hosts' CDNs.
  Every model file bpp downloads is SHA-256 pinned and verified
  before it's loaded, including the LaMa weights (`big-lama.pt`).
  (The dlib face-recognition weights arrive differently: as the
  `face_recognition_models` package from PyPI, via `bppicker[faces]`.
  The desktop app doesn't include them.)
- **Runtime dependency installs** (only when you click Install —
  the face-recognition offer before an analysis, or the editor's
  **Remove object** tool) — runs `pip install` against PyPI. TLS-only
  trust; transitive dependencies are not hash-pinned (same trust
  level as `pip install` from a terminal).
- **Update check** (default on) — about 5 seconds after the app's
  page loads, bpp asks api.github.com whether a newer release exists
  (cached for 24 hours). Sends a User-Agent containing your current
  version and nothing else — no library data, no telemetry. Turn it
  off with **Settings → App → Check for updates on startup**; that
  switch is stored in the browser (or the desktop app's window), so
  it applies to that browser only. 256 KB response cap enforced in
  bpp — oversized responses are refused before any bytes are parsed
- **Map tiles** (when a map is on screen) — opening the Map view, or
  opening a geotagged photo in the full-size viewer (its small
  location map), makes your browser fetch OpenStreetMap tiles around
  the photo's location. Each tile request reveals approximate photo
  coordinates to OSM's tile servers. The grid and other views don't
  load tiles.
- **LAN sharing** (only if you enable it) — phone access over your
  local Wi-Fi; never traverses the internet

All photo analysis runs on your machine. Originals are never uploaded
to any server, and soft-delete keeps recoverable copies for 30 days.

## Development Setup

### Prerequisites

- **Python 3.11** (CI tests this version only — 3.12+ is not validated). On macOS, Homebrew ships 3.14 by default — install 3.11 via `brew install python@3.11` or `pyenv install 3.11`.
- **Node.js 20+** and npm (for desktop app only)
- **Rust** (stable, for desktop app only) — install via [rustup.rs](https://rustup.rs)
- **CMake + C++ compiler** (for dlib face recognition) — macOS: `brew install cmake`

### 1. Python environment

```bash
git clone https://github.com/Arkalogy/best-photo-picker.git
cd best-photo-picker

python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,web,faces]"

# Optional: HEIC support
pip install -e ".[heic]"
```

### 2. Run the web server

```bash
bpp serve --library ~/Pictures/BestPhotoPicker --no-browser
# Open the link it prints, or add --open-loopback and use http://127.0.0.1:5001
```

### 3. Desktop app (Tauri v2)

The desktop app wraps the web UI in a native macOS window via Tauri.

```bash
# Install JS dependencies
cd desktop && npm install && cd ..

# Dev mode (starts Python server + Tauri window)
cd desktop && npm run dev
```

For a standalone `.app` bundle (launchable from Finder/Spotlight):

```bash
# Build sidecar (PyInstaller-bundled Python server)
cd desktop && ./build-sidecar.sh

# Build .app + .dmg
npm run build
# Output: desktop/src-tauri/target/release/bundle/
```

The sidecar binary lands at `desktop/src-tauri/binaries/bpp-server-{target-triple}` (e.g., `bpp-server-aarch64-apple-darwin`).

> **Note:** A build you make yourself isn't signed with an Apple Developer ID, so Gatekeeper will block it the first time. To open it: right-click → Open → Open (once). The release DMG and the app inside are signed with a Developer ID and notarized by Apple, so they open normally.

### 4. Lint and test

```bash
# Python
ruff check . && ruff format --check .
pytest -v                        # full pytest suite

# Frontend (JS) — requires `npm install` once
npm run lint                     # ESLint + auto-generated globals allowlist
npm run format:check             # Prettier (scoped to dev-tool JS)
npm run typecheck                # tsc --noEmit on @ts-check files
npm run test:js                  # Vitest + jsdom unit tests under tests-js/

# End-to-end smoke tests (Playwright)
npx playwright install chromium  # one-time browser install
# (start `bpp serve` first, then:)
npm run test:e2e                 # full Playwright suite under tests-e2e/
```

### Project structure

```
bpp/
  cli.py, commands.py       # CLI entry points
  scoring/                  # Blur, exposure, face (YuNet/BlazeFace/dlib), composition
  dedupe/                   # Perceptual hash + CLIP semantic dedup
  selection/                # Greedy chooser with diversity constraints
  db/                       # SQLite schema, migrations, CRUD
  web/                      # Flask app, blueprint-per-feature, background workers
    static/js/              # Vanilla JS SPA (modules under static/js/modules/)
    templates/index.html    # Single-page HTML shell
desktop/
  src-tauri/                # Tauri v2 Rust app
  build-sidecar.sh          # PyInstaller sidecar build
  scripts/dev-macos.sh      # Dev launcher
```

### Git workflow

- Feature and fix branches merge into **`main`** through pull requests; releases are tagged from `main`.
- CI (lint, pytest and the JS unit tests, Python 3.11) runs on every pull request to `main`. It is not a required check.
- The Playwright end-to-end suite runs in CI on each release tag (`v*.*.*`) and on manual dispatch.

## Installing on a New Machine

### Desktop app

Download the signed, notarized [BestPhotoPicker-macOS-arm64.dmg](https://github.com/Arkalogy/best-photo-picker/releases/latest/download/BestPhotoPicker-macOS-arm64.dmg) and drag it to Applications (see [Desktop app](#desktop-app-macos-apple-silicon) above). To build the app yourself instead, see [Development Setup](#3-desktop-app-tauri-v2).

### Transferring your photo library

The library folder (default `~/Pictures/BestPhotoPicker/`) contains everything:

```
BestPhotoPicker/
  photos/    # your imported photos (organized by batch)
  data/      # SQLite database (scores, faces, albums, settings)
  cache/     # thumbnails and face crops (regenerated automatically)
  logs/      # server logs
```

To move to a new machine:

1. Copy the entire `BestPhotoPicker/` folder to the new machine (same path or any location)
2. Install the app on the new machine
3. Start it: `bpp serve --library ~/Pictures/BestPhotoPicker`

The app automatically detects that file paths have changed (different username, different OS path) and **remaps all photos by SHA-256 hash** on first startup. Your scores, albums, face clusters, favorites — everything is preserved. The `cache/` folder can be skipped to save transfer time; thumbnails regenerate on demand.

## Troubleshooting

**`dlib` fails to install** — Requires CMake and a C++ compiler. macOS: `brew install cmake`. Ubuntu: `sudo apt install cmake build-essential`.

**HEIC photos not recognized** — `pip install "bppicker[heic]"`. Linux may also need `libheif-dev`.

**Port 5001 in use** — `bpp serve --port 8080`, or check what's holding it: `lsof -i :5001`.

**`ImportError: cv2`-related errors after upgrading or installing extras** — `bpp` declares `opencv-python-headless` as its base OpenCV. `bppicker[faces]` (mediapipe) pulls in `opencv-contrib-python` and `bppicker[inpaint]` (simple-lama-inpainting) pulls in `opencv-python`. All three install into the same `cv2` namespace and the last-installed one wins on disk. With matching version numbers it's harmless, but a future minor bump in any of them can produce hard-to-diagnose import-order errors. If you hit one, reset to the headless variant: `pip uninstall opencv-python opencv-contrib-python && pip install --force-reinstall opencv-python-headless`.

**Desktop app won't reopen from dock** — Quit fully (Cmd+Q), then relaunch. The Tauri app handles dock-click reopen.

**App fails to start after an upgrade / failed migration** — every
schema migration writes a verified `data/photopicker.db.backup`
before mutating anything. To roll back the live DB to that
snapshot:

```bash
# Stop any running server first.
pkill -f 'bpp serve'

# Restore. The current DB is moved aside with a timestamped
# suffix so nothing is destroyed. Use --previous to roll back
# to the older `.backup.prev` snapshot if `.backup` is also
# bad.
bpp db restore-backup --library ~/Pictures/BestPhotoPicker
```

The command verifies the backup's integrity before swapping it in
and refuses if the backup is corrupt. Once the app starts and your
library looks correct, you can delete the moved-aside files
manually.

**Downgrading bpp to an older version** — bpp's schema migrations
are *forward-only*. Once a newer bpp version migrates your library
DB, an older bpp will refuse to open it (or crash on missing
columns). To roll back:

1. Install the older bpp version (`pip install bppicker==X.Y.Z` or
   reinstall the matching bundle).
2. Restore the pre-migration snapshot:
   ```bash
   pkill -f 'bpp serve'
   bpp db restore-backup --library ~/Pictures/BestPhotoPicker --previous
   ```
3. Start the older bpp.

The `--previous` flag restores `.backup.prev` — the snapshot taken
before the most recent backup rotation, which is what bpp wrote
just before the upgrade migration ran. This works for **single-
version rollbacks** (upgraded once, want to go back). If you've
already started the new version multiple times or run multi-step
migrations, `.backup.prev` may have been overwritten — at that
point the only path back is restoring from your own external
backup of the library directory.

## Changelog

User-facing release notes live in [CHANGELOG.md](https://github.com/Arkalogy/best-photo-picker/blob/main/CHANGELOG.md).

## Support

If you find this useful, [star the repo](https://github.com/Arkalogy/best-photo-picker) and spread the word. Bug reports and PRs welcome.

- [☕ Buy us a coffee](https://buymeacoffee.com/arkalogy) — fuels the bug-fix afternoons
- [📬 Get in touch](mailto:support@arkalogy.com)

## About

I'm **Arkadiy Birger**, and I build at [**Arkalogy**](https://arkalogy.com). I started bpp to triage my own family photo library — thousands of shots of my kid — without sending any of it to someone else's cloud. The options I found either lived in someone's data center or were built for professional photographers, not for "find the 50 best photos of my kid for the grandparents." If you have the same problem and this helps, that's the win.

This codebase was built with **Claude (Anthropic)** — implementation and line-by-line code review are both AI-driven, not human. I work as the product owner and architect: directing the work, approving every commit, and capturing load-bearing architectural decisions in [`docs/adr/`](https://github.com/Arkalogy/best-photo-picker/tree/main/docs/adr). It's a deliberate test of how far a PM-led, AI-built product can go while staying disciplined — hence the registries, the test gates, the legal posture in [`MODEL_POLICY.md`](https://github.com/Arkalogy/best-photo-picker/blob/main/MODEL_POLICY.md), and the ADRs. It's also a tool I actually use.

## License

MIT — see [LICENSE](https://github.com/Arkalogy/best-photo-picker/blob/main/LICENSE). See [NOTICE.txt](https://github.com/Arkalogy/best-photo-picker/blob/main/NOTICE.txt) for the full third-party license breakdown.

bpp itself is MIT. The Python packages a default `pip install bppicker` pulls in declare permissive licenses (MIT / Apache-2.0 / BSD / MIT-CMU), except `tqdm`, which is MPL-2.0 AND MIT. Some of those wheels also carry native libraries under other licenses: the OpenCV wheel bundles FFmpeg (LGPL-2.1), and its macOS build also includes libx265 (GPL-2.0).

For pip installs, the copyleft Python packages are opt-in extras that are never installed without you asking for them:

- **`bppicker[heic]`** pulls in `pillow-heif` (GPLv2 binary wheel, due to bundled x265; libheif and libde265 are LGPL-3.0; Python source is BSD-3) for HEIC support.
- **`bppicker[raw]`** pulls in `rawpy` (MIT, links to LibRaw which is LGPL-2.1) for camera-RAW support.
- **`bppicker[inpaint]`** pulls in `simple-lama-inpainting` (Apache 2.0) for AI object removal, plus `torch` and `torchvision` (both BSD-3) transitively. The LaMa model weights (from the [advimman/lama](https://github.com/advimman/lama) research project; bpp's model registry classifies them as research / non-commercial) are downloaded on first use from a third-party re-host, the `simple-lama-inpainting` project's GitHub release — review their terms before redistributing a derived work.

**The Mac app is different:** it's built with the extras above installed, so the DMG itself contains the Python runtime, pillow-heif with x265 (GPL-2.0), rawpy with LibRaw (LGPL-2.1), FFmpeg (LGPL-2.1), the other native libraries OpenCV's macOS wheel bundles, and PyTorch (it leaves out dlib and its `face_recognition_models` weights). Those components keep their own licenses inside the app. Each release attaches an inventory of the app's Python side — every Python package with its license and native libraries, the Python runtime, the PyInstaller bootloader, and the files actually packed into the app (the Rust window shell isn't covered) — plus the exact HEIC/RAW sources, and Arkalogy offers the source of any GPL/LGPL component in that inventory on request — see the written offer in [NOTICE.txt](https://github.com/Arkalogy/best-photo-picker/blob/main/NOTICE.txt).

The Ultralytics YOLOv11n weights downloaded for pet detection are AGPL-3.0; we use the ONNX-exported weights only (no Ultralytics Python code runs in your process). See [NOTICE.txt](https://github.com/Arkalogy/best-photo-picker/blob/main/NOTICE.txt) for the full picture.

### Models, commercial use, and bppicker's monetization stance

Bppicker bundles one model file: the BlazeFace short-range face detector (Apache-2.0, from Google's MediaPipe) ships inside the package. The Mac app also carries OpenCV's frontal-face Haar cascade, which comes inside the OpenCV wheel (Intel's BSD-style OpenCV license). Every other model is downloaded by your local installation on first use from a third-party host (not from Arkalogy; the hosts are listed under [Privacy](#privacy)), subject to upstream model terms — bppicker does not redistribute or relicense them.

Arkalogy will not monetize, sell, or market Best Photo Picker for commercial workflows. Because the source code is MIT-licensed, third parties may still use it commercially; restricted-model access is separately controlled by model-specific terms and app-level gates (commercial-use gate, hard-block, click-through acknowledgment). Optional third-party models may have separate licenses, including non-commercial or research-only restrictions — commercial users must select commercial-safe models or provide their own licensed weights.

Every model bppicker runs has an entry in its model registry, and that entry is the only place the model's license is written; the app reads license labels from it. The built-in models a default analysis uses (SFace, YuNet, Google's MediaPipe models and OpenCV's Haar cascade) are permissively licensed with attribution terms, so the first time you analyze, the dialog lists them with their licenses and asks you to accept them together with one checkbox (`bpp model accept-attribution` does the same from the command line). Until you do, face detection, people, expressions and composition scoring stay off.

For face recognition specifically, bppicker's default embedder (SFace) is permissively licensed and commercial-use-safe. Other face embedders are available behind an opt-in click-through; some are research-only / non-commercial and are blocked when you select "Commercial" as your use context, unless you state in the click-through that you hold separate rights to them. The Bring-Your-Own-Model path lets you point bppicker at a local ONNX file you have your own rights to.
