# Best Photo Picker v0.1.0

First public release. Local-first, face-aware photo curation — pick the best shots from large libraries without uploading a single pixel to the cloud.

## Highlights

- **Automated quality scoring** — every photo scored on sharpness, exposure, faces, and composition. Weighted selection picks the best K photos from any album.
- **Face-aware curation** — name the people who matter, boost their photos in the selection. Face clustering groups shots by person automatically. Wrong box on a busy pattern? Drag it onto the real face and the embedding is re-extracted in place.
- **Near-duplicate detection** — burst shots and visually similar photos grouped by perceptual hash distance. Step through duplicates side-by-side and keep the winner.
- **Live Photo sidecar filtering** — iPhone `_N` companion files excluded from counts and albums automatically.
- **Full photo library** — import, browse, tag, edit (non-destructive), export, trash, search, maps, calendar, memories, slideshow.
- **Offline-first** — model downloads happen once; no cloud account, no telemetry. Your photos never leave the machine for analysis.
- **Optional LAN sharing** — serve the UI on your network, approve devices via QR-code pairing.
- **Native macOS app** — Tauri v2 wrapper with native menus and auto-respawn on crash.

## Install

```bash
pip install bppicker[web]      # Flask UI, no ML models
pip install bppicker[web,faces,heic,raw]  # recommended for macOS
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

- **Official Docker image** (`arkalogy/bppicker`) for one-command self-hosting — the build pipeline is in place; publishing is planned for a follow-up release.

## Full changelog

See [CHANGELOG.md](https://github.com/Arkalogy/best-photo-picker/blob/main/CHANGELOG.md).
