# ADR 0003 — Plugin protocol

**Status.** Proposed (P0). Locks the shape that P5 implements.

**Context.** Today bpp extends through **seven** different registries
with seven different shapes:

| Registry | Lives in | Shape |
|---|---|---|
| `WorkerRegistry` | `bpp/web/worker_registry.py` | name → factory callable |
| `SmartAlbumRegistry` | `bpp/db/smart_albums.py` | name → (refresh_fn, get_ids_fn) tuple + optional on_rename hook |
| `_ScoringRegistry` (face detector + embedder both inherit) | `bpp/scoring/_registry_base.py` | name → ScorerSpec dataclass |
| `ExportModeRegistry` | `bpp/output/export.py` | name → ExportMode dataclass |
| `DedupeStrategyRegistry` | `bpp/dedupe/strategy.py` | name → strategy class |
| `ModelRegistry` | `bpp/scoring/model_base.py` | name → ModelEntry (ML lifecycle) |
| `photo_hooks._hooks` | `bpp/db/photo_hooks.py` | kind → list[callable] event-bus |

A plugin author has to learn each shape independently and figure out
when their entry point fires relative to WebAppState construction.
There's no place to put a "set up DB tables when a library opens"
hook because there's no library-lifecycle event.

**Decision.** P5 ships a single `Plugin` protocol in
`bpp/plugins/protocol.py` with these lifecycle hooks:

```python
class Plugin(Protocol):
    name: str

    def on_register(self, app: Flask) -> None: ...
    def on_library_open(self, ctx: WebAppState) -> None: ...
    def on_library_close(self, ctx: WebAppState) -> None: ...
    def on_shutdown(self) -> None: ...
```

Hook semantics:

- **`on_register`** fires once per process startup, after blueprints
  are registered. Plugins use this to register endpoints, register
  smart album types, register export modes, etc. Must NOT touch the
  DB — there's no ctx yet.
- **`on_library_open`** fires every time a library is opened (initial
  startup AND every `switch_library`). Plugins use this to set up
  per-library state, create DB tables via a migration, warm caches.
- **`on_library_close`** fires before tearing down a library (every
  `switch_library` and `shutdown`). Plugins use this to flush state,
  cancel their workers, release file handles.
- **`on_shutdown`** fires once on process exit. Plugins use this for
  final cleanup that doesn't depend on a library.

**Migration of existing registries.** Four migrate onto the protocol:
- `WorkerRegistry` — plugins call `register_worker(...)` from `on_register`.
- `SmartAlbumRegistry` — plugins call `register_smart_album(...)` from
  `on_register`. The "indexed-column" extension (P5 schema fix) takes a
  column-spec arg so plugin-registered album types get their own
  indexed columns instead of substring-matching JSON.
- `ExportModeRegistry` — plugins call `register_export_mode(...)` from
  `on_register`.
- `DedupeStrategyRegistry` — plugins call `register_dedupe_strategy(...)`
  from `on_register`.

**Three stay separate** because their lifecycle doesn't fit:
- `_ScoringRegistry` (face detector + embedder) — already shares a
  common base, lazy-ML-load lifecycle (different from the synchronous
  registries above), and the singleton-per-process semantics don't
  match `on_library_open`.
- `ModelRegistry` — same reason. ML models load on first use and live
  until process exit.
- `photo_hooks` — event-bus semantics. Stays as-is; plugins subscribe
  via `register_photo_hook(...)` from `on_register`.

**Plugin discovery.** Plugins are discovered via `entry_points` in
`pyproject.toml` (group: `bpp.plugins`) and via `BPP_PLUGINS=...` env
var (semicolon-separated module names) for dev. CLI also accepts
`--plugin path/to/module.py`. The `load_plugin_entry_points` call
(already wired in `bpp/commands/{analyze,pick,serve}.py`) becomes the
single load path.

**Consequences.**
- Plugin authors learn one protocol, not seven.
- The migration shim period keeps the old `register_*` free functions
  working — they're rewritten as thin wrappers that build a Plugin
  with just that one hook implemented.
- New tests: `test_plugin_lifecycle_hooks_fire_in_order`,
  `test_plugin_library_open_called_on_switch`, etc. P5 ships a trivial
  example plugin in `bpp/plugins/example/` as a smoke test.

**Out of scope.**
- Async plugin hooks.
- Plugin sandboxing / security boundaries (plugins run as bpp process).
- Plugin marketplace.
