# Best Photo Picker API Reference

Best Photo Picker exposes a full REST API on `localhost:5001`. All endpoints are served from the Flask web UI.

**Versioning.** All endpoints live under the `/api/v1/` prefix. The version segment is part of the contract — when breaking changes ship, they will be released under `/api/v2/` and `/api/v1/` will be kept alongside (and eventually deprecated with notice). Today there is exactly one version (`v1`), so every example in this document is a real path you can curl. There are no unversioned aliases — `/api/<endpoint>` (without `v1/`) returns 404.

**Authentication.** Every `/api/v1/*` request must carry a valid token in the `X-Auth-Token` header (or the `_token=` query string for `<img>` / `<video>` URLs that can't send custom headers). Two tokens are accepted:

- **App session token** — generated fresh on every server boot, embedded in the index page's `<meta name="auth-token">` tag for the local Tauri app to read.
- **Persistent share token** — DB-backed, used by phones via the LAN share URL. Only valid from non-loopback addresses and only when the device's fingerprint cookie maps to a *trusted* `share_devices` row.

When **LAN sharing is off** (Settings → Share toggle), all non-loopback requests are denied at the gate before the token check runs. When LAN sharing is on, untrusted phones can only reach a small allow-list (`/`, `/static/*`, `/api/v1/share/pair/status`, `/api/v1/share/pair/request`) until the owner approves them. See [security.md](security.md) for the full threat model.

**Conventions:**

- All `POST`, `PUT`, and `DELETE` endpoints accept `Content-Type: application/json`.
- All state-changing operations use `POST`, `PUT`, or `DELETE` (never `GET`).
- SSE (Server-Sent Events) streams use `text/event-stream` and emit JSON objects prefixed with `data: `.
- Photo hashes (`path_hash`, `thumb_hash`) are content-addressed identifiers managed by the thumbnail system.

---

## Quick Start

### 1. Check status and list photos

```bash
# Check app status (is the server running? are there photos?)
curl http://localhost:5001/api/v1/status

# List all analyzed photos with metadata and scores
curl http://localhost:5001/api/v1/photos

# List albums
curl http://localhost:5001/api/v1/albums
```

### 2. Trigger analysis and monitor progress

```bash
# Import photos from a folder into the library
curl -X POST http://localhost:5001/api/v1/import \
  -H 'Content-Type: application/json' \
  -d '{"source_dir": "/path/to/photos"}'

# Monitor import progress (SSE stream -- use curl -N for streaming)
curl -N http://localhost:5001/api/v1/import/progress

# Start photo analysis (scoring for sharpness, lighting, faces, composition)
curl -X POST http://localhost:5001/api/v1/analyze \
  -H 'Content-Type: application/json' \
  -d '{}'

# Monitor analysis progress
curl -N http://localhost:5001/api/v1/analyze/progress
```

### 3. Export selected photos

```bash
# Recompute selection with custom weights and pick the top 50
curl -X POST http://localhost:5001/api/v1/recompute \
  -H 'Content-Type: application/json' \
  -d '{"k": 50, "blur_weight": 1.0, "face_weight": 1.5}'

# Export the selected photos to a folder
curl -X POST http://localhost:5001/api/v1/export \
  -H 'Content-Type: application/json' \
  -d '{"outdir": "/path/to/output", "selected_paths": ["/lib/photos/batch/IMG_001.jpg"]}'
```

---

## Building on the API

Best Photo Picker exposes a full REST API on `localhost:5001`. You can build custom frontends, integrate with automation tools (Shortcuts, Hazel, cron), or use it as a photo scoring microservice. The API binds to localhost; authentication is implicit because only the local machine can reach the socket. For LAN sharing (multi-device access from a phone or tablet on the same Wi-Fi), see [`docs/security.md`](security.md) for the pairing model and auth boundary. All state-changing operations use `POST`, `PUT`, or `DELETE`, so read-only tools can safely call any `GET` endpoint without side effects.

---

## Core

Endpoints from `bp_core.py`: application status, health checks, settings, presets, and runtime dependency management.

### GET /

Serve the main HTML SPA.

**Response:** HTML page.

### GET /api/v1/status

Application status and feature availability.

**Response:**

```json
{
  "has_analysis": true,
  "first_run": false,
  "image_count": 1234,
  "workdir": "/path/to/data",
  "input_dir": "/path/to/photos",
  "library_path": "/path/to/library",
  "analyzing": false,
  "importing": false,
  "serve_mode": true,
  "defaults": { "blur_weight": 1.0, "..." : "..." },
  "face_recognition_available": true,
  "face_installable": false,
  "nudenet_available": false,
  "pets_available": true,
  "face_extraction_done": true,
  "face_needs_clustering": false,
  "face_extracting": false,
  "face_cluster_threshold": 0.55,
  "clip_available": true,
  "clip_installable": false,
  "clip_ready": true,
  "clip_extracting": false,
  "clip_embedding_count": 1234,
  "heic_available": true,
  "pet_detection_done": false
}
```

### GET /api/v1/stats

Library statistics: counts, sizes, format breakdown.

**Response:**

```json
{ "total_photos": 1234, "total_size": 5368709120, "formats": { "jpg": 900, "png": 334 } }
```

### GET /api/v1/health

Aggregated liveness + readiness in one call (db, storage, background
workers, the operation journal, and the P4 collaborators). Lightweight by
construction — liveness only, no model probes or deep `quick_check`. Always
returns `200`; inspect the per-check blocks for status. Filesystem paths are
included only for the local-app owner.

**Response:**

```json
{
  "db": { "ok": true, "writable": true, "schema_version": 43, "path": "…/photopicker.db" },
  "storage": { "accessible": true, "free_gb": 812.4, "total_gb": 1862.0, "path": "…/BestPhotoPicker" },
  "workers": { "analyze": { "alive": false }, "import": { "alive": false } },
  "journals": { "pending": 0, "kinds": {} },
  "collaborators": { "analysis_store": { "phash_ready": true, "generation": 3 } }
}
```

### GET /api/v1/health/storage

Check if the library storage path is accessible (useful for NAS/network drives).

**Response:**

```json
{ "accessible": true }
```

### POST /api/v1/photos/recheck-missing

Re-scan all photos marked as missing and restore those whose files reappeared (e.g. after NAS reconnect).

**Response:**

```json
{ "restored": 5, "still_missing": 2 }
```

### POST /api/v1/pick

Open a native macOS file/folder picker dialog.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | string | `"folder"` | `"folder"` or `"file"` (archive picker) |

```bash
curl -X POST http://localhost:5001/api/v1/pick \
  -H 'Content-Type: application/json' \
  -d '{"mode": "folder"}'
```

**Response:**

```json
{ "path": "~/Photos" }
```

### GET /api/v1/presets

List all saved scoring presets.

**Response:**

```json
{ "presets": { "Portrait": { "face_weight": 2.0 }, "Landscape": { "composition_weight": 2.0 } } }
```

### POST /api/v1/presets

Save a scoring preset.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Preset name |
| `settings` | object | yes | Weight/config settings |

```bash
curl -X POST http://localhost:5001/api/v1/presets \
  -H 'Content-Type: application/json' \
  -d '{"name": "Portrait", "settings": {"face_weight": 2.0, "blur_weight": 1.5}}'
```

### DELETE /api/v1/presets/:name

Delete a preset by name.

```bash
curl -X DELETE http://localhost:5001/api/v1/presets/Portrait
```

### GET /api/v1/settings

Get all persisted settings (DB-stored overrides for analysis config).

**Response:**

```json
{ "face_detection_confidence": "0.3", "max_long_side": "1024" }
```

### PUT /api/v1/settings

Update settings. Accepts a flat key-value object.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| (any key) | string | yes | Setting key/value pairs |

```bash
curl -X PUT http://localhost:5001/api/v1/settings \
  -H 'Content-Type: application/json' \
  -d '{"face_detection_confidence": "0.25", "max_long_side": "2048"}'
```

### POST /api/v1/install/faces

Install the `bppicker[faces]` extra via pip at runtime. Returns immediately; monitor progress via the SSE endpoint.

**Response:** `202` with `{"status": "started"}`, or `200` if already installed.

### GET /api/v1/install/faces/progress

SSE stream for pip install progress.

**Event types:** `start`, `log` (with `message`), `done`, `error` (with `message`).

---

### GET /api/v1/version

Return the running app version. `{"version": "0.1.0"}`

### GET /api/v1/update/check

Check GitHub for a newer release. Returns the latest version, the
installed version, and whether an update is available. Only network
call besides model downloads; respects the update-check toggle.

### POST /api/v1/client-error

Ingest an uncaught client-side JS error into the server log so it
surfaces in Settings → Activity. Body: `{message, source, line}`.

### POST /api/v1/reveal-file

Owner-only. Reveal a photo in the OS file manager (Finder on macOS).
Body: `{filepath}` — must be inside the library root.

### GET /api/v1/logs

Recent log entries from the in-memory ring buffer (`?limit=500`).
Feeds the Activity tab; the bell dropdown runs the humanizer over it.

### POST /api/v1/logs/clear

Owner-only. Clear the in-memory buffer and truncate server.log files.

### GET /api/v1/debug/memory

Snapshot of major in-memory structures (caches, worker queues) for
memory-leak diagnostics.

### GET /api/v1/_diag/is_e2e_fixture

Internal. Reports whether the active library is a synthetic e2e
fixture — used by the Playwright suite's safety gate.

### POST /api/v1/settings/clip_max_override

Owner-only. Set or clear the per-library CLIP load-cap bypass.
Body: `{"value": "bypass"}` to set, `{"value": null}` to clear.

## Photos

Endpoints from `bp_photos.py`: photo listing, scoring, selection, export, overrides, favorites, edits, and batch operations.

### GET /api/v1/photos

List analyzed photos with metadata and scores. Paginated.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `limit` | int | No | Rows per page. Default `5000`, max `50000`, min `1`. |
| `offset` | int | No | Rows to skip. Default `0`. |

Bad input (negative offset, non-numeric values, oversized limit) is clamped to a safe default rather than rejected with 400.

**Response:**

```json
{
  "photos": [
    {
      "filepath": "/lib/photos/batch/IMG_001.jpg",
      "filename": "IMG_001.jpg",
      "date": "2024-06-15 14:30:00",
      "aggregate_score": 0.82,
      "blur_score": 0.9,
      "exposure_score": 0.75,
      "face_score": 0.8,
      "composition_score": 0.85,
      "thumb_hash": "abc123",
      "_enhanced": false
    }
  ],
  "count": 5000,
  "total": 87432,
  "limit": 5000,
  "offset": 0,
  "has_more": true
}
```

`count` is the number of rows in this response; `total` is the full library size; `has_more` is `true` if a higher offset would return more rows. Iterate by incrementing `offset` until `has_more` is `false`.

### GET /api/v1/photos/timeline

Photo count distribution grouped by month.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `album_id` | int | no | Filter to a specific album |

### GET /api/v1/photos/map

Photos with GPS coordinates for map view.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `album_id` | int | no | Filter to a specific album |

**Response:**

```json
{
  "photos": [
    { "id": 1, "filepath": "...", "gps_lat": 40.7128, "gps_lon": -74.006, "thumb_hash": "..." }
  ],
  "count": 42
}
```

### GET /api/v1/photos/preview

Preview photos from DB before analysis is complete. Returns basic metadata for all imported photos.

### POST /api/v1/recompute

Recompute the photo selection with given weights and parameters.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `k` | int | 50 | Number of photos to select |
| `seed` | int | 42 | Random seed for reproducibility |
| `blur_weight` | float | 1.0 | Sharpness weight |
| `exposure_weight` | float | 1.0 | Lighting weight |
| `face_weight` | float | 1.0 | Face quality weight |
| `composition_weight` | float | 1.0 | Composition weight |
| `face_selection_boost` | float | | Boost for photos containing selected faces |
| `sensitive_in_picks` | string | `allow` | Sensitive-photo pick policy: `allow` (compete normally) or `exclude` (filter from auto-picks; manual includes still win) |
| `selected_faces` | int[] | `[]` | Cluster IDs of people to prioritize |
| `hash_distance_threshold` | int | | Per-group dedup threshold |
| `global_hash_distance_threshold` | int | | Global dedup threshold |
| `time_window_seconds` | int | | Near-duplicate time window |
| `max_per_day` | int | | Maximum photos per day |
| `min_per_month` | int | | Minimum photos per month |
| `max_per_month` | int | | Maximum photos per month |
| `force_include` | string[] | `[]` | Filepaths to always include |
| `force_exclude` | string[] | `[]` | Filepaths to always exclude |
| `delta` | bool | false | Return only selection + scores (no full metadata) |

```bash
curl -X POST http://localhost:5001/api/v1/recompute \
  -H 'Content-Type: application/json' \
  -d '{"k": 50, "blur_weight": 1.0, "face_weight": 1.5, "selected_faces": [0, 2]}'
```

**Response:**

```json
{
  "photos": [ { "filepath": "...", "selected": true, "aggregate_score": 0.82 } ],
  "selected_paths": ["/lib/photos/batch/IMG_001.jpg"],
  "stats": { "total": 1234, "selected": 50, "deduplicated": 12 }
}
```

### POST /api/v1/optimize

Auto-optimize scoring weights to maximize selection quality.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `k` | int | 50 | Number of photos to select |
| `selected_faces` | int[] | `[]` | Cluster IDs to prioritize |

**Response:**

```json
{ "blur_weight": 1.2, "exposure_weight": 0.8, "face_weight": 1.5, "composition_weight": 1.0 }
```

### POST /api/v1/export

Export selected photos to a directory.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `outdir` | string | **required** | Output directory path |
| `selected_paths` | string[] | **required** | Filepaths of photos to export |
| `fmt` | string | `"original"` | Output format: `"original"`, `"jpeg"`, `"png"` |
| `mode` | string | `"copy"` | `"copy"`, `"hardlink"`, `"symlink"`, or `"zip"` (bundle the processed photos into a single `best-photos.zip`; honors `fmt`/`max_size`/`strip_metadata`; gallery is skipped) |
| `max_size` | int | null | Max dimension in pixels (min 100) |
| `quality` | int | 85 | JPEG quality 1-100 |
| `gallery` | bool | true | Generate HTML gallery |
| `write_manifest` | bool | false | Write `manifest.json` to outdir |
| `write_xmp` | bool | false | Write XMP sidecars next to each exported photo |
| `strip_metadata` | bool | true | Re-encode JPEGs to strip EXIF/GPS metadata |

**Existing destination behavior:** if `outdir` already exists, BPP **merges**
files into it — same-named files are overwritten in place; unrelated files
the user had in the folder are preserved. The previous `force` flag (which
silently `rmtree`d the destination, including unrelated user files) was
removed because pointing at `~/Downloads` or any shared folder would
destroy data. Callers passing `force` will see no error; it has no effect.

```bash
curl -X POST http://localhost:5001/api/v1/export \
  -H 'Content-Type: application/json' \
  -d '{"outdir": "/tmp/export", "selected_paths": ["/lib/photos/batch/IMG_001.jpg"], "fmt": "jpeg", "quality": 90}'
```

**Response:**

```json
{ "status": "exported", "outdir": "/tmp/export", "count": 50, "failed": 0 }
```

### POST /api/v1/open-folder

Open a directory in the native file manager (Finder on macOS).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | yes | Directory path to open |

### POST /api/v1/override

Set include/exclude override for a photo in the All Photos album.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepath` | string | yes | Photo filepath |
| `mode` | string | yes | `"include"`, `"exclude"`, or `null` (clear) |
| `selected_paths` | string[] | no | Current selection (for dedup feedback) |

### POST /api/v1/favorite

Toggle favorite status for a photo.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepath` | string | yes | Photo filepath |

**Response:**

```json
{ "status": "ok", "favorite": true }
```

### POST /api/v1/batch/override

Batch override for multiple photos.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepaths` | string[] | yes | Photo filepaths |
| `mode` | string | yes | `"include"`, `"exclude"`, or `null` |

### POST /api/v1/batch/favorite

Batch favorite for multiple photos.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepaths` | string[] | yes | Photo filepaths |
| `favorite` | bool | no | `true` (default) or `false` |

### GET /api/v1/overrides

Get all overrides and favorites for the All Photos album.

**Response:**

```json
{
  "overrides": { "/path/to/photo.jpg": "include" },
  "favorites": ["/path/to/photo.jpg"]
}
```

### POST /api/v1/photos/delete

Soft-delete photos (move to Recently Deleted).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepaths` | string[] | yes | Photo filepaths |

### POST /api/v1/photos/restore

Restore soft-deleted photos.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepaths` | string[] | yes | Photo filepaths |

### POST /api/v1/photos/delete-permanent

Permanently delete photos from DB and disk.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepaths` | string[] | yes | Photo filepaths |

**Response:**

```json
{ "status": "ok", "count": 3, "files_removed": 3 }
```

### GET /api/v1/photos/deleted

List all soft-deleted photos.

### POST /api/v1/photos/hide

Hide photos (remove from normal views without deleting).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepaths` | string[] | yes | Photo filepaths |

### POST /api/v1/photos/unhide

Unhide previously hidden photos.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepaths` | string[] | yes | Photo filepaths |

### GET /api/v1/photos/hidden

List all hidden photos.

### POST /api/v1/photos/enhance

Auto-enhance photos (adjust brightness, contrast, etc.) and store edit parameters.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepaths` | string[] | yes | Photo filepaths |

**Response:**

```json
{ "enhanced": 3, "params": { "/path/to/photo.jpg": { "brightness": 1.1, "contrast": 1.05 } } }
```

### POST /api/v1/photos/reset-edits

Remove all edits for specified photos, reverting to originals.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepaths` | string[] | yes | Photo filepaths |

### GET /api/v1/photos/edits

Get current edit parameters for a photo.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepath` | string (query) | yes | Photo filepath |

**Response:**

```json
{ "edits": { "brightness": 1.1, "contrast": 1.05, "rotation": 0 } }
```

### POST /api/v1/photos/save-edits

Save manual edit parameters for a single photo.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepath` | string | yes | Photo filepath |
| `edits` | object | yes | Edit parameters (brightness, contrast, saturation, sharpness, rotation, crop, warmth, etc.) |

Supported edit keys: `brightness`, `contrast`, `saturation`, `sharpness`, `rotation` (0/90/180/270), `crop_x`, `crop_y`, `crop_w`, `crop_h` (normalized 0-1), `flip_h`, `flip_v`, `straighten`, `warmth`, `tint`, `vibrance`, `exposure`, `brilliance`, `black_point`, `highlights`, `shadows`, `fade`, `definition`, `noise_reduction`, `vignette`, `grain`, `perspective_v`, `perspective_h`, `redeye_points`.

### POST /api/v1/photos/:photo_id/date

Update a photo's date.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `date` | string | yes | New date string (e.g. `"2024-06-15 14:30:00"`) |

**Response:**

```json
{ "date": "2024-06-15 14:30:00", "date_day": "2024-06-15", "date_month": "2024-06" }
```

### GET /api/v1/inpaint/status

Check if AI inpainting (object removal) is available.

**Response:**

```json
{ "available": false }
```

### POST /api/v1/photos/:photo_id/inpaint

Apply AI object removal to a photo. Requires the `[inpaint]` extra.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `mask` | string | yes | Base64-encoded PNG mask (white = area to remove) |

**Response:**

```json
{ "image": "<base64 PNG>", "photo_id": 42 }
```

### POST /api/v1/batch/rename/preview

Preview batch rename results without applying changes.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `pattern` | string | yes | Rename pattern (max 1000 chars) |
| `photo_ids` | int[] | no | Specific photo IDs (defaults to first 50 active photos) |

**Response:**

```json
{ "mapping": [ { "photo_id": 1, "old": "IMG_001.jpg", "new": "vacation-001.jpg" } ] }
```

### POST /api/v1/batch/rename/apply

Apply a batch rename mapping.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `mapping` | object[] | yes | Rename mapping from preview |

---

### POST /api/v1/photos/sensitive

Owner-only. Set or clear the user's sensitive-photo override on one
photo. Body: `{filepath, sensitive: bool|null}` — `null` returns the
photo to model-scored behavior; a boolean pins it permanently.

### GET /api/v1/photos/:photo_id/auto_straighten

Detect the dominant rotation angle for the editor's auto-straighten.
Returns `{"angle": -1.8}` (degrees; 0 when no dominant line found).

### GET /api/v1/photos/enhance-preview

Return auto-enhance parameters for a photo without saving anything
(`?filepath=...`) — powers the editor's per-section AUTO buttons.

### GET /api/v1/duplicates/groups

Near-duplicate groups for the review flow: photos grouped by
`dup_cluster_id`, each group sorted best-score-first.

## Albums

Endpoints from `bp_albums.py`: album CRUD, smart albums, per-album selection and overrides.

### GET /api/v1/albums

List all albums.

**Response:**

```json
{
  "albums": [
    { "id": 1, "name": "All Photos", "album_type": "all", "photo_count": 1234, "k": 50 }
  ]
}
```

### POST /api/v1/albums

Create a new album.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Album name (max 255 chars) |
| `config` | object | no | Per-album weight overrides |
| `k` | int | no | Selection size (default 50) |
| `parent_id` | int | no | Parent album ID for nesting |

```bash
curl -X POST http://localhost:5001/api/v1/albums \
  -H 'Content-Type: application/json' \
  -d '{"name": "Summer 2024", "k": 30}'
```

**Response:** `201` with `{"status": "created", "id": 5}`

### GET /api/v1/albums/:album_id

Get album details.

**Response:**

```json
{ "album": { "id": 5, "name": "Summer 2024", "album_type": "manual", "k": 30, "photo_count": 100 } }
```

### PUT /api/v1/albums/:album_id

Update album properties.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | no | New name (max 255 chars) |
| `config` | object | no | Per-album weight overrides |
| `k` | int | no | Selection size |
| `parent_id` | int/null | no | Parent album ID |

### DELETE /api/v1/albums/:album_id

Delete an album. Cannot delete the "All Photos" album. Deleting a smart album dismisses it permanently.

### GET /api/v1/albums/:album_id/photos

Get photos in an album with selection state.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int | all | Page size (max 5000) |
| `offset` | int | 0 | Pagination offset |
| `slim` | string | `"0"` | `"1"` for minimal columns |

**Response:**

```json
{
  "photos": [ { "filepath": "...", "selected": true, "override": "include", "favorite": true } ],
  "count": 100,
  "total": 120,
  "album": { "..." : "..." },
  "limit": null,
  "offset": 0,
  "has_more": false
}
```

### GET /api/v1/albums/:album_id/stats

Enriched statistics for an album.

**Response:**

```json
{
  "total": 100,
  "date_min": "2024-01-15 08:30:00",
  "date_max": "2024-08-20 17:45:00",
  "avg_score": 0.65,
  "gps_count": 42,
  "people_count": 5,
  "disk_size": 536870912,
  "video_count": 3
}
```

### POST /api/v1/albums/:album_id/recompute

Recompute selection within an album context. Accepts the same weight parameters as `POST /api/v1/recompute` plus uses album-specific config.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `k` | int | album's k | Selection size |
| `delta` | bool | false | Return only selection + scores |
| (weight params) | float | | Same as `/api/v1/recompute` |

### POST /api/v1/albums/:album_id/override

Set override for a photo within an album.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepath` | string | yes | Photo filepath |
| `mode` | string | yes | `"include"`, `"exclude"`, or `null` |

### POST /api/v1/albums/:album_id/favorite

Toggle favorite for a photo within an album.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepath` | string | yes | Photo filepath |

### POST /api/v1/albums/:album_id/batch/override

Batch override within an album.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepaths` | string[] | yes | Photo filepaths |
| `mode` | string | yes | Override mode |

### POST /api/v1/albums/:album_id/batch/favorite

Batch favorite within an album.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepaths` | string[] | yes | Photo filepaths |
| `favorite` | bool | no | Default `true` |

### POST /api/v1/albums/:album_id/add-photos

Add photos to an album.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepaths` | string[] | yes | Photo filepaths |

### POST /api/v1/albums/:album_id/remove-photos

Remove photos from an album.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepaths` | string[] | yes | Photo filepaths |

### GET /api/v1/albums/time/months

Monthly photo counts for a given year.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `year` | string (query) | yes | 4-digit year |

**Response:**

```json
{ "year": "2024", "months": [ { "month": 6, "count": 42 } ] }
```

### POST /api/v1/albums/refresh-smart

Regenerate all smart albums based on current data.

**Response:**

```json
{ "status": "refreshed", "albums": [ { "..." : "..." } ] }
```

---

### GET /api/v1/albums/:album_id/faces

Face cluster IDs present in an album's photos — drives the boost-chip
gallery shown inside person albums.

## Analysis & Import

Endpoints from `bp_analysis.py`: photo analysis pipeline, import, library management.

### POST /api/v1/analyze

Start photo analysis (scoring for sharpness, lighting, faces, composition).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `input_dir` | string | no | Override input directory or archive path |
| `recursive` | bool | no | Scan subdirectories (default `false`) |

```bash
curl -X POST http://localhost:5001/api/v1/analyze \
  -H 'Content-Type: application/json' \
  -d '{"recursive": true}'
```

**Response:** `202` with `{"status": "started", "workdir": "/path/to/data"}`

### GET /api/v1/analyze/progress

SSE stream for analysis progress.

**Event types:** `progress` (with `current`, `total`), `done`, `error` (with `message`), `keepalive`.

```bash
curl -N http://localhost:5001/api/v1/analyze/progress
```

### POST /api/v1/analyze/cancel

Cancel a running analysis.

**Response:**

```json
{ "status": "cancelling" }
```

### POST /api/v1/import

Import photos from a source directory into the library.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `source_dir` | string | yes | Source directory path |
| `batch_name` | string | no | Custom batch name |

```bash
curl -X POST http://localhost:5001/api/v1/import \
  -H 'Content-Type: application/json' \
  -d '{"source_dir": "/path/to/photos", "batch_name": "vacation-2024"}'
```

**Response:** `202` with `{"status": "started", "library_path": "/path/to/library"}`

### GET /api/v1/import/progress

SSE stream for import progress.

**Event types:** Same as analysis progress.

### POST /api/v1/import/cancel

Cancel a running import.

### GET /api/v1/library/status

Library path, batches, and import state.

**Response:**

```json
{
  "library_path": "~/Pictures/BestPhotoPicker",
  "exists": true,
  "batches": ["vacation-2024", "birthday"],
  "importing": false
}
```

### DELETE /api/v1/library

Delete all photos from DB and disk. Requires confirmation.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `confirmation` | string | yes | Must be `"delete"` |

```bash
curl -X DELETE http://localhost:5001/api/v1/library \
  -H 'Content-Type: application/json' \
  -d '{"confirmation": "delete"}'
```

---

### POST /api/v1/compute-hashes

Owner-only. Backfill perceptual hashes, then rebuild derived state.
When hashes are missing this kicks the ordered background pipeline
(hashes → Live Photo sidecar tags → duplicate clusters → Moments →
smart-album refresh) and returns `{"status": "started", "missing": N}`.
When nothing is missing, clustering runs inline and returns
`{"status": "done", "clustered": N, "moments": M}`.

### DELETE /api/v1/analysis-cache

Owner-only. Delete the scoring cache so the next analyze re-scores
everything from scratch. Photos are untouched.

## Export

### POST /api/v1/export/start

Owner-only. Spawn the streaming export worker; returns 202
immediately. Body: `{output_dir, mode, k, ...}` — `mode` is any
registered export mode (`copy` / `hardlink` / `symlink` built in).

### GET /api/v1/export/progress

SSE stream of export progress: `start` / `export_progress` / `done` /
`error` / `cancelled` events. Open right after /export/start returns.

### POST /api/v1/export/cancel

Owner-only. Stop the export worker after its current photo; the
progress stream emits `cancelled` and closes.

## Models & Install

### GET /api/v1/models

ML model status grouped by feature (installed, enabled, size, license).

### GET /api/v1/models/pending

Models not on disk yet — the downloads the next analyze would trigger.
Powers the download-consent dialog (`{models: [...], total_mb}`).

### POST /api/v1/models/toggle

Owner-only. Toggle a model on/off. Body: `{key, enabled}`.

### POST /api/v1/models/uninstall

Owner-only. Delete a model from disk to free space; it can be
re-downloaded later. Body: `{name}`.

### POST /api/v1/models/redownload

Owner-only. Delete and re-download a model by name (recovers from a
corrupt cache). Body: `{name}`.

### POST /api/v1/install/:key

Owner-only. Install a whitelisted pip package at runtime (e.g.
`faces`). The key must be on the server-side allowlist.

### GET /api/v1/install/:key/info

The exact pip specs the install endpoint will run for `key` — powers
the consent dialog.

### GET /api/v1/install/:key/progress

SSE stream of pip install progress.

## Faces & People

Endpoints from `bp_faces.py`: face extraction, clustering, person management, CLIP embeddings, and dedup feedback.

### POST /api/v1/faces/extract

Start face recognition (embedding extraction + clustering).

**Response:** `202` with `{"status": "started"}`

### POST /api/v1/faces/retry

Retry face extraction from scratch (clears existing data and face crop cache).

**Response:** `202` with `{"status": "started"}`

### GET /api/v1/faces/extract/progress

SSE stream for face extraction progress.

**Event types:** `progress`, `done`, `error`, `keepalive`.

### GET /api/v1/faces/clusters

List all face clusters (people) with representative photos.

**Response:**

```json
{
  "clusters": [
    {
      "cluster_id": 0,
      "name": "Alice",
      "photo_count": 42,
      "representative": { "filepath": "...", "face_index": 0, "thumb_hash": "abc123" }
    }
  ]
}
```

### POST /api/v1/faces/avatar

Set or clear a manual avatar override for a person cluster.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `cluster_id` | int | yes | Cluster (person) ID |
| `filepath` | string | no | Photo filepath (omit to clear override) |
| `face_index` | int | no | Face index in the photo (omit to clear) |

### GET /api/v1/faces/cluster/:cluster_id

Return face entries for a single cluster (for avatar picker). Results are sampled if the cluster is large.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `limit` | int (query) | 80 | Max faces to return |

**Response:**

```json
{ "faces": [ { "filepath": "...", "face_index": 0, "thumb_hash": "..." } ], "total": 150 }
```

### POST /api/v1/faces/merge

Merge face clusters (assign multiple people to a single identity).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `primary_cluster_id` | int | yes | Target cluster ID |
| `merge_cluster_ids` | int[] | yes | Cluster IDs to merge into primary |

```bash
curl -X POST http://localhost:5001/api/v1/faces/merge \
  -H 'Content-Type: application/json' \
  -d '{"primary_cluster_id": 0, "merge_cluster_ids": [3, 5]}'
```

**Response:**

```json
{ "status": "merged", "albums": [ "..." ] }
```

### POST /api/v1/faces/dismiss

Dismiss face clusters (mark as "not a face").

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `cluster_id` | int | conditional | Single cluster ID |
| `cluster_ids` | int[] | conditional | Multiple cluster IDs |

Provide either `cluster_id` or `cluster_ids`.

### POST /api/v1/faces/recluster

Re-run face clustering with a new distance threshold (no re-extraction needed).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `threshold` | float | yes | Clustering threshold (0.3 -- 1.2) |

```bash
curl -X POST http://localhost:5001/api/v1/faces/recluster \
  -H 'Content-Type: application/json' \
  -d '{"threshold": 0.5}'
```

**Response:**

```json
{ "status": "reclustered", "clusters": 15 }
```

### GET /api/v1/faces/photo/:path_hash

Return face info for all faces detected in a photo, including bounding boxes and person names.

**Response:**

```json
{
  "faces": [
    {
      "face_id": 42,
      "face_index": 0,
      "cluster_id": 0,
      "name": "Alice",
      "bbox_w": 80,
      "bbox_h": 100,
      "bbox_pct": { "x": 25.5, "y": 10.2, "w": 8.0, "h": 12.5 }
    }
  ],
  "person_tags": [ { "cluster_id": 0, "name": "Alice" } ],
  "thumb_hash": "abc123"
}
```

### GET /api/v1/faces/crop/:path_hash/:face_index

Get a cropped face thumbnail as JPEG.

**Response:** `image/jpeg` binary.

```bash
curl http://localhost:5001/api/v1/faces/crop/abc123/0 --output face.jpg
```

### POST /api/v1/faces/tag

Manually tag a photo with a person (cluster).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path_hash` | string | yes | Photo hash |
| `cluster_id` | int | yes | Person cluster ID |

### DELETE /api/v1/faces/tag

Remove a manual person tag from a photo.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path_hash` | string | yes | Photo hash |
| `cluster_id` | int | yes | Person cluster ID |

### POST /api/v1/faces/reassign

Reassign a specific face embedding to a different cluster (person). Propagates to similar unassigned faces.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `face_id` | int | yes | Face embedding ID |
| `cluster_id` | int | yes | Target cluster ID (or `-2` for "not a face") |

**Response:**

```json
{ "status": "reassigned", "albums": [ "..." ] }
```

### GET /api/v1/groups

Detect groups of people who appear together in photos.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `min_photos` | int (query) | 3 | Minimum co-occurrences |

**Response:**

```json
{
  "groups": [
    {
      "members": [0, 2],
      "photo_count": 15,
      "member_info": [ { "cluster_id": 0, "name": "Alice", "thumb_hash": "...", "face_index": 0 } ],
      "album_id": 10,
      "album_name": "Alice & Bob"
    }
  ]
}
```

### POST /api/v1/clip/extract

Start CLIP embedding extraction for semantic search.

**Response:** `202` with `{"status": "started"}`

### GET /api/v1/clip/progress

SSE stream for CLIP extraction progress.

**Event types:** `progress`, `done`, `error`, `keepalive`.

### GET /api/v1/dedup/feedback/stats

Dedup feedback statistics and adaptive threshold info.

**Response:**

```json
{
  "threshold": 0.92,
  "default_threshold": 0.92,
  "info": { "..." : "..." },
  "feedback_count": 5
}
```

---

### GET /api/v1/faces/dismissed

Dismissed faces with thumbnails — the Ignored section of the Faces
page.

### POST /api/v1/faces/create

Owner-only. Create a face from a user-drawn bbox. Body:
`{path_hash, bbox_pct, cluster_id | new_person_name}`.

### POST /api/v1/faces/update-bbox

Owner-only. Move a face's bbox after the user drags it onto the
correct face. Body: `{path_hash, face_index, bbox_pct}`.

### POST /api/v1/faces/split

Owner-only. Split selected faces out of their cluster into a new one
and record a hard negative so they won't re-merge. Body: `{face_ids}`.

### POST /api/v1/faces/restore

Owner-only. Restore dismissed faces back to unassigned (they
re-cluster on the next pass). Body: `{face_ids}`.

### DELETE /api/v1/faces/purge

Owner-only. Permanently delete dismissed face detections.

### GET /api/v1/faces/review

Unreviewed clusters with suggested matches — the "Review (N)" wizard's
data source.

### GET /api/v1/faces/threshold

The current adaptive face clustering threshold + metadata (feedback
count, confidence).

### GET /api/v1/faces/feedback/stats

Feedback-corpus statistics and whether re-clustering is recommended
(`nudge_recluster`).

### GET /api/v1/faces/review-pairs/count

Count of reviewable ambiguous pairs — gates the "Review pairs (N)"
button without building full pair metadata.

### GET /api/v1/faces/review-pairs/next

Ambiguous cluster pairs for the "Same person?" flow (`?limit=30`),
closest-first, enriched with names + representative crops. Pairs the
user already answered (either way) are excluded.

### POST /api/v1/faces/review-pairs/verdict

Owner-only. Record a verdict on a pair. Body:
`{cluster_a, cluster_b, verdict: "same"|"different"}`. A "same"
verdict MERGES the pair (the named or larger cluster wins) and the
response carries `merged`, `primary_cluster_id`,
`absorbed_cluster_id`, refreshed `albums`, and an `undo` snapshot.
"different" records a hard negative.

### POST /api/v1/faces/review-pairs/verdict/undo

Owner-only. Undo the most recent verdict on a pair. Body mirrors the
verdict call plus the `undo` snapshot for "same" verdicts — the merge
is fully reversed (faces, identities, person tags, album name).

## Media

Endpoints from `bp_media.py`: thumbnails, full-size photos, video serving, video tools.

### GET /thumb/:path_hash

Serve a thumbnail JPEG. Cached for 1 year (content-addressed by hash).

**Response:** `image/jpeg` binary.

### GET /photo/:path_hash

Serve a full-size photo. Handles HEIC, JPEG, PNG, and RAW formats (converts to JPEG on the fly). Applies any saved edits.

**Response:** `image/jpeg` binary.

### GET /video/:path_hash

Serve a video file with appropriate MIME type.

**Response:** Video binary (mp4, mov, avi, mkv, webm, etc.).

### GET /api/v1/video/preview/:path_hash

Serve a sprite-sheet preview image for a video. Cached for 24 hours.

**Response:** `image/jpeg` binary.

### POST /api/v1/video/trim

Trim a video file using ffmpeg. Replaces the original file.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `filepath` | string | yes | Video file path |
| `start` | float | yes | Start time in seconds |
| `end` | float | yes | End time in seconds |

```bash
curl -X POST http://localhost:5001/api/v1/video/trim \
  -H 'Content-Type: application/json' \
  -d '{"filepath": "/lib/photos/batch/VID_001.mp4", "start": 2.5, "end": 10.0}'
```

**Response:**

```json
{ "ok": true, "duration": 7.5 }
```

### POST /api/v1/thumbnails/clear

Clear the thumbnail cache.

**Response:**

```json
{ "status": "cleared", "count": 1234 }
```

---

## Libraries

Endpoints from `bp_library.py`: multi-vault library management.

### GET /api/v1/libraries

List all registered libraries.

**Response:**

```json
{
  "libraries": [
    { "path": "~/Pictures/BestPhotoPicker", "name": "Main Library", "exists": true }
  ],
  "active": "~/Pictures/BestPhotoPicker"
}
```

### POST /api/v1/libraries

Register and create a new library.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | yes | Library directory path |
| `name` | string | no | Display name |

### DELETE /api/v1/libraries

Remove a library from the registry (does not delete files).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | yes | Library path to unregister |

### PUT /api/v1/libraries/rename

Rename a library. Optionally rename the folder on disk.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | yes | Current library path |
| `name` | string | yes | New name |
| `rename_folder` | bool | no | Also rename the folder on disk |

### POST /api/v1/libraries/switch

Switch the active library.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | string | yes | Library path to activate |

```bash
curl -X POST http://localhost:5001/api/v1/libraries/switch \
  -H 'Content-Type: application/json' \
  -d '{"path": "~/Pictures/AnotherLibrary"}'
```

### GET /api/v1/libraries/active

Get the currently active library.

**Response:**

```json
{ "path": "~/Pictures/BestPhotoPicker", "name": "Main Library" }
```

---

## Search

Endpoints from `bp_search.py`: universal search across photos, albums, people, dates, and CLIP semantic search.

### GET /api/v1/search

Universal search. Searches filenames, album names, people names, dates (month names, years), score qualifiers (`"great"`, `"good"`, `"fair"`, `"low"`), and CLIP semantic similarity.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `q` | string (query) | yes | Search query |

```bash
curl 'http://localhost:5001/api/v1/search?q=beach'
```

**Response:**

```json
{
  "photos": [ { "filepath": "...", "similarity": 0.32 } ],
  "albums": [ { "id": 5, "name": "Beach Trip" } ],
  "people": [ { "album_id": 10, "name": "Alice", "photo_count": 42 } ],
  "dates": [ { "label": "June 2024", "month": 6, "year": 2024, "date_month": "2024-06" } ],
  "semantic": [ { "filepath": "...", "similarity": 0.28 } ],
  "clip_status": { "ready": true, "models_available": true, "embedding_count": 1234 }
}
```

---

## Calendar & On This Day

Endpoints from `bp_calendar.py`: calendar views and nostalgia features.

### GET /api/v1/calendar/months

All year/month combinations that have photos.

**Response:**

```json
{ "months": [ { "year": 2024, "month": 6, "count": 42 } ] }
```

### GET /api/v1/calendar/days

Daily photo counts for a given month.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `year` | int (query) | yes | Year (1900-2100) |
| `month` | int (query) | yes | Month (1-12) |

**Response:**

```json
{ "days": [ { "day": 15, "count": 8 } ], "year": 2024, "month": 6 }
```

### GET /api/v1/calendar/year

Daily photo counts for all months in a year (year overview).

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `year` | int (query) | yes | Year (1900-2100) |

**Response:**

```json
{ "year": 2024, "months": { "1": [ { "day": 1, "count": 3 } ], "6": [ { "day": 15, "count": 8 } ] } }
```

### GET /api/v1/calendar/photos

Photos for a specific date or date range.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `date` | string (query) | conditional | Single date `YYYY-MM-DD` |
| `start` | string (query) | conditional | Range start `YYYY-MM-DD` |
| `end` | string (query) | conditional | Range end `YYYY-MM-DD` |

Provide either `date` or both `start` and `end`.

```bash
curl 'http://localhost:5001/api/v1/calendar/photos?date=2024-06-15'
```

**Response:**

```json
{
  "photos": [ { "id": 1, "filepath": "...", "hash": "abc123", "date": "2024-06-15 14:30:00", "score": 0.82 } ],
  "date": "2024-06-15"
}
```

### GET /api/v1/on-this-day

Photos from this date in past years, grouped by year.

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `month` | int (query) | today | Month (1-12) |
| `day` | int (query) | today | Day (1-31) |

```bash
curl 'http://localhost:5001/api/v1/on-this-day?month=6&day=15'
```

**Response:**

```json
{
  "years": [
    { "year": 2023, "photo_count": 5, "hero_hash": "abc123", "photos": [ { "filepath": "...", "hash": "..." } ] },
    { "year": 2022, "photo_count": 3, "hero_hash": "def456", "photos": [ "..." ] }
  ],
  "month": 6,
  "day": 15
}
```

---

## Memories

Endpoints from `bp_memories.py`: auto-generated photo stories and highlights.

### GET /api/v1/memories

List all generated memories.

**Response:**

```json
{ "memories": [ { "id": 1, "title": "Summer 2024", "photo_count": 20, "date_range": "..." } ] }
```

### GET /api/v1/memories/:memory_id

Get a single memory with its photos.

**Response:**

```json
{
  "id": 1,
  "title": "Summer 2024",
  "photos": [ { "id": 42, "filepath": "...", "hash": "abc123", "date": "2024-06-15", "score": 0.82 } ]
}
```

### POST /api/v1/memories/refresh

Regenerate all memories from current photo data.

**Response:**

```json
{ "count": 5, "memories": [ "..." ] }
```

---

## Tags

Endpoints from `bp_tags.py`: tag CRUD, photo tagging, search, batch operations.

### GET /api/v1/tags

List all tags with photo counts.

**Response:**

```json
{ "tags": [ { "id": 1, "name": "vacation", "count": 42 } ] }
```

### POST /api/v1/tags

Create a new tag.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Tag name (stored lowercase) |

### PUT /api/v1/tags/:tag_id

Rename a tag.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | New name |

### DELETE /api/v1/tags/:tag_id

Delete a tag.

### GET /api/v1/tags/search

Search tags by prefix.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `q` | string (query) | yes | Search prefix |

### GET /api/v1/photos/:photo_id/tags

Get tags for a specific photo.

### POST /api/v1/photos/:photo_id/tags

Add a tag to a photo. Creates the tag if it does not exist.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `tag_id` | int | conditional | Existing tag ID |
| `name` | string | conditional | Tag name (creates if needed) |

Provide either `tag_id` or `name`.

### DELETE /api/v1/photos/:photo_id/tags/:tag_id

Remove a tag from a photo.

### POST /api/v1/tags/batch

Add a tag to multiple photos.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `photo_ids` | int[] | yes | Photo IDs |
| `tag_id` | int | yes | Tag ID |

### POST /api/v1/tags/batch/remove

Remove a tag from multiple photos.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `photo_ids` | int[] | yes | Photo IDs |
| `tag_id` | int | yes | Tag ID |

---

### GET /api/v1/tags/:tag_id/photos

All active photos carrying a tag — the Tags browse view's
click-through grid (standard photo dicts, lightbox-compatible).

### POST /api/v1/tags/:tag_id/merge

Owner-only. Merge this tag INTO `target_tag_id` (body): every photo
tagged with this tag gets the target tag, then this tag is deleted.

## Pets

Endpoints from `bp_pets.py`: pet detection clusters and crops.

### GET /api/v1/pets/clusters

List pet clusters (cats/dogs) with representative crops and photo counts.

**Response:**

```json
{
  "clusters": [
    {
      "cluster_id": 0,
      "pet_class": "dog",
      "photo_count": 15,
      "representative": { "filepath": "...", "detection_index": 0, "thumb_hash": "..." },
      "filepaths": [ "..." ]
    }
  ]
}
```

### GET /api/v1/pets/crop/:path_hash/:detection_index

Get a cropped pet thumbnail as JPEG.

**Response:** `image/jpeg` binary.

### GET /api/v1/pets/detections/:path_hash

Pet detections for a specific photo.

**Response:**

```json
{ "detections": [ { "detection_index": 0, "pet_class": "dog", "confidence": 0.95, "bbox_x": 100, "bbox_y": 50 } ] }
```

### GET /api/v1/pets/cluster/:cluster_id

Pet detections for a cluster (the identify picker). Sampled when
large (`?limit=80`).

### POST /api/v1/pets/split

Owner-only. Move selected detections into a new pet cluster. Body:
`{detection_ids}`. Returns the new cluster + refreshed albums.

### POST /api/v1/pets/merge

Owner-only. Merge pet clusters into a primary. Body:
`{primary_cluster_id, merge_cluster_ids}`.

### POST /api/v1/pets/dismiss

Owner-only. Mark a whole pet cluster as not-a-pet (false detection) —
it disappears from the Pets view, photo chips, and its album. Body:
`{cluster_id}`. Recoverable at the DB level (sentinel, not deletion).

## LAN sharing

These endpoints back the **Settings → Share** UI and the phone-side
TOFU pairing flow. State machine + threat model: see
[security.md](security.md). All endpoints sit behind the standard
auth boundary except `/api/v1/share/pair/status` and
`/api/v1/share/pair/request`, which are exempted via the
`authorize_request` allow-list because unpaired phones need to call
them before being trusted.

### GET /api/v1/share/info

Owner UI: current LAN sharing state.

**Response:**

```json
{
  "enabled": true,
  "lan_ip": "192.168.1.50",
  "port": 5001,
  "share_url": "http://192.168.1.50:5001/?_token=…",
  "recent_access": [
    { "ts": 1735689600, "ip": "192.168.1.42", "user_agent": "iPhone Safari" }
  ]
}
```

`enabled` is true only when both the persisted toggle is on AND a
LAN-routable IP is reachable. `share_url` embeds the persistent
share token (not the per-boot app session token) so phone bookmarks
survive restarts. `recent_access` is the deduplicated audit log
(last 10 entries, one per device per 10-minute window).

### POST /api/v1/share/toggle

Flip LAN sharing on/off. Persists across restarts.

**Body:** `{ "enabled": true }` or `{ "enabled": false }`. Missing
field returns 400.

**Response:** `{ "enabled": <bool> }`

### POST /api/v1/share/revoke

Rotate the persistent share token. All current share URLs become
invalid; trusted devices keep working until you revoke them
individually.

**Response:** `{ "share_url": "http://…/?_token=NEW" }` (or `null`
if sharing is currently off / no LAN IP).

### GET /api/v1/share/qr

Branded PNG QR code for the LAN share URL. Returns 404 when sharing
is disabled or no LAN IP is detected.

**Response:** `image/png` binary (rounded modules, BPP glyph
embedded in center).

### GET /api/v1/share/devices

Owner UI: list of pending + trusted devices.

**Response:**

```json
{
  "pending": [
    { "id": 7, "fingerprint": "…", "name": "iPhone", "ip_at_pair": "192.168.1.42",
      "first_seen": 1735689600, "last_seen": 1735689650,
      "trusted_at": null, "revoked_at": null, "prev_revoked": 0 }
  ],
  "trusted": [ /* same shape, trusted_at non-null */ ]
}
```

Revoked devices are hidden from this list. They reappear in
`pending` only if the phone explicitly re-requests via
`/api/v1/share/pair/request`.

### POST /api/v1/share/devices/:id/approve

Owner approves a pending device. Idempotent.

**Response:** `{ "ok": true, "id": <id> }`. 404 if device unknown.

### POST /api/v1/share/devices/:id/revoke

Owner revokes a device. Sets the sticky `prev_revoked` flag for
forensic UX. Phone's next request fails immediately, the in-page
JS auto-reloads, and the phone lands on the "Access revoked" pair
page.

**Response:** `{ "ok": true, "id": <id> }`. 404 if device unknown.

### GET /api/v1/share/pair/status

Phone polls this every ~2 seconds while waiting on the pair page.
Allowed for any LAN client with a fingerprint cookie — the response
contains only the device's own state, no library data.

**Response:** `{ "state": "unknown" | "pending" | "trusted" | "revoked" }`

### POST /api/v1/share/pair/request

Phone explicitly re-requests access after a revoke. Tied to the
"Request access again" button on the revoked pair page. Idempotent
on already-pending and already-trusted rows (never demotes).

**Response:** `{ "state": "pending" | "trusted" }`. 400 if no
fingerprint cookie. 404 if the cookie maps to no row.

### POST /api/v1/share/devices/:device_id/approve

Owner-only. Approve a pending LAN device — one click, no code typing.

### POST /api/v1/share/devices/:device_id/revoke

Owner-only. Revoke a device; its next request 403s.

## Model Registry

The model-registry endpoints implement the legal-posture surface for
third-party ML models: the click-through acceptance dialog, the
per-user acceptance log, the use-context declaration, the
Bring-Your-Own-Model path, and model removal with derived-data purge.
See [model-policy](../MODEL_POLICY.md) for the policy these endpoints
enforce, and `pm-face-embedder-spike.md` (gitignored) for the
24-item plan.

Every mutating endpoint requires `@requires_local_app` — the LAN
share principal cannot drive someone else's acceptance or model
removal. The two read endpoints (`acceptance/draft` and
`use-context` GET) are loopback-only by virtue of the same gate.

### GET /api/v1/model-registry/entries

List every registered `ModelEntry`, grouped by license posture
(permissive vs restricted). The Settings → Models picker renders
this response.

**Response:**

```json
{
  "groups": [
    {
      "title": "Permissive license",
      "subtitle": "No known commercial-use restriction",
      "entries": [
        {
          "id": "sface_yunet",
          "display_name": "SFace (YuNet + SFace ONNX)",
          "kind": "face_embedder",
          "ui_label": "SFace recognition (permissive)",
          "group_title": "Permissive license",
          "group_subtitle": "No known commercial-use restriction",
          "license_summary": "OpenCV Zoo distribution under Apache 2.0…",
          "status": "available",
          "requires_explicit_ack": false,
          "commercial_use_restriction_known": false,
          "default_for_kind": true,
          "ack_text_kind": "canonical",
          "expected_download_size_bytes": 232589
        }
      ]
    },
    {
      "title": "Restricted-license models",
      "subtitle": "Hard-blocked in commercial mode without rights assertion",
      "entries": [
        {
          "id": "insightface_buffalo_s",
          "display_name": "InsightFace buffalo_s (research-only)",
          "kind": "face_embedder",
          "requires_explicit_ack": true,
          "commercial_use_restriction_known": true,
          "status": "available",
          "expected_download_size_bytes": 127607557
        }
      ]
    }
  ]
}
```

`expected_download_size_bytes` is `0` when the upstream artifact
size is unknown.

### GET /api/v1/model-registry/acceptance/draft

Return the click-through draft payload for one model — every
string the dialog must render plus the required-checkbox set.
Read-only.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| model_id | string | yes | Registry entry id (e.g. `insightface_buffalo_s`) |
| use_context | string | no | One of `personal` / `research` / `commercial` / `unspecified` |

**Response:**

```json
{
  "model_id": "insightface_buffalo_s",
  "model_display_name": "InsightFace buffalo_s (research-only)",
  "compressed_disclaimer": "Best Photo Picker does not redistribute…",
  "full_disclaimer": "Best Photo Picker does not redistribute or bundle this model…",
  "commercial_use_definition": "Commercial use means…",
  "biometric_responsibility_text": "Face embeddings derived from your photos…",
  "produces_biometric_data": true,
  "required_checkboxes": [
    { "id": "not_commercial", "text": "I will not use this model for commercial workflows…" }
  ],
  "separate_rights_assertion": "I have obtained separate commercial rights for…",
  "ack_text_version": "canonical-disclaimer-v2",
  "ack_text_sha256": "a7346a755f33a9a4b1a6e4a08bcddce0dfca976b63baa15c2139254c1d00c5e5",
  "use_context_text_version": "use-context-v1",
  "use_context_text_sha256": "…",
  "use_context": "personal",
  "terms_url": "https://github.com/deepinsight/insightface/blob/master/LICENSE",
  "terms_permalink_url": "https://github.com/deepinsight/insightface/blob/<sha>/LICENSE",
  "terms_retrieved_at": "2026-06-02"
}
```

400 with `{"error": "Unknown model_id: …"}` if `model_id` is not
registered. `biometric_responsibility_text` is an empty string when
the entry's `produces_biometric_data` is false (the GUI block is
suppressed for non-biometric models even though the canonical
disclaimer still carries the paragraph).

### POST /api/v1/model-registry/acceptance/confirm

Validate the dialog response, persist the acceptance row, return
the persisted row.

**Body:**

```json
{
  "model_id": "insightface_buffalo_s",
  "use_context": "personal",
  "checkbox_responses": {
    "not_commercial": true,
    "mit_doesnt_grant_rights": true,
    "direct_upstream": true,
    "no_paid_without_separate_rights": true
  },
  "accepted_at": "2026-06-02T12:34:56+00:00",
  "separate_rights_asserted": false,
  "source_of_rights_note": ""
}
```

`accepted_at` is optional; defaults to server-side UTC now. Every
required checkbox id from the draft response MUST appear in
`checkbox_responses` (use `false` rather than omitting if unchecked).

**Response (200):**

```json
{ "acceptance": { /* persisted AcceptanceRow */ } }
```

**Response (400)** — validation failure (unchecked / missing required
boxes, blank timestamp, restricted entry without a `terms_permalink_url`):

```json
{ "error": "user must check every required box. Unchecked: ['not_commercial']" }
```

### GET /api/v1/model-registry/acceptance/list

Read-only view of the acceptance log. Settings → "View your
acceptances" renders this. Returns every row in append order
(oldest first).

**Response:**

```json
{
  "acceptances": [
    {
      "model_id": "insightface_buffalo_s",
      "ack_text_version": "canonical-disclaimer-v2",
      "ack_text_sha256": "…",
      "use_context_at_acceptance": "personal",
      "separate_rights_asserted": false,
      "accepted_at": "2026-06-02T12:34:56+00:00",
      "checkbox_responses": {
        "not_commercial": true,
        "mit_doesnt_grant_rights": true,
        "direct_upstream": true,
        "no_paid_without_separate_rights": true
      },
      "schema_version": 2
    }
  ]
}
```

Per-checkbox responses land in schema v2 rows; legacy v1 rows on disk
load with `checkbox_responses: {}` and `schema_version: 1`. Each row also
carries an `event` discriminator (`"accept"` or `"revoke"`); legacy rows
without it read as `"accept"`. For a given `model_id`, the most-recent
row's event decides whether it is currently accepted.

### POST /api/v1/model-registry/acceptance/revoke

Withdraw a prior acceptance for a restricted model. **Append-only:** the
original acceptance row is never deleted (it stays in the legal audit
trail) — a withdrawal writes a new `event: "revoke"` row that supersedes
it. The model re-gates: the server-side load policy
(`enforce_load_policy_for`) blocks inference until the user re-accepts.
`@requires_local_app`.

**Body:**

```json
{ "model_id": "insightface_buffalo_s" }
```

**Response (200):**

```json
{ "revocation": { "model_id": "insightface_buffalo_s", "event": "revoke", "accepted_at": "…" } }
```

Returns **400** if there is no active acceptance to withdraw (never
accepted, or already withdrawn) or the `model_id` is unknown.

### POST /api/v1/face-embedders/ensure-weights

Force a *catalog* entry's weights to be downloaded NOW, before the user
activates the model. Catalog entries (currently only
`insightface_buffalo_s`) are runtime-fetched models with no install
wiring in the legacy `ModelRegistry` — the regular
`/api/v1/models/redownload` path cannot find them. The Settings → Models
picker uses this endpoint as the "Download" step in the Review → Download
→ Use → Uninstall lifecycle so the user is never surprised by a silent
multi-MB fetch on first analyze (project convention: nothing should be silent).

Synchronous — blocks until the fetch + SHA-verifying extract completes.
The download itself routes through `bpp.utils.download.download_file`,
which runs the policy gate before any network call, so a restricted
entry whose license has not been accepted is refused here too.
`@requires_local_app`.

**Body:**

```json
{ "registry_id": "insightface_buffalo_s" }
```

**Response (200):**

```json
{ "ok": true, "size_bytes": 127596032 }
```

Returns **400** when `registry_id` is missing or names an entry that
has no catalog loader (e.g. an installable model with legacy
`ModelRegistry` wiring — those go through `/api/v1/models/redownload`).
Returns the underlying error's diagnostic message on policy refusal,
SHA mismatch, or network failure.

### POST /api/v1/face-embedders/uninstall-weights

Delete a catalog entry's locally cached weights. Symmetric counterpart
to `/ensure-weights` for the Uninstall step in the picker menu.
Idempotent: removing weights that are not on disk returns
`bytes_freed: 0` rather than an error. `@requires_local_app`.

**Body:**

```json
{ "registry_id": "insightface_buffalo_s" }
```

**Response (200):**

```json
{ "ok": true, "bytes_freed": 127596032 }
```

Returns **400** when `registry_id` is missing or names an entry that
has no catalog loader.

### GET /api/v1/model-registry/use-context

Return the user's current use-context declaration + audit history.

**Response:**

```json
{
  "use_context": "personal",
  "set_at": "2026-06-02T12:00:00+00:00",
  "set_via": "settings",
  "history": [
    { "value": "unspecified", "set_at": "…", "set_via": "first-launch-gate" },
    { "value": "personal", "set_at": "…", "set_via": "settings" }
  ]
}
```

### POST /api/v1/model-registry/use-context

Persist a use-context declaration.

**Body:**

```json
{ "use_context": "personal", "set_via": "settings" }
```

`set_via` defaults to `"settings"` (used by the GUI Settings panel);
the onboarding wizard's Step 3 sends `"first-launch-gate"`.

**Response (200):** same shape as the GET, without `history`.

**Response (400):** if `use_context` is missing or not one of the
four valid values (`personal` / `research` / `commercial` / `unspecified`).

### GET /api/v1/model-registry/byom

List the user's registered Bring-Your-Own-Model entries.

**Response:**

```json
{
  "byom_entries": [
    {
      "id": "byom_abc123",
      "display_name": "My ArcFace fine-tune",
      "kind": "face_embedder",
      "file_path": "/Users/you/models/my_arcface.onnx",
      "weight_sha256": "…",
      "added_at": "2026-06-02T12:00:00+00:00",
      "ack_text_version": "byom-disclaimer-v2",
      "ack_text_sha256": "…"
    }
  ]
}
```

### POST /api/v1/model-registry/byom

Register a user-supplied model file. Returns the projected entry
shape so the caller can immediately drive the acceptance dialog on
it via `/api/v1/model-registry/acceptance/draft?model_id=<id>`.

**Body:**

```json
{
  "file_path": "/Users/you/models/my_arcface.onnx",
  "display_name": "My ArcFace fine-tune",
  "kind": "face_embedder"
}
```

`kind` defaults to `face_embedder`. Registration does NOT walk the
acceptance dialog — registration + acknowledgment are two steps so
the dialog can render the BYOM ack text and the caller can
re-snapshot the hash at acceptance time.

**Response (201):** the registered entry shape (same as the GET
response's row).

**Response (400):** if `file_path` is missing or the file does not
exist.

### DELETE /api/v1/model-registry/byom/{entry_id}

Owner-only. Forget a BYOM entry. Does NOT delete the underlying file from
disk — BYOM is a pointer abstraction.

**Response (200):** `{ "removed": "<entry_id>" }`.
**Response (400):** if no BYOM entry with the given id exists.

### GET /api/v1/model-registry/removal/preview

Return the derived-data counts the removal confirmation modal will
display BEFORE the user clicks through. Pure read; nothing is
deleted.

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| model_id | string | yes | Registry entry id |

**Response:**

```json
{
  "model_id": "insightface_buffalo_s",
  "embeddings": 3944,
  "distinct_clusters": 234,
  "distinct_photos": 2627
}
```

### POST /api/v1/model-registry/removal

Remove a model entry and optionally purge its derived data (face
embeddings tagged with the producing `model_id`). Both fields are
required — the endpoint fails closed when `purge_derived` is
omitted, matching the CLI's explicit-flag requirement (Q8). The
GUI confirmation modal sends `purge_derived=true` unless the user
explicitly checked the "Keep derived data" opt-out.

**Body:**

```json
{ "model_id": "insightface_buffalo_s", "purge_derived": true }
```

**Response (200):**

```json
{
  "model_id": "insightface_buffalo_s",
  "entry_kind": "face_embedder",
  "embeddings_purged": 3944,
  "distinct_clusters_affected": 234,
  "distinct_photos_affected": 2627,
  "purged": true
}
```

**Response (400):** if `model_id` or `purge_derived` is missing,
or the entry can't be removed (e.g. is a bundled baseline that
removal policy forbids).

