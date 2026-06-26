# Contributing to Best Photo Picker

Thanks for your interest in contributing! This guide will help you get started.

## Git workflow

- **`develop`** is the integration branch. Feature work merges here.
- **`main`** is the release branch. Only `develop` → `main` merges are allowed, and only when a release is being cut.
- Feature branches: `feat/<topic>` or `fix/<topic>`, branched off `develop`, merged back to `develop`.
- Never push directly to `main`. Always go through a PR.
- **CI runs on PRs to `main`** (release gate). Day-to-day development on `develop` is verified locally with the gate commands below. Python 3.11 only — matches the Tauri sidecar pin.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/Arkalogy/best-photo-picker.git
cd best-photo-picker

# ── Python ──
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# ── Pre-commit hooks (catches eslint-globals.json drift before commit) ──
pre-commit install

# ── Frontend dev tooling (optional but recommended) ──
# Node 20+ required; installs ESLint, Prettier, Vitest, TypeScript
npm install

# Verify everything works
pytest -v                       # ~3000 tests
ruff check . && ruff format --check .
npm run lint                    # ESLint + auto-generated globals
npm run format:check            # Prettier (scoped to dev-tool JS)
npm run typecheck               # tsc --noEmit on @ts-check files
npm run test:js                 # Vitest + jsdom
```

## Making Changes

1. **Create a branch** from `develop` for your work
2. **Write tests** for new functionality or bug fixes
3. **Run the checks** before committing:
   ```bash
   # Python
   pytest -v
   ruff check . && ruff format .

   # JS (if you touched any JS/CSS/HTML)
   npm run lint && npm run typecheck && npm run test:js
   ```
4. **Keep commits focused** — one logical change per commit
5. **Open a pull request** with a clear description of what changed and why

## Project Structure

```
bpp/
  scoring/      # Image analysis (blur, exposure, faces, composition)
  dedupe/       # Perceptual hash deduplication
  selection/    # Greedy selection with diversity constraints
  output/       # Export (JSON, CSV, HTML, file copy)
  db/           # SQLite database layer
  web/          # Flask web UI and background workers
  utils/        # Logging, config helpers
