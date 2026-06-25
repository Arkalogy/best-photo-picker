# Plugins

Best Photo Picker can be extended without forking via setuptools
entry-points. A plugin is an installed Python package that declares a
setup callable; bpp invokes it at startup so the plugin can register
detectors, embedders, scorers, smart-album types, workers, or config
fields.

> **Plugin loading is OFF by default.** Set the environment variable
> `BPP_ENABLE_PLUGINS=1` to opt in. See the *Trust contract* section
> below for why.

## Quick start

In your plugin package's `pyproject.toml`:

```toml
[project.entry-points."bpp.plugins"]
my_extension = "my_pkg.bpp_plugin:setup"
```

Then write the `setup` callable:

```python
# my_pkg/bpp_plugin.py
from bpp.scoring.face_detector_registry import FaceDetector, register_detector

def setup() -> None:
    """Called once at bpp startup. Register your extensions here."""
    register_detector(FaceDetector(
        name="my_retinaface",
        detect=my_retinaface_callable,
        toggle_key="model_my_retinaface",
        license_id="MIT",
        description="Custom RetinaFace ONNX detector",
    ))
```

Install the plugin (`pip install my-bpp-plugin`) into the same venv as
bpp, then run with the opt-in:

```sh
BPP_ENABLE_PLUGINS=1 bpp serve --library ~/Pictures/BestPhotoPicker
```

A debug log line confirms registration: `Registered face detector
'my_retinaface' (license=MIT, …)`.

## Lifecycle hooks — `Plugin` protocol

The zero-arg `setup()` callable above is the minimum a plugin needs.
For richer integration — opening a per-library resource, registering
a Flask blueprint, releasing handles when the user switches library —
implement the `Plugin` protocol from `bpp.plugin_protocol` and call
`register_plugin(plugin_instance)` from your `setup()`:

```python
# my_pkg/bpp_plugin.py
from bpp.plugin_protocol import register_plugin


class MyPlugin:
    """Implements only the hooks you care about. Every method is
    optional — the host checks via hasattr before calling."""

    def on_register(self, app):
        """Process startup, after setup() returns. `app` is the
        Flask app instance (None in CLI / test contexts). Use for:
        registering Flask blueprints, attaching CLI subcommands,
        hooking the photo event bus."""
        if app is not None:
            from .blueprint import bp
            app.register_blueprint(bp)

    def on_library_open(self, ctx):
        """Per library, after WebAppState.startup() completes. Use for
        opening a side-cache DB, priming derived state, fetching
        per-library secrets."""
        self._sidecache = open_sidecache(ctx.workdir)

    def on_library_close(self, ctx):
        """Per library, before switch_library swaps the DB pool. Mirror
        of on_library_open — release resources opened there. Fires in
        REVERSE registration order (LIFO mirrors context managers)."""
        self._sidecache.close()

    def on_shutdown(self):
        """Process exit. Mirror of on_register. Also fires in LIFO order."""
        ...


def setup() -> None:
    register_plugin(MyPlugin())
```

Failure isolation — a plugin's exception in any hook is caught,
logged at WARNING, and the next plugin still fires. Startup is never
aborted by a plugin failure.

## Trust contract

A `bpp.plugins` entry-point runs **arbitrary Python code** in the bpp
process at startup, with the same privileges as bpp itself —
filesystem access, network access, the user's photo library, model
weights. Plugins are not sandboxed.

Treat plugin packages the way you'd treat editor extensions or shell
plugins: install only from sources you trust. The opt-in env var
exists so a malicious package landing in your venv (typo-squatting,
compromised dependency, accidental install) doesn't auto-execute on
the next `bpp serve`.

## Extension points

| Extension | Module | Register via |
|---|---|---|
| Face detector | `bpp.scoring.face_detector_registry` | `register_detector(FaceDetector(...))` |
| Face embedder | `bpp.scoring.face_embedder_registry` | `register_embedder(FaceEmbedder(...))` |
| Face extraction phase | `bpp.web.face_phase_pipeline` | `register_face_phase(phase, priority=...)` |
| Config field | `bpp.config_schema` | `register_field(ConfigField(...))` |
| Smart album type | `bpp.db.smart_albums` | `SmartAlbumRegistry.register(...)` |
| Smart-album refresh domain | `bpp.db.smart_albums` | `register_album_domain(domain, types, extend=True)` |
| Background worker | `bpp.web.state` | `WorkerRegistry.register(name, factory)` |
| ML model lifecycle | `bpp.scoring.model_base` | `ModelRegistry.register(ModelEntry(...))` |
| Operation recovery | `bpp.db.journal` | `register_recovery_handler(kind, handler)` |
| Custom scorer | `bpp.scoring.registry` | `register_scorer(ScorerDef(...))` |
| Custom export mode | `bpp.output.export` | `register_export_mode(name, handler, ...)` |
| Dedup strategy | `bpp.dedupe.strategy` | `register_dedupe_strategy(DedupeStrategy(...))` |
| Photo deletion lifecycle | `bpp.db.photo_hooks` | `register_photo_deletion_hook(callback)` |

