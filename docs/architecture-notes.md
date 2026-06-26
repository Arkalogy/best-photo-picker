# Architecture notes

Schema-level details and design choices that affect how data flows
through the codebase. Read the relevant section before touching the
modules it mentions.

## GPS coordinates (schema v30)

GPS coords live in dedicated `gps_lat REAL` / `gps_lon REAL` columns
on the `photos` table, **not** only inside the `exif_json` blob.
A partial index `idx_photos_gps` covers map view and album-stats
queries against these columns.

- **New writers** populate both the dedicated columns and `exif_json`.
- **Back-compat**: `_maybe_lift_gps_from_exif()` in `bpp/db/photos.py`
  promotes coords out of `exif_json` for rows imported before v30.

If you add a feature that reads GPS, query the dedicated columns —
the partial index makes those queries fast even on libraries with
millions of photos.

## Live Photo sidecars (schema v33)

Apple Live Photo bundles ship a heic and a mov file together
(`IMG_xxxx.HEIC` + `IMG_xxxx_1.MOV`, or `IMG_xxxx_1.HEIC` +
`IMG_xxxx_1.MOV`). The `_1` sidecar is the motion track; the user
shouldn't see it as a separate photo in the grid.

- Sidecars are tagged `is_live_photo_sidecar = 1` and excluded from
  `ACTIVE_PHOTO_SQL` — they're stored but invisible in every
  user-facing view.
- Detection lives in `bpp/db/live_photo.py`; the heuristic
  assumptions are documented at the top of that module.
- Users opt in to importing sidecars via the import modal toggle
  (default off).

If you add a query that fetches photos, base it on `ACTIVE_PHOTO_SQL`
rather than `SELECT * FROM photos` — otherwise sidecars leak in.

## Face embedding dtype (schema v35)

SFace, the face-embedding model, produces 128-dim float32 vectors
natively. Earlier writes in `bpp/scoring/face_embed.py` called
`.astype(np.float64)` before serializing to the `face_embeddings.embedding`
blob, which doubled both the on-disk size (1024 bytes per face instead
of 512) **and** the peak RAM of the face-clustering matrix at runtime.
For a 100K-face library that is ~200 MB of slack per recompute; for a
500K-face library, ~1 GB.

The `_migrate_v35` step rewrites every existing embedding blob in place
from float64 to float32 — the high half of every double was zero-padding
anyway because the model is float32 internally, so the conversion is
mathematically a no-op for the actual content. New writes go in as
float32 from v35 on.

Migration safety:

- Idempotent — rows whose blob is already 512 bytes are skipped.
- Rows with an unexpected blob size (not 512 or 1024) are logged at
  WARNING and left untouched; they're pre-existing corruption that
  this migration shouldn't try to guess at.
- Per-step backup is taken before the step runs, so a botched batch
  rolls back from `<library>/photopicker.db.backup`.
- All 17 read sites across `bpp/web/bp_faces.py`, `bp_faces_manage.py`,
  `bp_faces_review.py`, `face_worker.py`, and `bpp/db/face_queries.py`,
  `face_cluster_ops.py`, `face_feedback.py` were updated to
  `dtype=np.float32` in the same change set — if you grep for
  `frombuffer.*embedding.*float64` and find a hit, that's a regression.

## Near-duplicate clustering (schema v34)

`assign_near_duplicate_clusters()` in `bpp/db/dedupe.py` groups photos
by phash + ahash hamming distance ≤ 8 bits using a Union-Find
algorithm. The result is stored in two columns:

- `dup_cluster_id INTEGER` — every photo in a cluster gets the same ID;
  singletons get a unique ID.
- `cluster_size INTEGER` — count of photos in the cluster.

The "Duplicates" smart album queries `cluster_size > 1`; the review
flow groups visible rows by `dup_cluster_id`.

Clustering runs automatically:

- after each import + analyze cycle, and
- on server startup as a backfill pass for any rows that don't have
  cluster info yet.

It can also be triggered manually via **Settings → Compute similarity
clusters**.

## CLIP semantic dedup peak memory (200K-photo scale)

The CLIP embedding-rows cap in `bpp/db/clip.py`
(`CLIP_EMBEDDING_MAX_ROWS`, default 200,000) protects against OOM at
recompute time. Peak runtime footprint is **~3x the embedding dict**:
the dict itself, the cached search matrix, and one semantic-dedup
scratch matrix can all be in memory at once (~1.2 GB at the 200K cap).

If you change the semantic-dedup pipeline:

- Add `del` calls between passes to free pass-1 buffers before pass-2
  allocates. The vectorized rewrite that landed in 0.1.0 originally
  held both buffers live and pushed peak well past the documented
  budget.
- Update the comment + `ClipEmbeddingsTooLarge` message math in
  `bpp/db/clip.py` if your change adds a fourth in-memory copy.
- The `BPP_CLIP_MAX_PHOTOS` env var lets users override the cap on
  machines with more RAM (e.g. 350,000 ≈ 2.1 GB peak, comfortable on
  16 GB; 500,000 ≈ 3 GB, prefer 32 GB). The in-app override lives in
  Settings → CLIP semantic search; the API + UI for it is wired in
  `bpp/web/bp_core.py` (`api_set_clip_max_override`) and
  `bpp/web/static/js/modules/clip.mjs`.

