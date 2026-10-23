# Best Photo Picker v0.1.0

First public release. Local-first, face-aware photo curation — pick the best shots from large libraries without uploading a single pixel to the cloud.

## Highlights

- **Automated quality scoring** — every photo scored on sharpness, exposure, faces, and composition. Weighted selection picks the best K photos from any album.
- **Face-aware curation** — name the people who matter, boost their photos in the selection. Face clustering groups shots by person automatically. Wrong box on a busy pattern? Drag it onto the real face and the embedding is re-extracted in place.
- **Near-duplicate detection** — burst shots and visually similar photos grouped by perceptual hash distance. Step through duplicates side-by-side and keep the winner.
- **Live Photo sidecar filtering** — iPhone `_N` companion files excluded from counts and albums automatically.
- **Full photo library** — import, browse, tag, edit (non-destructive), export, trash, search, maps, calendar, memories, slideshow.
- **Local-first** — your photos never leave the machine for analysis; models download once and are cached; no cloud account, no telemetry. (bpp does make a few network calls — a signed model-registry check at startup, an update check, map tiles — listed in the README's Privacy section.)
- **Optional LAN sharing** — serve the UI on your network, approve devices via QR-code pairing.
- **Native macOS app** — Tauri v2 wrapper with native menus and auto-respawn on crash.

## Install

```bash
pip install "bppicker[web]"      # Flask UI; ML models download on first analysis
pip install "bppicker[web,faces,heic,raw]"  # recommended for macOS
bpp serve --library ~/Pictures/MyPhotos
```

See the [README](https://github.com/Arkalogy/best-photo-picker#readme) for the full install guide, optional extras, and CLI reference.

## What's included

| Category | Status |
|----------|--------|
| Library management (import, browse, trash, RAW, HEIC, video, batch rename) | ✅ Full |
| Organization (albums, faces, pets, tags, GPS/map, hierarchy, hidden) | ✅ Full |
| Search & discovery (CLIP semantic, date, tag, map, recently added) | ✅ Full |
| Viewing (lightbox, compare, slideshow, calendar, memories) | ✅ Full |
| Non-destructive editing (crop, adjust, filters, red-eye, AI inpaint) | ✅ Full |
| Export (folder copy, symlink, resize, XMP sidecar, JSON manifest) | ✅ Full |
| Automated curation (scoring, selection, dedup, near-dupe, face boost) | ✅ Full |
| LAN sharing with device pairing | ✅ Full |
| Native macOS desktop wrapper (Tauri v2) | ✅ Full |

## Platform notes (0.1.x)

- **macOS-first.** The Flask backend runs on Linux; the Tauri desktop wrapper builds macOS only.
- **Python 3.11.** Other versions are untested in this release.

## Roadmap

- **Official Docker image** for one-command self-hosting — not published. The repo ships a `Dockerfile` and a manually triggered build workflow, but neither has been run in CI yet.

## Full changelog

See [CHANGELOG.md](https://github.com/Arkalogy/best-photo-picker/blob/main/CHANGELOG.md).