Each registry is documented in its own module's docstring. The
intended call timing for `setup()`:

- **Face detector / embedder / config field**: register at any time
  during `setup()`. Consumed lazily as scoring runs and as
  `Config.get(...)` is read.
- **Smart album type**: register during `setup()`. Consumed lazily by
  `refresh_smart_albums()` (fires on import completion, face ops, hash
  computation).
- **Smart-album refresh domain**: when your plugin adds an album type
  that should refresh on a built-in mutation domain (face cluster,
  pet detect, dedup, edit, import), call
  `register_album_domain("face_cluster", ("smart_my_kind",),
  extend=True)` during `setup()`. `extend=True` appends to the existing
  built-in entry (deduped). Pass `extend=False` to replace the entry
  outright (rare — usually you want to coexist with the built-ins, not
  hide them).
- **Worker**: register during `setup()`. **Important**: workers are
  instantiated once per `WebAppState.__init__` from the registry, so a
  worker registered AFTER `WebAppState` is constructed won't run.
  Always register in `setup()`, not in a deferred callback.

## Custom ML models

If your plugin ships its own ONNX/TFLite model (custom face embedder, a
saturation scorer with weights, a domain-specific classifier), register
it through `ModelRegistry` so bpp manages download, integrity
verification, and reset/redownload semantics for free. Hand-rolling a
download path skips the SHA-256 supply-chain defense and the offline
"model unavailable" degradation path that the rest of bpp expects.

There are two layers:

1. **`ModelSingleton`** — manages the live, in-process model object
   (load on first use, double-checked locking, atomic instance swap on
   redownload, optional `import_check`, bundled-fallback path).
2. **`ModelRegistry`** — declarative metadata for the lifecycle UI
   (path / url / sha256 / reset hook). The Settings → Advanced →
   ML Models panel reads this registry to render Redownload /
   Uninstall affordances; `bpp.utils.download.download_file` reads the
   sha256 to verify after fetch.

Most plugins want both. Pattern:

```python
# my_pkg/my_model.py
from pathlib import Path

import onnxruntime  # your inference runtime

from bpp.scoring.model_base import ModelEntry, ModelRegistry, ModelSingleton

_MODEL_PATH = Path.home() / ".cache" / "bpp" / "my_plugin" / "my_model.onnx"
_MODEL_URL = "https://example.com/models/my_model.onnx"
_MODEL_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _create(path: Path | None):
    """Called once after ensure_model() has placed the bytes on disk
    and verified the SHA. Build and return your inference handle."""
    return onnxruntime.InferenceSession(
        str(path), providers=["CPUExecutionProvider"]
    )


_singleton = ModelSingleton(
    name="MyPlugin custom model",
    model_path=_MODEL_PATH,
    model_url=_MODEL_URL,
    model_sha256=_MODEL_SHA256,
    create_fn=_create,
    # bundled_path=str(Path(__file__).parent / "weights" / "my_model.onnx"),
    # ↑ optional: ship a copy in your wheel; bpp uses it when offline.
)


def get_model():
    """Lazy accessor for your scoring code. Returns None if unavailable."""
    return _singleton.get()


# Register the lifecycle entry so Settings → Advanced → ML Models
# shows your model with Redownload / Uninstall affordances and the
# pending-download consent prompt before first analyze includes it.
ModelRegistry.register(
    ModelEntry(
        name="MyPlugin custom model",  # must match ModelSingleton.name
        path=str(_MODEL_PATH),
        url=_MODEL_URL,
        sha256=_MODEL_SHA256,
        reset=_singleton.reset,
    )
)
```

Then call `get_model()` from your scoring callable (face detector,
custom scorer, etc.). Don't call `_singleton.get()` from `setup()` —
that would force the model load on every bpp startup, defeating the
on-demand contract. Plugin `setup()` only registers; first inference
triggers the actual download + load.

**Lifecycle guarantees you get:**

- Model file downloaded once on first `get()`, verified against
  `sha256` post-download, written via tmp+rename so a partial download
  never gets used.
- 120s timeout enforced by `download_file()`; long-stalled downloads
  fail loud rather than blocking forever.
- Redownload from Settings → Advanced calls your `reset` callback so
  the next `get()` rebuilds against the fresh bytes (atomic swap, no
  "ghost" inference against the old in-memory model).
- Bundled-fallback path: if `bundled_path` is set and the network is
  unreachable, bpp uses the bundled copy and skips download.
- Pending-download consent: bpp's first-analyze consent prompt
  enumerates pending downloads — your model appears in that list with
  its name, URL, and size, so users see exactly what's about to be
  fetched.

**Constraints:**

- `path` is resolved at registration time. If you support env-var
  overrides (e.g. `BPP_MY_PLUGIN_MODELS_DIR`), read them in your
  module, NOT in the registry registration call.
