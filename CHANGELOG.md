# Changelog

All notable changes to Best Photo Picker are documented here. The
format follows [Keep a Changelog](https://keepachangelog.com/) and
this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] — 2026-06-23

First public release. **Schema version: 34.**

### Added

- Manual Face-draw flow for missed faces, with identity stickiness and duplicate guards.
- Activity-tab Copy-log button.
- End-to-end fixture sentinel guard for mutating Playwright helpers.
- Photo-deletion lifecycle hook registry for plugins.

### Changed

- Vectorized CLIP semantic deduplication to cut large-library Pick-count recompute latency.
- Sidebar folders and per-album filters persist across refresh and album switches.
- Toolbar Pick count commits on blur, Enter, and arrow-step changes instead of every keystroke.
- Recently Deleted and Hidden photo APIs are paginated.
- Storage health checks pause while the tab is hidden.
- Recompute reports elapsed time in stats and user-visible toasts for noticeable runs.

### Fixed

- Boot restore waits for the album list before reopening the saved album.
- Update checker surfaces real network/GitHub errors instead of reporting falsely up to date.
- Face, batch-rename, compare, pet, logging, parser, and IO hardening from the pre-release audit pass.
- Release-gate fixes: Activity-log filtering, Prettier formatting, filter persistence, face-purge counts, deletion-hook affected IDs, CLIP-dedup peak memory, cv2 decode-size cap, and download-chokepoint test isolation.

### Highlights

- **Local-first photo curation.** Score every photo on sharpness,
  lighting, faces, and composition, then pick the best ones to
  share. No cloud account, no telemetry — your photo bytes never
  leave your machine for analysis, scoring, or selection. (See the
  README → Privacy section for the full list of network calls bpp
  does make: model downloads on first analyze, OpenStreetMap tiles
  when you open the Map view, default-on update check against
  GitHub Releases.)
- **Flask web UI** with a vanilla-JS single-page app, plus an
  optional native macOS desktop wrapper via Tauri v2.

### Features

- **Scoring pipeline** — blur (Laplacian + LoG), exposure, faces
  (SCRFD + BlazeFace + dlib + Haar fallback with confidence-weighted
  NMS), composition (rule-of-thirds + leading lines + saliency).
- **Deduplication** — dual perceptual hashing (dHash + aHash) plus
  optional CLIP-embedding semantic similarity.
- **Face clustering** — group photos by who's in them, name people,
  boost selections by named person. When a face is mis-localized
  (e.g. a busy pattern triggers a phantom detection while the real
  face goes unboxed), click the wrong bbox in the lightbox and drag
  it onto the real face — the embedding is re-extracted and the
  person is re-matched automatically.
- **Pet detection** (cats / dogs) via YOLOv11n.
- **Near-duplicate clustering** — burst shots and visually similar
  photos grouped by perceptual hash distance (hamming ≤ 8, 60-second
  time window). The Duplicates smart album lets you step through groups
  and keep the best.
- **Live Photo sidecar filtering** — iPhone `_N` sidecar files are
  detected and excluded from all user-facing counts and albums so
  library sizes reflect real photos, not metadata duplicates.
- **Smart albums** — manual + 16 auto-curated types (person, time,
  score, duplicates, pets, screenshots, documents, edited photos,
  recents, hidden, deleted, "needs review", and more).
- **Library management** — import via copy or symlink, batch
  rename, soft delete with auto-purge after 30 days, restore from
  backup.
- **Editing** — non-destructive crops, rotation, exposure / contrast
  / saturation adjustments, plus optional AI inpainting (LaMa) for
  removing objects.
- **Export** — original / hardlink / symlink / re-encode at chosen
  quality, with optional XMP sidecar and JSON manifest.
- **Search & navigation** — full-text tag search, calendar view,
  map view (geo-tagged photos on OpenStreetMap tiles), "On This
  Day," memories.

### LAN sharing & security

- **Optional LAN sharing** — toggle in Settings → Share. The owner
  approves each new device via QR-code pairing; trusted devices
  reconnect silently across restarts.
- **Per-boot session token** for the local Tauri / browser, plus a
  persistent share token for paired LAN devices. All API endpoints
  are gated.
- Loopback-only bind by default; flips to all-interfaces only when
  the share toggle is on at startup.
- Token redaction in logs (active + rotated + in-memory ring
  buffer), including bare-token assignments in subprocess
  tracebacks and home-directory paths in PII-sensitive log lines.
- HTTP-only by default — designed for trusted LAN. For untrusted
  networks, run behind a TLS-terminating reverse proxy and set
  `BPP_TRUSTED_PROXIES`.

### ML & data integrity

- All bundled and downloaded model weights are SHA-256 pinned and
  verified on cache hit.
- **Atomic backups** before every mutating schema migration, with
  WAL/SHM siblings, integrity verification of the copy, and
  timestamped quarantine of corrupt copies. Restore CLI refuses to
  overwrite the live DB from a corrupt source and refuses to
  proceed when a stranded quarantine sibling is present.
- Forward-only schema migrations; the recovery path is
  `bpp db restore-backup`.

### Extensibility

- Public registries for face detectors, face embedders, scoring
  fields, smart-album types, background workers, and config
  schema.
- Optional plugin auto-loading via setuptools entry-points
  (`bpp.plugins`). **Off by default** — opt in with
  `BPP_ENABLE_PLUGINS=1`. See `docs/plugins.md` for the trust
  contract and authoring guide.

### Optional features (opt-in extras)

- `bppicker[heic]` — HEIC / HEIF support.
- `bppicker[faces]` — face recognition (dlib + scipy + mediapipe).
- `bppicker[nudity]` — NSFW detection (NudeNet, GPL-3.0).
- `bppicker[raw]` — camera RAW formats (rawpy / LibRaw).
- `bppicker[inpaint]` — LaMa AI object removal (heavy: pulls torch).
- `bppicker[web]` — Flask SPA backend (most users want this).

### Network calls

External calls are limited to documented features:

- Model downloads from public hosts (HuggingFace, GitHub releases,
  Azure CDN, OpenStreetMap tile servers) on first use.
- Update check against GitHub Releases (owner-only, default-on,
  256 KB response cap, can be disabled in Settings → App).
- LAN-share traffic to paired devices, never traversing the
  internet.

### Platform scope (0.1.x)

- macOS-first. The Flask backend runs on Linux too; the Tauri
  desktop wrapper currently builds only the macOS bundle.
- Python 3.11 only (CI tests this version; 3.12+ is unvalidated).

[Unreleased]: https://github.com/Arkalogy/best-photo-picker/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Arkalogy/best-photo-picker/releases/tag/v0.1.0
