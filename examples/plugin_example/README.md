# bpp-example-plugin

A minimal working reference plugin for [Best Photo Picker](https://github.com/Arkalogy/best-photo-picker).

Copy this directory, rename the package, implement your own logic, and
`pip install -e .` into the same venv as bpp. Then set
`BPP_ENABLE_PLUGINS=1` when running `bpp serve` and your registrations
take effect at startup.

## What this example does

Registers three extensions to show the pattern for each extension point:

| Extension | What | Where |
|---|---|---|
| Config field | `example_score_boost` float (0-2) | `bpp.config_schema.register_field` |
| Smart-album type | "High Confidence" — photos with score ≥ 0.90 | `bpp.db.smart_albums.SmartAlbumRegistry` |
| Face detector | No-op passthrough (shows the detector contract) | `bpp.scoring.face_detector_registry.register_detector` |

## Quick start

```bash
# From inside the best-photo-picker repo venv:
pip install -e examples/plugin_example/

# Enable plugins and start the server:
BPP_ENABLE_PLUGINS=1 bpp serve --library ~/Pictures/BestPhotoPicker

# You'll see log lines like:
#   INFO  bpp.plugins: Loaded plugin 'example=bpp_example_plugin.plugin:setup'
#   DEBUG bpp.scoring.face_detector_registry: Registered face detector 'example_passthrough' ...
#   DEBUG bpp.config_schema: Registered config field 'example_score_boost' ...
```

## File layout

```
examples/plugin_example/
├── README.md               ← you are here
├── pyproject.toml          ← declares the bpp.plugins entry-point
└── bpp_example_plugin/
    ├── __init__.py
    └── plugin.py           ← all registrations in setup()
```

## Authoring guide

See [docs/plugins.md](../../docs/plugins.md) for the full contract:
extension-point list, lifecycle timing, trust model, and authoring
checklist.