- `sha256` is required for any `url` you trust the model lifecycle
  for. None means "no integrity check" — the test suite warns on
  this. ONNX/TFLite/PyTorch deserialization is RCE on substituted
  bytes; please pin the SHA.
- `reset` must be idempotent and safe to call from any thread.
  `ModelSingleton.reset` already meets this; if you roll your own
  module-global cache, mirror its lock-then-clear pattern.

See `examples/plugin_example/bpp_example_plugin/plugin.py` for a
working `ModelRegistry` registration in the demo plugin.

## Crash-resumable operations

If your plugin runs a multi-step mutation that can leave half-finished
state on the disk if the process is SIGKILL'd or crashes (think:
batch-export to a remote bucket, large transformation pipeline,
custom indexer rebuild), use the operation journal so the next bpp
startup can clean up — or resume — automatically.

The contract has two halves: the call site brackets the work with
`journal_start` / `journal_complete`, and a plugin-supplied recovery
handler picks up any entries that didn't reach `journal_complete`.

```python
# my_pkg/my_indexer.py
import sqlite3

from bpp.db.journal import (
    journal_complete,
    journal_start,
    register_recovery_handler,
)


def rebuild_index(conn: sqlite3.Connection, paths: list[str]) -> None:
    """Long-running mutation. Survives a mid-flight crash."""
    journal_id = journal_start(
        conn,
        kind="my_plugin_index_rebuild",  # globally unique; namespace it
        payload={"paths": paths, "version": 1},
    )
    try:
        for p in paths:
            _index_one(conn, p)  # commits per-row
        journal_complete(conn, journal_id)
    except Exception:
        # Don't journal_complete — leave the entry so recovery picks it up.
        raise


def _recover(conn: sqlite3.Connection, payload: dict) -> bool:
    """Called once on next startup if rebuild_index was interrupted.

    Return True to mark the journal entry recovered (it gets deleted),
    False to leave it for manual investigation. False is the right
    answer when you don't recognize the payload schema (the user may
    have downgraded bpp; don't drop their breadcrumb).
    """
    paths = payload.get("paths") or []
    if not isinstance(paths, list):
        return False  # corrupt payload — don't claim recovery
    # Any partial state: re-index the affected paths idempotently.
    for p in paths:
        _index_one(conn, p)
    return True


def setup_recovery() -> None:
    """Call from your plugin's setup() so a crashed run doesn't strand
    rows in your index forever. Bpp invokes this handler once per
    startup, after schema migration, before any UI is reachable."""
    register_recovery_handler(
        "my_plugin_index_rebuild",
        _recover,
        # `replace=True` is recommended for plugins — bpp's library-
        # switch path rebinds handlers whose closures captured the old
        # context. Without replace=True, a `bpp serve` followed by an
        # in-app library switch would raise on the second registration.
        replace=True,
    )
```

**Lifecycle guarantees:**

- `journal_start` commits before your work begins, so a SIGKILL on
  the very next line still leaves a recoverable entry on disk.
- `journal_complete` (single DELETE) is idempotent — calling it twice
  is harmless.
- `recover_pending` runs handlers for ALL pending kinds at startup,
  including ones from kinds you don't know about (your handler is
  only invoked for the kinds you registered). Plugins compose
  cleanly: the user can install N plugins, each with its own kind
  and handler, and they all recover independently.
- A handler that raises is logged with `exc_info` and its journal
  entry is left in place. Other handlers for other kinds still run.
  Recovery never aborts startup.

**Constraints:**

- `kind` is a free-form string. Namespace it (e.g.
  `"acme_plugin_rebuild"`) so it can't collide with a future bpp
  built-in kind. Recommended: `<plugin_name>_<operation>`.
- `payload` must be JSON-serializable. Bpp parses it with
  `safe_json_loads` on recovery — corrupt JSON yields `{}`, not an
  exception. If your handler depends on payload fields, validate
  them before mutating; treat unknown shapes as "not mine, leave
  it" by returning False.
- The handler runs **before any worker has started**, so `conn` is
  the only resource you can rely on. Don't try to enqueue background
  work from a recovery handler — that's what worker startup is for.
- Recovery handlers are global, not per-library. If you switch
  libraries during a session, the journal table in the NEW library
  is checked, but the handler closure was registered against the
  old context. Use `replace=True` and re-register on every plugin
  setup() to keep this clean.

See `bpp/web/clip_worker.py::register_clip_extraction_recovery` and
`bpp/web/bp_photos_manage.py` for built-in usage patterns.

## Custom scorers

The built-in scoring pipeline runs blur, exposure, face, and
composition; their per-photo outputs feed into a weighted
``aggregate_score``. Plugins can add their own scorers — saturation,
subject count, color harmony, OCR-text density, anything that has a
0..1 metric per photo.

