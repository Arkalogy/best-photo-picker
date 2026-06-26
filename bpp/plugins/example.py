"""Reference plugin — exercises every registry + every lifecycle hook.

This is a runnable, importable sample plugin that demonstrates the
complete plugin API surface that closed P5 of refactor-plan.md.

Out of the box it does nothing useful — every handler is a no-op that
records a marker into a public ``_calls`` list so test code can verify
the protocol contract. It is intentionally:

* A single self-contained file (drop it into a package, point an
  entry-point at :func:`setup`, run).
* No external dependencies beyond bppicker itself.
* Inert by default — registering against built-in names with
  ``replace=False`` would collide with the shipped detectors / album
  refreshers, so the example uses fresh plugin-prefixed names
  (``example_*``).
* Lifecycle-aware — implements all four :class:`Plugin` hooks
  (``on_register`` / ``on_library_open`` / ``on_library_close`` /
  ``on_shutdown``) so the test suite can assert ordering.

Two ways to use this:

1. **In-tree** — call :func:`setup` directly from a test or REPL to
   register the example, then drive lifecycle events with
   :mod:`bpp.plugin_protocol` helpers.

2. **As an entry-point** — declare in your wheel's pyproject.toml::

       [project.entry-points."bpp.plugins"]
       example = "bpp.plugins.example:setup"

   then run with ``BPP_ENABLE_PLUGINS=1``.

The plan called the registry-unification deliverable "4 registries
migrated to a shared Plugin protocol shape." The actual shape they
share is structural — every registry exposes ``register(...) /
get(name) / _reset_for_tests()``. The example below registers
something against each of the four registries to prove the surface
is uniform from a plugin author's POV.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

# Public — tests append/inspect these markers to verify the protocol.
#
# Thread-safe via :data:`_calls_lock`. Lifecycle hooks may fire from
# different host threads (``on_register`` on the main thread,
# ``on_library_close`` on a worker thread during shutdown drain) and
# the post-event hooks fire from whatever background worker just
# committed — analyse / import / face extract. The lock keeps the
# marker list internally consistent even when two hooks race.
#
# Plugin authors copying this file as a template: if your plugin
# maintains its own shared state (a side-cache DB, an in-memory
# index, a counter), use a similar lock. The host's lifecycle firing
# is ordered per registration, but it does NOT serialise across the
# background workers — concurrent hook fires from analyse + face
# extract + import are all possible at the same moment.
_calls: list[str] = []
_calls_lock = threading.Lock()

EXAMPLE_DETECTOR_NAME = "example_detector"
EXAMPLE_EMBEDDER_NAME = "example_embedder"
EXAMPLE_ALBUM_KIND = "smart_example_demo"
EXAMPLE_ALBUM_DOMAIN = "example_demo"
EXAMPLE_EXPORT_MODE = "example_metadata_only"
EXAMPLE_DEDUPE_STRATEGY = "example_noop"
EXAMPLE_WORKER_NAME = "example_worker"


def _record(event: str) -> None:
    """Append an event marker thread-safely so tests can read ordering."""
    with _calls_lock:
        _calls.append(event)


def _reset_calls() -> None:
    """Test-only — clear the markers list."""
    with _calls_lock:
        _calls.clear()


# ──────────────────────────────────────────────────────────────────
# Registry registrations
# ──────────────────────────────────────────────────────────────────


def _example_detect(
    image: np.ndarray, min_confidence: float
) -> list[tuple[int, int, int, int, float]]:
    """Sample face detector — always returns no faces. The point is
    that the plugin registers against FaceDetectorRegistry without
    needing to ship a model."""
    _record("detect_called")
    return []


def _example_embed(image: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray | None:
    """Sample face embedder — returns a deterministic zero vector."""
    _record("embed_called")
    return np.zeros(128, dtype=np.float32)


def _example_album_refresh(conn: Any) -> None:
    """Sample smart-album refresher — does nothing. Real plugins
    would query the photos table and call :func:`_ensure_smart_album`."""
    _record("album_refresh_called")


def _example_album_get_ids(conn: Any, rule: dict) -> list[int]:
    """Sample smart-album resolver — returns no photo ids."""
    _record("album_get_ids_called")
    return []


def _example_export_handler(src: str, dest: str) -> None:
    """Sample export-mode handler — records the call."""
    _record(f"export_called:{dest}")


def _example_dedupe_fn(items: list[dict], config: dict, **_kw: Any) -> list[dict]:
    """Sample dedupe strategy — keep every item ("nothing duplicate")."""
    _record("dedupe_called")
    return list(items)


class _ExampleWorker:
    """Sample background worker. Real workers inherit
    :class:`bpp.web.base_worker.BackgroundWorker`; for the example
    we just need the registration to succeed."""

    def __init__(self) -> None:
        _record("worker_constructed")

    def cancel(self) -> None:
        _record("worker_cancelled")

    def join(self, timeout: float | None = None) -> None:
        _record("worker_joined")


def _do_registry_registrations() -> None:
    """Register an entry against each plugin-target registry.

    Called from :func:`setup`. Wrapped so test code can also invoke
    it directly without going through the full entry-point loader.

    Every step is wrapped in a defensive try/except — a real plugin
    wouldn't suppress like this, but the example needs to stay
    runnable when registries have already been populated in a
    long-lived test process. Errors are recorded as markers so a
    failed registration is still visible.
    """
    # 1. FaceDetectorRegistry.
    try:
        from bpp.scoring.face_detector_registry import FaceDetector, register_detector

        register_detector(
            FaceDetector(
                name=EXAMPLE_DETECTOR_NAME,
                detect=_example_detect,
                toggle_key=None,
                license_id="MIT",
                description="Example plugin detector (no-op)",
            )
        )
        _record("registered:detector")
    except Exception:
        _record("registered:detector:skipped")

    # 2. FaceEmbedderRegistry.
    try:
        from bpp.scoring.face_embedder_registry import FaceEmbedder, register_embedder

        register_embedder(
            FaceEmbedder(
                name=EXAMPLE_EMBEDDER_NAME,
                embed=_example_embed,
                embedding_dim=128,
                license_id="MIT",
                description="Example plugin embedder (zeros)",
            )
        )
        _record("registered:embedder")
    except Exception:
        _record("registered:embedder:skipped")

    # 3. SmartAlbumRegistry — register a custom type + domain.
    try:
        from bpp.db.smart_albums import SmartAlbumRegistry, register_album_domain

        SmartAlbumRegistry.register(
            EXAMPLE_ALBUM_KIND,
            _example_album_refresh,
            _example_album_get_ids,
            replace=True,
        )
        register_album_domain(
            EXAMPLE_ALBUM_DOMAIN,
            (EXAMPLE_ALBUM_KIND,),
            extend=True,
        )
        _record("registered:smart_album")
    except Exception:
        _record("registered:smart_album:skipped")

    # 4. ExportModeRegistry.
    try:
        from bpp.output.export import register_export_mode

        register_export_mode(
            EXAMPLE_EXPORT_MODE,
            _example_export_handler,
            description="Example plugin export mode",
            replace=True,
        )
        _record("registered:export_mode")
    except Exception:
        _record("registered:export_mode:skipped")

    # 5. DedupeStrategyRegistry.
    try:
        from bpp.dedupe.strategy import DedupeStrategy, register_dedupe_strategy

        register_dedupe_strategy(
            DedupeStrategy(
                name=EXAMPLE_DEDUPE_STRATEGY,
                dedupe_fn=_example_dedupe_fn,
                description="Example plugin dedupe strategy (no-op)",
            ),
            replace=True,
        )
        _record("registered:dedupe_strategy")
    except Exception:
        _record("registered:dedupe_strategy:skipped")

    # 6. WorkerRegistry.
    try:
        from bpp.web.worker_registry import WorkerRegistry

        WorkerRegistry.register(EXAMPLE_WORKER_NAME, _ExampleWorker, replace=True)
        _record("registered:worker")
    except Exception:
        _record("registered:worker:skipped")


# ──────────────────────────────────────────────────────────────────
# Plugin class with all four lifecycle hooks
# ──────────────────────────────────────────────────────────────────


class ExamplePlugin:
    """Reference :class:`bpp.plugin_protocol.Plugin` implementation.

    Every hook records its own name into :data:`_calls` so tests can
    assert ordering. A real plugin would do meaningful work in the
    same slots: open a per-library DB in ``on_library_open``, close
    it in ``on_library_close``, etc.
    """

    name = "example-plugin"
    version = "0.1.0"

    def on_register(self, app: Any) -> None:
        _record("on_register")

    def on_db_restore(self, corrupted_sidecar_path: str) -> None:
        # Real plugins flush their own caches here — the row set in
        # the freshly-restored DB may not match what the cache was
        # built against. The sidecar path is available for triage
        # (e.g. emit it to a plugin-specific log so the user can
        # diff against the live DB after the fact).
        _record(f"on_db_restore:{corrupted_sidecar_path}")

    def on_library_open(self, ctx: Any) -> None:
        _record("on_library_open")

    def on_library_close(self, ctx: Any) -> None:
        _record("on_library_close")

    def on_shutdown(self) -> None:
        _record("on_shutdown")


__plugin_name__ = "bppicker-example"
__plugin_version__ = "0.1.0"
__bpp_version_required__ = ">=0.1,<2"


def _on_post_analyze(_conn: Any, results: list[dict[str, Any]]) -> None:
    _record(f"post_analyze:{len(results)}")


def _on_post_cluster(_conn: Any, kind: str, n_clusters: int) -> None:
    _record(f"post_cluster:{kind}:{n_clusters}")


def _on_post_import(_conn: Any, photo_ids: list[int], _fps: list[str]) -> None:
    _record(f"post_import:{len(photo_ids)}")


def setup() -> None:
    """Plugin entry point — registry registrations + lifecycle hook.

    Declared in an external wheel's ``pyproject.toml`` as::

        [project.entry-points."bpp.plugins"]
        example = "bpp.plugins.example:setup"

    For in-tree tests the loader is bypassed; call ``setup()`` directly.
    """
    from bpp.db.event_hooks import (
        register_post_analyze_hook,
        register_post_cluster_hook,
        register_post_import_hook,
    )
    from bpp.plugin_protocol import register_plugin

    _do_registry_registrations()
    register_post_analyze_hook(_on_post_analyze)
    register_post_cluster_hook(_on_post_cluster)
    register_post_import_hook(_on_post_import)
    register_plugin(ExamplePlugin())