## WebAppState collaborators (P4)

`WebAppState` (in `bpp/web/state.py`) is a facade over four
collaborators introduced in refactor-plan.md P4. New endpoints reach
through these directly; legacy attribute access still works via
deprecation-logged property delegates in `bpp/web/state_compat.py`,
but every new code site should bind against the collaborator.

| Collaborator | Lives in | Owns |
|---|---|---|
| `ctx.workers` | `bpp/web/worker_pool.py` :: `WorkerPool` | Background-worker registry + `cancel_and_join_all`. `ctx.workers["analyze"]` etc. |
| `ctx.caches` | `bpp/web/model_cache.py` :: `ModelCache` | Derived-state caches: `caches.clip_cache`, `caches.face_cluster_map`, `caches.enhanced_ids`. Each sub-cache has its own `invalidate()`. |
| `ctx.analysis_store` | `bpp/web/analysis_store.py` :: `AnalysisStore` | `state["analysis"]` payload, the phash compute thread (`phash_ready`, `phash_generation`, `compute_thread`), and the cancellation event for the warm thread. |
| `ctx.lifecycle` | `bpp/web/library_lifecycle.py` :: `LibraryLifecycle` | `startup` / `shutdown` / `switch_library` orchestration. Calls into `state_lifecycle` module functions. |

Deprecation timeline: the property delegates in `state_compat.py` log
a one-shot WARNING on first access via the deprecated path. They
remain available for at least the v0.1 → v0.2 window; the audit
budget closes on them when the migration is complete.

PoC consumer reaching through the canonical collaborator surface:
`bpp/web/bp_health.py:170` reads `ctx.workers.items()` directly
instead of going through the deprecated `ctx._workers` delegate.

## Plugin extension surface

bppicker ships an opt-in plugin loader (`BPP_ENABLE_PLUGINS=1`) and a
typed protocol. See `docs/plugins.md` for the plugin-author guide; the
runtime contracts live here:

| Surface | Module |
|---|---|
| Entry-point loader | `bpp/plugins/__init__.py` :: `load_plugin_entry_points` |
| Lifecycle protocol | `bpp/plugin_protocol.py` :: `Plugin` (`on_register` / `on_library_open` / `on_library_close` / `on_shutdown`) |
| Registry protocol | `bpp/plugins/registry_protocol.py` :: `PluginRegistryLike` |
| Reference plugin | `bpp/plugins/example.py` |
| Reactive event bus | `bpp/db/event_hooks.py` (post-analyze / post-cluster / post-import) + `bpp/db/photo_hooks.py` (deletion) |
| Custom face phases | `bpp/web/face_phase_pipeline.py` :: `register_face_phase` |

Plugin-target registries (all four conform to `PluginRegistryLike`):

* `bpp/web/worker_registry.py` :: `WorkerRegistry`
* `bpp/db/smart_albums.py` :: `SmartAlbumRegistry` (including the
  `undeletable=True` and `ui_metadata_fn=…` registration extras)
* `bpp/output/export.py` :: `ExportModeRegistry`
* `bpp/dedupe/strategy.py` :: `DedupeStrategyRegistry`

Plus the in-tree face detector/embedder registries
(`bpp/scoring/face_detector_registry.py`,
`bpp/scoring/face_embedder_registry.py`) which already shared a
common base — exempt from the unification but still plugin-extensible
via `register_detector` / `register_embedder`.

## Activity feed: two levels (friendly vs. technical)

The same in-memory log ring buffer (`bpp/utils/logging.py` ::
`InMemoryHandler`, served by `GET /api/v1/logs`) feeds two UI surfaces
with deliberately different audiences:

| Surface | Audience | Content |
|---|---|---|
| Bell-dropdown "Recent Activity" | End user | Curated, plain-language milestones only |
| Settings → Activity tab ("View all" / Copy log) | Operator / support | The full, raw technical log — unchanged |

The split lives entirely on the frontend in
`bpp/web/static/js/modules/activity-humanize.mjs` ::
`humanizeActivity(entry)`. The bell-dropdown renderer and the bell badge
(`activity-log.mjs` :: `_renderDropdown` / `_activityBadgeCount`) both run
every raw entry through it; the Activity tab does **not** (it shows raw
lines verbatim).

Design rules:

* **Allowlist, not denylist.** A raw line appears in the friendly feed
  only if a rule maps it to friendly text. Everything else returns `null`
  and is dropped. New backend logging therefore never leaks into the
  consumer feed by default — it must be deliberately promoted with a rule.
* **No internals in friendly text** — no pids, exit codes, module names,
  "subprocess", or phase names. Counts are comma-formatted and pluralized.
* **Capability losses get a gentle note**, kept at `WARNING` level so the
  badge reflects it (e.g. "Pet detection isn't available on this device"),
  rather than the raw "download failed" line.
* **Unmatched `ERROR` never vanishes** — it falls through to a generic
  "Something went wrong — open Activity for details." note. Unmatched
  `INFO`/`WARNING` is treated as plumbing and hidden.
* **Badge counts only what's shown** — a hidden plumbing warning does not
  ping the bell.

To surface a new user-facing event, add an ordered rule in
`activity-humanize.mjs` (specific patterns before generic) and a branch
test in `tests-js/activity-humanize.module.test.mjs`.