```python
# my_pkg/saturation_scorer.py
from typing import Any

import cv2
import numpy as np

from bpp.scoring.registry import ScorerDef, register_scorer


def saturation_score(
    img: np.ndarray, filepath: str, config: dict[str, Any]
) -> dict[str, Any]:
    """Run during analyze. Returns a dict merged into the photo result."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return {
        "myplugin_saturation_score": float(hsv[:, :, 1].mean()) / 255.0,
    }


def setup_scorer() -> None:
    register_scorer(
        ScorerDef(
            key="myplugin_saturation",          # plugin-prefixed; reserved
                                                # built-in keys are blocked
            weight_key="myplugin_saturation_weight",  # optional
            default_weight=0.0,                  # 0 = no aggregate effect
            aggregate_default=0.5,               # fallback when score absent
            optional=True,                       # required for plugins
            toggle_key="model_myplugin_saturation",
            score_fn=saturation_score,
            api_fields={"myplugin_saturation_score": 0},
        ),
        replace=True,
    )
```

**What you get:**

- `score_fn` runs once per photo during analyze, inside
  `run_optional_scorers()`. The image is already decoded and
  resized; you don't pay decode costs.
- The dict you return is merged into the in-memory photo result.
  Any keys you list in `api_fields` flow through to the JSON API
  response (during the analyze run — see persistence note below).
- If `weight_key` is set and `default_weight > 0`, your scorer joins
  the weighted average that produces `aggregate_score`. The user
  can tune the weight via YAML config (`myplugin_saturation_weight:
  0.05`). The Settings UI sliders today are static HTML; plugin
  scorers don't get a slider until a contributor ports the slider
  panel to render the registry dynamically.
- `toggle_key` gives the user a master on/off switch (read from
  `model_toggles`). Default to True if you want the scorer to run
  out-of-the-box; default to False if it's heavy (model-backed) and
  the user should opt in.

**What you DON'T get:**

- **No DB columns.** ``db_columns`` MUST be empty in plugin
  ScorerDefs — `bulk_upsert_photos` would silently drop columns the
  `photos` table doesn't have. Your per-photo score lives in the
  in-memory analyze result; if a user opens a photo's lightbox
  immediately after analyze, the field is in the API response, but
  after a server restart (or any path that rebuilds the photo dict
  from DB), the field reverts to its `api_fields` default.
- **No persistence beyond aggregate_score.** `aggregate_score` IS
  persisted, so a weighted plugin scorer's effect on ranking
  survives restart — but the individual `myplugin_saturation_score`
  field does not. If you need persistence, write to your own DB
  table from your plugin code.
- **No automatic UI slider.** Plugin scorers run, but the Settings
  → Scoring panel doesn't render a slider for them yet. Document
  the YAML key in your plugin README so users know how to override
  `default_weight`.

**Constraints:**

- `key` MUST be plugin-prefixed. Reserved (built-in) keys: `blur`,
  `exposure`, `face`, `composition`, `skin`, `nudity`, `pets`,
  `aggregate`. Registering one of these raises (use `replace=True`
  only if you really mean to override a built-in, which is almost
  never the right call).
- `optional=True` and `score_fn` are required. The lazy-wire path
  for built-in optionals (`_wire_optional_scorers`) is bpp-internal.
- `default_weight=0` is the safe default. Setting a non-zero default
  re-normalizes `aggregate_score` for every existing photo on next
  analyze — disclose this in your plugin's README.

**Performance:**