tests/          # pytest test suite
```

## Code Style

### Python
- **Linter**: ruff (rules: E, F, W, I, UP, B, SIM, RUF)
- **Formatter**: ruff format (100 char line length)
- **Type hints**: use them for function signatures
- **Logging**: use `get_logger(__name__)`, never `print()`

### JavaScript
- **Linter**: ESLint 9 with an auto-generated globals allowlist
  (`.eslint-globals.json`, committed; CI verifies it's in sync)
- **Formatter**: Prettier (scoped to dev-tool JS — `tests-js/`,
  `scripts/*.mjs`, configs). Runtime `bpp/web/static/js/` is not
  auto-formatted yet; match existing style by eye.
- **Type checking**: `tsc --checkJs` on files with `// @ts-check`.
  Add JSDoc `@param` / `@returns` to new pure helpers.
- **Tests**: Vitest + jsdom under `tests-js/`. Prefer module-style
  imports (see `bpp/web/static/js/modules/` POC) for new code.

**Local gates** — run these before opening a PR; CI runs the same set on every PR to `main`:

| Command | What it checks |
|---|---|
| `npm run lint` | Regenerates `.eslint-globals.json`, then runs ESLint. Catches `no-undef`, dupe keys, unreachable code, `==`/`===` sanity. |
| `npm run format:check` | Prettier on dev-tool JS (`tests-js/`, `scripts/*.mjs`, configs). |
| `npm run typecheck` | `tsc --noEmit` on files annotated with `// @ts-check`. JSDoc is honored. |
| `npm run test:js` | Vitest + jsdom suite under `tests-js/`. |
| `npm run test:e2e:list` | `playwright test --list` — parse-only check, no browser/server. CI runs this on every PR. |
| `npm run test:e2e` | Full Playwright suite under `tests-e2e/`. Requires server on :5001 with a populated library; runs on demand in CI via the `E2E` workflow. |
| `python -m build --sdist --wheel` | Build smoke — catches packaging breakage (missing `MANIFEST.in` entries, broken entry points) before `publish.yml` runs in CI. |

At minimum, run `npm run lint && npm run typecheck && npm run test:js` before committing JS changes.

## Project invariants

The codebase enforces a handful of conventions. Most are caught by
tests or lint, but a few rely on review. Apply them to every PR.

### Data access (Python)

- **DB connections**: always use `get_db()` from `bpp/db/connection.py`. Never call `sqlite3.connect()` directly. Never `conn.close()` a pool connection — `close_all_connections()` owns lifecycle via `_safe_run()`.
- **DB queries in loops**: don't do `for x in items: conn.execute(... x ...)`. Batch with `IN` clauses, `executemany()`, or pre-load into a dict for O(1) lookup.
- **JSON from external sources**: always `safe_json_loads()` (in `bpp/utils/json_safe.py`). Never raw `json.loads()` on DB blobs, network responses, or disk reads.
- **File downloads**: always `download_file()` from `bpp/utils/download.py`. Enforces a 120-second timeout to prevent indefinite hangs. Never raw `urlretrieve`.

### Constants & sentinels

- **Cluster sentinels**: use `CLUSTER_UNASSIGNED` / `CLUSTER_DISMISSED` from `bpp/constants.py`. Never hardcode `-1` or `-2`. (Same rule on the JS side: import from `bpp/web/static/js/modules/constants.mjs`.)
- **Photo cache variants**: add new suffixes to `PHOTO_CACHE_SUFFIXES` in `bpp/constants.py`. Never hardcode suffix lists in cleanup code (`_invalidate_photo_cache`, `remove_for_hash`) — variants will leak across cache invalidation.

### Registries (single source of truth)

These registries are test-enforced; adding a new entry should require touching only one file:

- **Workers** — `_workers` dict in `bpp/web/state.py`
- **Smart album types** — `_SMART_ALBUM_TYPES` in `bpp/db/smart_albums.py`
- **Face detectors / embedders** — registries in `bpp/scoring/face_detector_registry.py` and `face_embedder_registry.py`

A new worker / album type / detector that bypasses the registry is a review finding.

### ML models

- **New models must use `ModelSingleton`** from `bpp/scoring/model_base.py`. Don't hand-roll globals + locks. (YuNet / SFace / dlib are exempt — they predate the singleton and use thread-local or no-download semantics.)
- **CLIP ONNX models**: select outputs by name (`'embeddings'`), never by index — the image and text encoders have different output ordering.

### Adding, modifying, or removing models

Any PR that touches the model registry, the download chokepoint, or anything under `bpp/registry/` must follow [`MODEL_POLICY.md`](MODEL_POLICY.md). Short version:

- No binary model weights in the repo. Weights live on the user's machine after first-use download.
- No new model URLs without an upstream-license review attached to the PR.
- No new restricted models without an issue first, plus code-owner approval on the PR.
- No restriction-class relaxations in the bundled baseline without two code owners and a written rationale.
- All model downloads must route through `bpp.utils.download.download_file()`.
- Acceptance text (`CANONICAL_DISCLAIMER` etc.) is versioned and append-only; edits bump the version constant.

The full reasoning, the signed-remote-registry rules, and the test gates that enforce all of the above live in [`MODEL_POLICY.md`](MODEL_POLICY.md). If a rule seems inconvenient, that is the rule working as intended.

### Web layer

- **Photo API responses**: use `build_photo_dict()` from `bpp/web/photo_dict.py`. Don't hand-build dicts in endpoints — fields drift.
- **Cached image generation**: use `_generate_cached_image()` from `bpp/web/bp_media.py`. Don't duplicate the open → transpose → edits → save pipeline elsewhere.

### Logging

- **No `print()`** in the `bpp/` package. Use `get_logger(__name__)` from `bpp/utils/logging.py`. CLI commands are exempt.
- **Nothing should be silent.** Every long-running operation (model downloads, background processing, network requests) must show visible progress or status — toast, spinner, or activity log entry. No silent stalls.
- **Don't swallow exceptions silently.** A bare `try: ... except: pass` hides real bugs; at minimum log at `warning` with `exc_info=True`.

### Architecture notes

For schema-level details (GPS columns, Live Photo sidecars, near-duplicate
clustering) see [`docs/architecture-notes.md`](docs/architecture-notes.md).

Deferred work and known follow-ups live in [`BACKLOG.md`](BACKLOG.md).
If you're about to start something non-trivial, check there first to
make sure you're not re-doing planned work.

## UI/JS coding rules

The frontend ships vanilla ES modules — no build step, no framework. Most patterns are conventions, not lint-enforced. Apply them as you edit.

- **Confirmations and notifications**: use `appConfirm()` and `toast()` from `bpp/web/static/js/modules/dialogs.mjs` / `toast.mjs`. Never `window.confirm()`, `window.alert()`, or `window.prompt()` — they block the event loop and look out of place.
- **HTML escaping**:
  - `escapeAttr()` (from `text-format.mjs`) for HTML attribute values — `title="..."`, `data-*="..."`, `alt="..."`. It escapes `& < > " '`.
  - `esc()` for text content inside elements. It escapes `& < >` only — using it in an attribute context leaves you exposed to quote injection.
- **ESC handlers in modals**: register on `document` in capture phase (`addEventListener("keydown", h, true)`) and call `ev.stopImmediatePropagation()` before your cleanup. The lightbox has its own document-level keydown handler in bubble phase; without `stopImmediatePropagation`, Esc closes both modals.
- **Display restoration**: when toggling visibility on elements that are `display:none` in CSS, set an explicit value (`"block"`/`"flex"`). `style.display = ""` removes the inline style and falls back to the CSS rule, so the element stays hidden.
- **Wrap third-party init in try/catch**: Leaflet, view entry points, and anything that touches the DOM at module top level should be wrapped — an unhandled throw in one view must not white-screen the app.
- **Layout changes**: before modifying the settings modal layout or shipping JS-generated grid/flex, sketch the full approach (what shows where, on which tabs) and implement in one pass. Partial DOM changes go live on every save. If unsure of style chain (CSS class → inline override → browser default), use fixed pixel dimensions.

## Test assertions (`msg=`)

pytest's assertion rewriting already prints the values that failed, so a generic `msg=` would just echo the assertion line. The policy:

- **Add `msg=` when the failing assertion's context isn't obvious from the line alone.** That covers asserts inside loops (the iteration value is the key context), asserts on complex objects (dict/list contents), and asserts where the predicate is opaque (e.g. `assert is_valid(x)`).
- **Don't add `msg=` to simple asserts** like `assert resp.status_code == 200` — pytest already prints the actual code.
- **`pyproject.toml` has `addopts = "-ra --tb=short --strict-markers --strict-config"`** — failures get a one-line summary at the bottom of the run plus a trimmed traceback. That's the universal diagnostic improvement; `msg=` is the local one.
- **Apply as you modify, not as a one-shot refactor.** `msg=` only helps when the assertion later fails; adding it to already-passing assertions is busywork.

## Performance & memory considerations

When changing hot paths — scoring, dedup, face/CLIP embedding loads,
the photo grid render — think about both CPU time *and* peak memory
at the documented large-library scale (`CLIP_EMBEDDING_MAX_ROWS =
200_000`, ~400 MB per dense embedding matrix).

Common trades that are easy to miss:

- **Vectorizing a Python loop into `np.stack` + matrix math** speeds
  CPU but adds a full embedding-sized matrix to peak RAM. If you do
  this, check the comment / cap math near `bpp/db/clip.py` and update
  it if your change adds a third copy.
- **`del` pass-1 buffers before allocating pass-2 buffers.** A
  multi-pass dedup that holds both buffers live blows past the
  documented 800 MB-ish peak budget at 200K rows.
- **`pool.submit()` over a 50K-photo loop** retains 50K `Future`
  objects in memory. Bound the in-flight queue (`workers * 4`
  submitted at a time) instead.
- **Float32 → float64 promotion** silently doubles peak memory for
  vectorized math. Hold embeddings at their declared dtype unless
  you have a reason to promote.

If you're touching the OOM guard at `bpp/db/clip.py` (`CLIP_EMBEDDING_MAX_ROWS`
/ `ClipEmbeddingsTooLarge`), update both the cap value AND the error
message peak-MB math at the same time. The two went out of sync once
already.

## Running the Web UI

```bash
bpp serve --library /path/to/your/photos --no-browser
```

The web UI binds to `127.0.0.1:5001` by default — don't change the port without coordinating with the Tauri sidecar, which hardcodes it. Always pass `--no-browser` when restarting during development to avoid opening a new browser tab on every reload.

Before restarting, check `lsof -i :5001` to see which process is holding the port — stale binaries occasionally linger after a crash.

Server logs go to `<library>/logs/server.log` — the path follows whichever `--library` you launched with. The app sets this up on first run.

## Expanding the JS code style

The Code Style section above covers the minimum gates. A few more conventions are worth knowing:

### Generated globals

- `.eslint-globals.json` is auto-generated from every top-level `function` / `let` / `const` / `var` in `bpp/web/static/js/`. If your call fails `no-undef`, the callee is misspelled or doesn't exist — fix one of those rather than adding to the allowlist.
- The file is committed; CI verifies it's in sync via `git diff --exit-code`. Run `npm run lint` (which regenerates it) and commit the result if it changes.
- `no-unused-vars` is intentionally off — runtime JS is script-tag loaded and most globals are called from HTML `onclick="foo()"` attributes ESLint can't see. Re-enabling produces hundreds of false positives. Revisit after the ES-module migration completes.
- The only non-codebase globals allowlisted in `eslint.config.js` are `VGrid` and `L` (Leaflet). If you pull in a new CDN library, declare it there.

### Test styles

Two test patterns coexist under `tests-js/`:

1. **Harness style** (`loadScript` from `_harness.mjs`) — for the legacy non-module JS in `bpp/web/static/js/*.js`. Loads a file via `new Function(...)`. Doesn't produce v8 coverage.
2. **Module style** (`import` directly) — for new ES modules under `bpp/web/static/js/modules/`. Does produce coverage.

Prefer module style for new code. See `bpp/web/static/js/modules/text-format.mjs` and its test for the reference pattern. If a runtime file has a top-level IIFE that touches the DOM (the legacy `lightbox.js`'s context-menu init is the canonical example), call `stubMissingDomElements()` from `_harness.mjs` before `loadScript()`.

### Incremental type safety

- Adding `// @ts-check` at the top of a `.mjs` or `.js` file turns on TypeScript checking for that file. JSDoc `@param` / `@returns` drive inference.
- Cross-file globals needed by a `@ts-check`ed file go in `tests-js/types/globals.d.ts`. Grow opt-in.
- Runtime `bpp/web/static/js/*.js` is not type-checked yet. New code should land as ES modules under `bpp/web/static/js/modules/` with `// @ts-check` enabled; existing non-module files are migrated opportunistically.

## Plugins

bpp supports out-of-tree extensions via setuptools entry-points: drop
a package on the venv path that declares `[project.entry-points."bpp.plugins"]`
with a `setup()` callable, and bpp's loader will invoke it at startup
to register face detectors, embedders, scorers, smart-album types,
workers, or config fields.

**Plugin loading is off by default** — set `BPP_ENABLE_PLUGINS=1` to
opt in. The full authoring contract, extension-point list, and trust
model live in [`docs/plugins.md`](docs/plugins.md).

## Reporting Issues

- Search existing issues before opening a new one
- Include steps to reproduce, expected vs actual behavior
- For bugs, include Python version and OS

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
