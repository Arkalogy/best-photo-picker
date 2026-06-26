# Adding a new ML model

This guide covers wiring a new model into bpp's code so it loads, gates
on its license, and shows up correctly in Settings → Models. For the
*operational* side (takedowns, status flips, publishing the signed
registry), see [registry-runbook.md](registry-runbook.md).

## First: which load path?

bpp has two ways a model's weights reach the user's disk. Pick one
**before** you start wiring — getting this wrong is what produced the
LaMa/NudeNet "Download button vanished" bug.

```
Are the weights bundled inside a pip package the user installs
(e.g. dlib ships inside `face_recognition`)?
│
├─ YES → pip-bundled. No download path of our own. Gate at USE time only
│         (enforce_load_policy_for in the loader). See "pip-bundled" below.
│
└─ NO → we fetch the weights ourselves. Two sub-cases:
    │
    ├─ Single file, known URL + SHA, fetched lazily on first use
    │   (CLIP, YuNet, SCRFD, SFace, YOLO) → MANIFEST path.
    │
    └─ Fetched on demand via its own flow — multi-file zip, or only
        pulled when a feature is invoked (buffalo_s, NudeNet, LaMa)
        → CATALOG path.
```

Rule of thumb: **if it has a stable single-file URL and should be part
of the "Download all models" pre-flight, it's MANIFEST. If it's a
heavier/optional capability fetched only when the user opts in, it's
CATALOG.**

## Always required (every path)

1. **Registry entry** — add a `ModelEntry` to `bpp/registry/builtins.py`
   describing license posture (`requires_explicit_ack`, license class,
   `kind`, `default_for_kind`, `terms_url`, etc.). This is the legal
   source of truth. Restricted (non-permissive) models set
   `requires_explicit_ack=True`.
2. **Download gate** — the actual fetch MUST go through
   `bpp/utils/download.py::download_file(..., registry_id="<id>")`. Never
   `urlretrieve`/`requests.get`. The `registry_id` makes the policy gate
   fire before any bytes hit the network — a restricted, unaccepted model
   is refused.
3. **Use gate (restricted models)** — call
   `enforce_load_policy_for("<id>")` in the loader **before inference**,
   for cases the download gate can't cover (bundled/pip models, or a
   cached file loaded without re-downloading). See
   `bpp/scoring/nudity.py` / `face_embed_buffalo_s.py` for the pattern.
4. **`ModelSingleton`** — new models use `ModelSingleton` from
   `bpp/scoring/model_base.py` for thread-safe lazy init (never hand-roll
   globals + locks).

## MANIFEST path checklist

1. Above "always required" steps.
2. **`bpp/scoring/model_manifest.py`** — add a `ModelEntry` to
   `all_models()` with `path`, `url`, `sha256`, `size_mb`, and
   **`legal_entry_id="<id>"`** linking it to the registry entry. Missing
   that link makes the pre-flight dialog treat a restricted model as a
   free download (the bug fixed in this rollout).
3. That's it — `pending_downloads()`, the pre-flight consent dialog, and
   the Settings picker pick it up automatically.

## CATALOG path checklist

1. Above "always required" steps.
2. **Expose a loader trio** in the model's module (mirror
   `bpp/scoring/nudity.py` lines for `is_on_disk` / `ensure_*_model` /
   `remove_local_weights`, or `bpp/ai/inpainting.py` for the
   `ModelSingleton`-backed variant):
   - `is_on_disk() -> bool` — cheap existence check (no SHA verify).
   - `ensure_<model>_model() -> str` — download + verify, return the
     path. Routes through `download_file(registry_id=...)`.
   - `remove_local_weights() -> int` — delete + `reset()` the singleton,
     return bytes freed. Log a WARNING on a failed unlink (don't swallow).
3. **`bpp/web/bp_catalog.py`** — add one row to
   `_catalog_loaders()` mapping `"<id>": (is_on_disk, ensure_fn,
   remove_fn)`. This is the single switch that makes the picker show
   Download/Uninstall and report on-disk status — the backend sets
   `is_catalog_entry=True` for any id in this map, and the frontend
   routes Download/Uninstall to the catalog endpoints off that flag. No
   frontend edit is needed for routing.
4. **If the model also has a legacy "feature" row** (a capability toggle
   in the old Settings list — e.g. "Content filter", "AI object
   removal"), it will have a `_build_*` entry in
   `bpp/web/models_status.py` returning `files: []`, and a matching
   `legacy_label` in `_LEGACY_FEATURE_MAP` in
   `bpp/web/static/js/modules/modals-face-embedders.mjs`. That's fine —
   the `is_catalog_entry` flag (step 3) is authoritative and overrides
   the fileless legacy row. Do **not** try to infer catalog-ness from the
   absence of a legacy record.

## pip-bundled path checklist

1. "Always required" steps 1, 3, 4 (no download gate — the weights come
   with the pip package).
2. **Use gate is mandatory and is the ONLY gate** — call
   `enforce_load_policy_for("<id>")` at the top of the inference function,
   fail-closed. dlib is the reference: `_enforce_dlib_policy()` in
   `bpp/scoring/face_embed_extractors.py`. Memoize the passing result if
   the gate is on a per-photo hot path (the acceptance check reads the
   log file each call).

## Verify

- `pytest tests/registry/ tests/test_models_pending_endpoint.py`
- For catalog entries: `tests/registry/test_catalog_weights_endpoints.py`
  (there's a regression test asserting on-demand models are flagged
  `is_catalog_entry`).
- Restart the server and confirm the new row in Settings → Models offers
  the right Review → Download → (Use) → Uninstall actions and reports the
  right status.