- `score_fn` runs sequentially on the analyze worker thread per
  photo. Heavy work (model inference) should batch where possible
  via thread-local state. Avoid loading models in `score_fn`; load
  on first use and cache via `ModelSingleton` (see "Custom ML
  models" above).

See `examples/plugin_example/bpp_example_plugin/plugin.py` —
`_register_scorer()` registers a working `example_saturation` demo.

## Custom export modes

The built-in export pipeline ships three modes — `copy`, `hardlink`,
`symlink` — selected by the user from the Export modal's "How to copy"
dropdown. Plugins can add their own modes (S3 upload, encrypted ZIP,
SFTP push, "Export to iPhoto", anything per-photo) via
`register_export_mode()`.

```python
# my_pkg/s3_export.py
import boto3

from bpp.output.export import register_export_mode


_s3 = None


def _client():
    """Lazy S3 client — don't construct it during setup()."""
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def s3_upload_export(src: str, dest: str) -> None:
    """Handler contract: (src, dest) -> None. Raise on failure."""
    # `dest` is the fully-resolved per-photo destination path — bpp
    # has already applied safe_join and created the parent dir. Use
    # whatever part of the path makes sense for your destination
    # (here: the basename becomes the S3 key).
    import os

    bucket = os.environ.get("MYPLUGIN_S3_BUCKET")
    if not bucket:
        raise RuntimeError(
            "MYPLUGIN_S3_BUCKET unset — refusing to upload"
        )
    key = os.path.basename(dest)
    _client().upload_file(src, bucket, key)


def setup_export_mode() -> None:
    register_export_mode(
        "myplugin_s3",                        # plugin-prefixed name
        s3_upload_export,
        description="Upload selected photos to MYPLUGIN_S3_BUCKET.",
        replace=True,                         # safe across library switches
    )
```

Once the user calls the Export API with `mode="myplugin_s3"`, bpp
loops through the selected photos and calls your handler once per
photo with `(src, dest)`.

**What you get:**

- Built-in pre-flight: `outdir` validated and created, per-photo
  destination computed via `safe_join`, parent dir created. Your
  handler receives a fully-formed dest path.
- Per-photo failure isolation: if your handler raises on one photo,
  bpp records the failure and continues with the rest of the batch.
  An exception does not abort the whole export.
- Manifest + report integration: as long as you accept that `dest`
  may not be a real local file after your handler returns (you
  uploaded it instead), bpp's manifest.json + report.txt are still
  written with the file metadata your handler decided on.

**What you DON'T get:**

- **No automatic UI dropdown.** The Export modal's "How to copy"
  control is hardcoded HTML today. Plugin modes are reachable via
  the API (`POST /api/v1/export` with `mode="myplugin_s3"`), but
  the dropdown won't list them until a contributor ports the
  dropdown to render from `ExportModeRegistry.all()`. Document the
  mode name in your plugin README so users can wire it up via API
  or a custom UI.
- **No image processing in plugin modes.** When the user picks
  `copy` mode with `fmt=jpeg` or a `max_size` cap, bpp processes
  the image with PIL BEFORE the handler is consulted. For other
  modes (yours included), bpp passes the ORIGINAL bytes. If your
  mode needs format conversion / resizing, do it inside your
  handler.
- **No per-photo metadata stripping in plugin modes.** The metadata
  scrub (`strip_metadata=True` default) only fires for `mode="copy"`
  on still-image extensions. Plugin modes get the original bytes
  including EXIF. Document this if your mode is recipient-facing.

**Constraints:**

- `name` MUST be plugin-prefixed (e.g. `myplugin_s3`). Built-in
  names (`copy`, `hardlink`, `symlink`) are reserved.
- Re-registering the same handler is idempotent. Different handler
  for the same name requires `replace=True`.
- Handlers should be cheap to construct — defer expensive setup
  (network clients, credential reads, model loads) to the first
  call rather than module import time, the same way `ModelSingleton`
  defers ML model loads.

See `examples/plugin_example/bpp_example_plugin/plugin.py` —
`_register_export_mode()` registers a working `example_sidecar` demo
that copies the photo and drops a JSON sidecar next to it.

## Photo deletion lifecycle

Plugins that maintain side-state (a custom index, a backup mirror, a
webhook notifier) often need to react when bpp deletes, restores, or
permanently removes a photo. Register a callback with
`bpp.db.photo_hooks.register_photo_deletion_hook`:

```python
# myplugin.py
from bpp.db.photo_hooks import register_photo_deletion_hook


def on_photo_change(conn, photo_ids, kind):
    """Called after every soft / restore / permanent delete.

    Args:
        conn: active sqlite3 connection (already committed)
        photo_ids: list[int] of affected photo IDs
        kind: "soft" | "restore" | "permanent"
    """
    if kind == "soft":
        my_index.mark_hidden(photo_ids)
    elif kind == "restore":
        my_index.mark_visible(photo_ids)
    elif kind == "permanent":
        my_index.drop(photo_ids)


def setup(api):
    register_photo_deletion_hook(on_photo_change)
```

Contract:

- The hook fires **after** the DB write commits, so the callback
  sees the new state. For `"permanent"`, the photo rows are already
  gone — if your plugin needs the old filepaths or metadata, subscribe
  to `"soft"` instead (deleted_at is set but the row is intact).
- Exceptions raised by the hook are **caught and logged at WARNING**.
  A bad plugin will not break the user-facing delete.
- `photo_ids` may include IDs of rows the DB write didn't actually
  modify (e.g. a second soft-delete on already-deleted rows). The
  caller passed those IDs, so the hook sees them. Check row state
  yourself if your plugin needs to distinguish.
- Multiple hooks can register; each receives the same `(conn, ids, kind)`
  tuple. Order matches registration order.

`unregister_photo_deletion_hook(callback)` removes a hook (returns
`True` if found). Useful in tests; production plugins typically
register once and never deregister.

## Custom dedup strategies

Bpp ships three built-in dedup strategies — `hash` (perceptual dHash +
aHash with EXIF time windowing), `clip` (CLIP cosine-similarity), and
`none` (skip). The recompute pipeline auto-picks: `clip` when CLIP
embeddings are loaded, else `hash`, with `none` reachable via the
explicit `--skip-dedupe` flag.

Plugins register additional named strategies via
`register_dedupe_strategy()`. The user opts in by setting
`dedupe_strategy: <name>` in their bpp config (YAML or DB-backed),
which **overrides** the auto-pick for the rest of the recompute path.

```python
# my_pkg/perceptual_dedup.py
from typing import Any

from bpp.dedupe.strategy import (
    DedupeStrategy,
    register_dedupe_strategy,
)


def perceptual_dedup_fn(
    items: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    clip_embeddings: dict | None = None,
    **_unused,
) -> list[dict[str, Any]]:
    """Plugin strategy contract:

    items:           per-photo analysis dicts (filepath, aggregate_score,
                     phash/ahash, optional id/date/date_month/...).
    config:          merged runtime config (read your tunables from here).
    clip_embeddings: dict[photo_id -> ndarray] when bpp has loaded them;
                     None if your strategy was selected before embeddings
                     were available. Set requires_clip_embeddings=True on
                     your DedupeStrategy if you need them — bpp's
                     resolve_strategy() will fall back to the auto-pick
                     instead of crashing your strategy on a None lookup.

    Return: list of representative dicts (one per cluster). Each
    representative SHOULD set `cluster_size`; semantic strategies
    typically also attach `similar_photos: [{filepath, similarity, ...}]`
    so the lightbox renders cluster siblings.
    """
    # ... your clustering ...
    out: list[dict[str, Any]] = []
    for item in items:
        item["cluster_size"] = 1
        out.append(item)
    return out


def setup_dedupe() -> None:
    register_dedupe_strategy(
        DedupeStrategy(
            name="myplugin_perceptual",         # plugin-prefixed
            dedupe_fn=perceptual_dedup_fn,
            description="Perceptual-quality-aware dedup.",
            requires_clip_embeddings=False,
        ),
        replace=True,
    )
```

**Activation:**

The user adds one line to their bpp config:

```yaml
# ~/.config/bpp/config.yaml (or via Settings → Advanced once that
# YAML editor lands)
dedupe_strategy: myplugin_perceptual
```

Or via the DB-backed settings table (`UPDATE settings SET
value='myplugin_perceptual' WHERE key='dedupe_strategy'`). bpp
reloads the config on the next recompute and picks up the override.

**Lifecycle guarantees:**

- `resolve_strategy()` is the dispatch helper. It is called inside
  `recompute._do_recompute_internal()` BEFORE the auto-pick logic.
  Plugin strategy → use it. Unknown name / `requires_clip_embeddings`
  but no embeddings loaded → log a warning and fall back to the
  auto-pick. **Plugins never crash the recompute.**
- If your strategy raises, the exception propagates to the recompute
  caller. The recompute endpoint surfaces it as an HTTP 500 and logs
  with `exc_info`. Don't raise — return the input list unchanged for
  pathological cases.
- `is_builtin=False` is the default for plugin strategies. The
  `_reset_for_tests` helper preserves built-ins so test isolation only
  drops plugin entries.

**What you DON'T get:**

- **No automatic UI selector.** The Settings panel doesn't render a
  "Dedup strategy" dropdown today. Plugin strategies are reachable via
  the config key; document the YAML key in your plugin README. A
  contributor wiring a registry-driven UI would unblock this.
- **No persistence beyond the recompute output.** A plugin strategy's
  cluster decisions live in the in-memory recompute response. The
  next recompute calls your strategy fresh. If you need per-photo
  cluster IDs persisted, write to your own DB table.
- **No retroactive effect on existing smart-album dedup.** The
  "Duplicates" smart album type has its own logic that consults the
  built-in phash directly (`bpp/db/smart_albums.py`); your plugin
  strategy does not change which photos appear in that album. To
  ship a custom "Duplicates" album, register a smart-album type in
  parallel (see "Smart album type" row above).

**Constraints:**

- `name` MUST be plugin-prefixed (e.g. `myplugin_perceptual`).
  Reserved built-in names: `hash`, `clip`, `none`.
- `dedupe_fn` MUST accept `(items, config, **kwargs)`. Plugins that
  ignore `clip_embeddings` should declare a `**_kwargs` catch-all
  rather than enumerate every keyword bpp may pass — the kwargs set
  may grow in future versions.
- Re-register same `DedupeStrategy` is idempotent. Different
  function for the same name requires `replace=True`.

See `examples/plugin_example/bpp_example_plugin/plugin.py` —
`_register_dedupe_strategy()` registers a working
`example_score_dedup` demo.

## Versioning & stability

bpp follows semver. Plugins should pin a compatibility range against
the bpp version they were built for, so a future major release that
breaks a registry signature **skips** an incompatible plugin instead
of letting it crash mid-registration on a missing field.

Declare three optional module-level constants on the module that hosts
your `setup()` entry point:

```python
# my_pkg/bpp_plugin.py
__plugin_name__ = "my-bpp-plugin"
__plugin_version__ = "1.2.3"
__bpp_version_required__ = ">=0.1,<1.0"


def setup() -> None:
    ...
```

Loader behaviour:

- **`__plugin_name__`** is used in log lines instead of the entry-point
  id, so `Loaded plugin my-bpp-plugin v1.2.3` reads better than
  `Loaded plugin example=my_pkg.bpp_plugin:setup`. Falls back to the
  entry-point id when missing.
- **`__plugin_version__`** is logged alongside the name. It is *not*
  used for any internal compatibility logic — it's purely a
  human-readable tag so an operator scanning logs can correlate
  registrations with the installed plugin wheel.
- **`__bpp_version_required__`** is parsed with
  `packaging.specifiers.SpecifierSet`. If the running `bpp.__version__`
  doesn't satisfy the spec, the loader logs a warning and **skips
  setup()**. A plugin built against a future bpp major can land in
  the venv without breaking startup. Empty / missing / unparseable
  specs are treated as "no constraint" — the plugin loads.

The Protocol class `bpp.plugins.BppPluginAPI` documents this contract.
It's `@runtime_checkable`, so a plugin author who wants editor type-
checking can `# type: ignore[abstract]` an `assert isinstance(module,
BppPluginAPI)` smoke test in their package's own tests. Adopting the
Protocol is fully optional; the loader only reads the constants via
`getattr(...)`.

What bpp commits to:

- **Registry signatures stay stable across minor versions.** Adding a
  new optional field on `ScorerDef`, `ExportMode`, `DedupeStrategy`,
  `ModelEntry`, `FaceDetector`, `FaceEmbedder`, or `ConfigField` is a
  minor bump; removing or repurposing a field is a major bump.
- **`register_*` keyword arguments are append-only across minors.**
  Existing kwargs keep their meaning; new kwargs default to a value
  that preserves the pre-kwarg behaviour.
- **Reserved (built-in) registry names don't move between majors.**
  `hash` / `clip` / `none` (dedup), `copy` / `hardlink` / `symlink`
  (export), `blur` / `exposure` / `face` / `composition` (scoring) —
  if any of these reserved names ever change, that's a major bump and
  shows up in CHANGELOG.md → "Breaking changes".
- **Removed registries get a deprecation warning two minor versions
  ahead of removal.** A registry that's slated for removal in 0.5.0
  starts logging a `DeprecationWarning` from 0.3.0 onward, so a plugin
  author has at least one minor cycle to migrate.

What plugins should commit to:

- Pin a lower bound (the bpp version you tested against) and an
  exclusive upper bound on the next major (`>=0.4,<1.0`). This is the
  setuptools convention and what `__bpp_version_required__` will
  parse cleanly.
- Treat the `**kwargs` catch-all in callbacks (scorer `score_fn`,
  dedup `dedupe_fn`, export handler) as required — bpp may pass new
  keyword arguments in future minor versions and a callable that
  enumerates kwargs without `**kwargs` will break.
- Pin the bpp dependency in your `pyproject.toml` to the same range
  you put in `__bpp_version_required__`. The runtime check is a
  belt-and-suspenders backstop; your wheel's pip resolver should
  refuse to install on an incompatible bpp in the first place.

## Loader behaviour

- **Idempotent**: the loader tracks loaded entry-points in a
  process-global set, so the three startup paths (`bpp serve`,
  `bpp analyze`, `bpp pick`) calling it in the same process don't
  double-invoke `setup()`.
- **Best-effort**: a plugin whose import or `setup()` raises is
  logged at WARNING with `exc_info` and skipped. Other plugins
  continue. Startup is never aborted by a plugin failure.
- **Non-callable target**: if the entry-point loads to a non-callable
  (the plugin is relying purely on import-time side effects), the
  loader logs an INFO breadcrumb and proceeds.
- **Version mismatch**: a plugin whose `__bpp_version_required__`
  doesn't match the running bpp is skipped with a warning. The entry
  is marked processed so a subsequent `load_plugin_entry_points()`
  call doesn't retry.

## Disabling plugins on a running deployment

Unset or set `BPP_ENABLE_PLUGINS` to anything other than `1` / `true`
/ `yes` / `on` (case-insensitive). The next process restart picks up
the change — there's no live re-load. Plugins always run from
`setup()` at startup; once registered, an entry can't be unregistered
without restarting.

## Authoring a plugin: checklist

- [ ] Declare `[project.entry-points."bpp.plugins"]` in
      `pyproject.toml` with one or more named entries.
- [ ] Each entry's value is a `pkg.module:callable` reference — the
      callable takes zero args.
- [ ] The callable performs all `register_*` calls synchronously.
- [ ] Don't open files, hit the network, or block in `setup()` —
      it's part of bpp's startup path. Defer expensive work to the
      detector / embedder / scorer call site.
- [ ] Document your plugin's `BPP_ENABLE_PLUGINS=1` requirement in
      your own README so users aren't surprised.
- [ ] Pin a `bppicker>=` lower bound in your dependencies so a future
      bpp release that drops a registry doesn't silently no-op your
      plugin.
- [ ] Declare module-level `__plugin_name__`, `__plugin_version__`,
      and `__bpp_version_required__` (PEP 440 specifier) so the loader
      logs friendly identifiers and skips your plugin cleanly when an
      incompatible bpp is installed. See *Versioning & stability* for
      the full contract.

## Lifecycle hooks

`setup()` is fired once at process startup. For per-library or
per-request work, implement a plugin class with any subset of the four
lifecycle methods below and pass an instance to `register_plugin`:

```python
# my_pkg/bpp_plugin.py
from bpp.plugin_protocol import register_plugin

class MyPlugin:
    def on_register(self, app):
        """Called once at process startup (after setup()). app is the
        Flask app, or None in CLI / test contexts. Use for: blueprint
        attach, CLI subcommands, photo event bus subscribers."""

    def on_library_open(self, ctx):
        """Called once per library after ctx.startup() completes. Use
        for: opening a per-library resource (a side-cache DB, a
        per-library model singleton)."""

    def on_library_close(self, ctx):
        """Called once per library, BEFORE switch_library swaps the
        DB. Mirror of on_library_open. Use for: releasing the
        resource."""

    def on_shutdown(self):
        """Called once at process exit. Mirror of on_register."""

def setup():
    register_plugin(MyPlugin())
```

Firing semantics:

* `on_register` and `on_library_open` fire in registration order
  (first-registered runs first).
* `on_library_close` and `on_shutdown` fire in *reverse* registration
  order — symmetric with nested context managers (LIFO cleanup).
* All four methods are optional; the host checks `hasattr` before
  calling. A plugin that needs only `on_library_open` defines just
  that method.
* If your hook raises, the host logs a WARNING with `module.Class`
  and the method name, then continues with the next plugin. One
  broken plugin can't break another's lifecycle.

The reference implementation lives at `bpp/plugins/example.py`; the
test suite drives every hook in
`tests/test_p5_plugin_lifecycle_and_indexed_smart_person.py`.

## Smart-album mutation domains

When you register a custom smart-album type via `SmartAlbumRegistry`,
the album's `refresh_fn` is invoked from
`bpp/db/smart_albums.py:refresh_smart_albums()`. But that helper takes
a `kinds=` argument so callers can refresh only the album types
affected by their mutation. Built-in mutation domains
(`face_cluster`, `pet_detect`, `tag_assign`, …) map to specific album
types; a plugin's custom type doesn't show up in those tables unless
you tell bpp about it:

```python
from bpp.db.smart_albums import SmartAlbumRegistry, register_album_domain

SmartAlbumRegistry.register(
    "smart_my_kind",
    _my_refresh,
    _my_get_ids,
)
register_album_domain(
    "face_cluster",            # bpp built-in domain to extend
    ("smart_my_kind",),         # your album type(s) to refresh on it
    extend=True,                # don't replace the existing tuple
)
```

Without the `register_album_domain` call, a face-cluster mutation
won't trigger your refresh — the album will only update on a full
unscoped refresh (which the UI does on a slower cadence). The example
plugin demonstrates the pattern at `bpp/plugins/example.py:175-185`.

## Post-event hooks

For reactive plugin work (side-mirror to a backup store, send a
webhook, enrich result dicts) bpp exposes three event buses in
`bpp.db.event_hooks` modelled on the existing photo deletion bus:

```python
from bpp.db.event_hooks import (
    register_post_analyze_hook,
    register_post_cluster_hook,
    register_post_import_hook,
)

def _on_post_analyze(conn, results):
    """Fires after AnalyzeWorker writes the result dicts. results is
    a list[dict[str, Any]]; plugins may mutate in place. The DB has
    already committed."""

def _on_post_cluster(conn, kind, n_clusters):
    """Fires after face / pet clustering writes cluster IDs. kind is
    'face' / 'pet' (or a plugin-prefixed string)."""

def _on_post_import(conn, photo_ids, filepaths):
    """Fires after an import batch commits. photo_ids and filepaths
    are parallel."""

register_post_analyze_hook(_on_post_analyze)
register_post_cluster_hook(_on_post_cluster)
register_post_import_hook(_on_post_import)
```

Same trust contract as the lifecycle hooks: a callback that raises is
logged at WARNING and the dispatch continues with the next subscriber.
Empty payloads (no imported photos, no analyzed results) short-circuit
the dispatch — your plugin isn't woken up for nothing.

## Inserting custom face-extraction phases

The face-extraction orchestrator is a sequence of seven explicit
phases (method reconciler → preload → partition → stale delete →
dismissed snapshot → extract → cluster → identity). Each phase
implements `bpp.web.face_phase_pipeline.FacePhase`. Plugins can splice
their own phase between any two built-ins:

```python
from bpp.web.face_phase_pipeline import register_face_phase

class MyValidatePhase:
    name = "my_validate_clusters"
    journal_bit = -1  # opt out of journal skip/mark — recompute every run

    def should_skip(self, ctx): return False
    def rehydrate(self, ctx): self.run(ctx)
    def run(self, ctx):
        # Inspect ctx.extraction.all_records, raise if a sanity check fails
        ...

register_face_phase(MyValidatePhase(), priority=650)  # after cluster (600), before identity (700)
```

Built-in priorities: `method_reconcile=100`, `preload=200`,
`partition=300`, `stale_delete=400`, `dismissed_slots=450`,
`extract=500`, `cluster=600`, `identity=700`. Wide gaps so plugins can
slot in cleanly. Use `priority=50` for "before all built-ins" or
`priority=999` for "after everything." Re-registering an existing
name without `replace=True` raises.
