"""Reference implementation — shows the bpp plugin extension-point contract.

This module is called once at bpp startup (via the `bpp.plugins` entry-point
group) when `BPP_ENABLE_PLUGINS=1` is set. All registrations happen inside
`setup()` so they're isolated: the module can be imported for tests without
side effects.

Demonstrates nine extension points:
  1. Config field        — `example_score_boost` (float, 0-2, default 1.0)
  2. Smart-album type    — "High Confidence" photos (score >= 0.90)
  3. Face detector       — a no-op passthrough to show the detector contract
  4. ML model entry      — declarative registry stub for a custom model so the
                           Settings → ML Models UI surfaces redownload/uninstall
                           affordances. The actual download URL and SHA are
                           placeholders here — a real plugin would point them at
                           a hosted weights file.
  5. Recovery handler    — registers a journal-recovery handler for a fictional
                           "example_plugin_op" kind so a mid-flight crash during
                           the plugin's hypothetical long-running operation gets
                           cleaned up on next startup instead of stranding state.
  6. Custom scorer       — `example_saturation` — adds a per-photo saturation
                           score during analyze. Default weight is 0 (no effect
                           on aggregate_score until the user turns it on via
                           YAML config); demonstrates the optional-scorer
                           contract with toggle-key gating.
  7. Export mode         — `example_sidecar` — drops a JSON sidecar next to each
                           exported photo's destination path. Demonstrates the
                           (src, dest) -> None handler contract that plugins use
                           to ship custom exports (S3 uploads, encrypted ZIP,
                           remote backup, etc).
  8. Dedup strategy      — `example_score_dedup` — declares each photo a
                           singleton (cluster_size=1) so the recompute
                           pipeline keeps every photo. Demonstrates the
                           dedupe-strategy registration contract; a real
                           plugin would implement perceptual-quality-aware
                           dedup, content-hash dedup, or similar.
  9. Plugin metadata     — module-level `__plugin_name__`, `__plugin_version__`,
                           and `__bpp_version_required__` constants so the
                           loader can surface a friendly label in logs and
                           skip the plugin when the running bpp version
                           doesn't match the declared compat range.

Copy and adapt. See docs/plugins.md for the full authoring guide.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# 9. Plugin metadata — read by bpp's loader before invoking setup()
# ---------------------------------------------------------------------------
#
# Optional but recommended. Three module-level constants the loader looks
# for via getattr() on the plugin module:
#
#   __plugin_name__         — friendly label used in log lines (defaults to
#                             the entry-point id when missing).
#   __plugin_version__      — your plugin's own version. Logged alongside
#                             the name; not used for compatibility checks.
#   __bpp_version_required__ — PEP 440 specifier set. The loader parses it
#                             with packaging.specifiers.SpecifierSet and
#                             skips the plugin (with a warning) when the
#                             running bpp.__version__ doesn't satisfy it.
#
# Pin a lower bound + an exclusive upper bound on the next major. bpp
# follows semver: registry signatures stay stable across minor versions,
# and breaking changes always bump the major.
__plugin_name__ = "bpp-example-plugin"
__plugin_version__ = "0.1.0"
__bpp_version_required__ = ">=0.1,<1.0"

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Config field
# ---------------------------------------------------------------------------


def _register_config_field() -> None:
    """Add `example_score_boost` to the settings schema.

    Users can then set it in their YAML config or via Settings → Advanced
    (once a /api/v1/settings/schema endpoint is wired). The schema entry
    also validates writes through Config.set().
    """
    from bpp.config_schema import ConfigField, register_field

    register_field(
        ConfigField(
            key="example_score_boost",
            type=float,
            label="Example score boost",
            description="Multiply aggregate_score by this factor (demo only).",
            min=0.0,
            max=2.0,
            ui_type="slider",
            category="Plugins",
        )
    )


# ---------------------------------------------------------------------------
# 2. Smart-album type
# ---------------------------------------------------------------------------


def _get_high_confidence_ids(conn: Any, config: dict[str, Any]) -> list[int]:
    """Return photo IDs whose aggregate_score >= 0.90.

    This is the resolver function for the smart-album type: bpp calls it
    whenever it needs to know which photos belong to the album.
    """
    threshold = float(config.get("example_score_boost", 1.0)) * 0.90
    rows = conn.execute(
        "SELECT id FROM photos WHERE aggregate_score >= ? "
        "AND deleted_at IS NULL AND hidden_at IS NULL",
        (threshold,),
    ).fetchall()
    return [r[0] for r in rows]


def _register_smart_album() -> None:
    """Register a "High Confidence" smart album type.

    Once registered, bpp will create one "High Confidence" album and
    refresh its membership when refresh_smart_albums() runs.
    """
    from bpp.db.smart_albums import SmartAlbumRegistry

    # SmartAlbumRegistry.register(album_type, refresh_fn, get_ids_fn)
    # refresh_fn(conn, config) → updates album membership in the DB.
    # get_ids_fn(conn, config) → returns list of photo IDs in the album.
    # Here we use a simple get_ids approach and let bpp handle the refresh
    # by wrapping it as both.
    SmartAlbumRegistry.register(
        "smart_high_confidence",
        refresh_fn=lambda conn, config: None,  # no-op; membership is read from get_ids_fn
        get_ids_fn=_get_high_confidence_ids,
    )


# ---------------------------------------------------------------------------
# 3. Face detector
# ---------------------------------------------------------------------------


def _example_detect(
    image: np.ndarray, min_confidence: float
) -> list[tuple[int, int, int, int, float]]:
    """No-op detector — always returns an empty list.

    Replace with your real inference code. The contract:
      - `image` is a BGR ndarray (height x width x 3, uint8).
      - `min_confidence` is the caller's threshold (0.0-1.0).
      - Return a list of (x, y, w, h, confidence) tuples.
      - Empty list = no faces found.
      - Must be thread-safe (the analyze worker calls this concurrently).
    """
    return []


def _register_face_detector() -> None:
    """Register the example passthrough detector.

    Note: the built-in orchestrator (_collect_detections) has its own
    per-detector early-exit logic; plugin detectors run via
    `run_optional_detector(name, image, min_confidence)` from your own
    scoring extension, not automatically in the built-in pipeline. See
    docs/plugins.md for the limitation.
    """
    from bpp.scoring.face_detector_registry import FaceDetector, register_detector

    register_detector(
        FaceDetector(
            name="example_passthrough",
            detect=_example_detect,
            toggle_key=None,  # always-on (no per-model toggle in Settings)
            license_id="MIT",
            description="No-op reference detector — always returns empty list",
        )
    )


# ---------------------------------------------------------------------------
# 4. ML model lifecycle entry
# ---------------------------------------------------------------------------


def _register_ml_model() -> None:
    """Register a stub `ModelEntry` so Settings → ML Models lists it.

    A real plugin would also instantiate a `ModelSingleton` to manage
    the live, in-process model object (download on first use, atomic
    redownload, etc.) — see `docs/plugins.md` → "Custom ML models" for
    the full pattern.

    Here we use a placeholder URL/SHA. With invalid bytes, the
    download path will refuse to verify and `_noop_reset()` is a
    standalone no-op so this stub does not interfere with anything
    real. The point of this registration is only to demonstrate the
    `ModelRegistry.register(ModelEntry(...))` shape.
    """
    from pathlib import Path

    from bpp.scoring.model_base import ModelEntry, ModelRegistry

    def _noop_reset() -> None:
        """A real plugin would point this at `ModelSingleton.reset`
        so a redownload triggers a clean reload of the cached object."""

    placeholder_path = Path.home() / ".cache" / "bpp" / "example_plugin" / "example_model.onnx"
    ModelRegistry.register(
        ModelEntry(
            name="example_plugin_model",
            path=str(placeholder_path),
            url="https://example.invalid/never-fetched-by-default.onnx",
            sha256=(
                # Placeholder: SHA-256 of the empty string. A real
                # plugin pins this to the actual upstream weights so
                # download_file() can verify before activation.
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            ),
            reset=_noop_reset,
        ),
        # The example plugin's test harness re-imports this module
        # under different fixtures; tolerate re-registration so we
        # don't fail noisily during repeated setup().
        replace=True,
    )


# ---------------------------------------------------------------------------
# 5. Operation recovery handler
# ---------------------------------------------------------------------------


def _recover_example_op(conn: Any, payload: dict[str, Any]) -> bool:
    """Recovery handler for the fictional "example_plugin_op" journal kind.

    A real long-running plugin operation would bracket its work like:

        from bpp.db.journal import journal_start, journal_complete
        jid = journal_start(conn, "example_plugin_op", {"work_id": ..., "version": 1})
        try:
            ... do the work ...
            journal_complete(conn, jid)
        except Exception:
            raise   # leave the journal entry; this handler picks it up

    On the next startup, bpp finds the orphaned entry and calls this
    handler with the parsed payload. Return True to mark recovered
    (entry deleted); False to leave the breadcrumb for manual triage.
    """
    log.info(
        "bpp-example-plugin: recovering example_plugin_op (payload=%r)",
        payload,
    )
    # A real handler would clean up partial state here. The example is
    # idempotent and has no real state, so we just clear the breadcrumb.
    return True


def _register_recovery_handler() -> None:
    """Bind the example recovery handler. `replace=True` so a library
    switch (which re-runs plugin setup against the new ctx) doesn't
    raise on duplicate-handler detection."""
    from bpp.db.journal import register_recovery_handler

    register_recovery_handler("example_plugin_op", _recover_example_op, replace=True)


# ---------------------------------------------------------------------------
# 6. Custom scorer
# ---------------------------------------------------------------------------


def _example_saturation_score_fn(
    img: np.ndarray, filepath: str, config: dict[str, Any]
) -> dict[str, Any]:
    """Compute a trivial 'saturation' score on the per-photo BGR image.

    Real plugins would do something interesting (perceptual saturation,
    color harmony, subject count, OCR-text density). This demo just
    averages the saturation channel of an HSV conversion so the
    contract — `score_fn(img, filepath, config) -> dict[str, Any]` —
    is testable end-to-end.

    Returns a dict with the field name(s) declared in `api_fields`.
    Skipped scorers return {} (or never run if the toggle is off);
    callers rely on missing keys rather than zero/null sentinels.
    """
    if img is None or img.size == 0:
        return {}
    try:
        import cv2

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        saturation = float(hsv[:, :, 1].mean()) / 255.0
    except Exception:
        log.debug("saturation score failed for %s", filepath, exc_info=True)
        return {}
    return {"example_saturation_score": saturation}


def _register_scorer() -> None:
    """Register the demo saturation scorer.

    Default weight is 0.0 — the scorer runs but doesn't influence
    aggregate_score until the user sets `example_saturation_weight` to
    a non-zero value in their YAML config. This is the safe default for
    plugins (re-normalizing aggregate on plugin install would change
    every existing photo's score). To make a plugin scorer that ships
    with a non-zero weight, document loudly that installing it will
    re-rank the user's library.
    """
    from bpp.scoring.registry import ScorerDef, register_scorer

    register_scorer(
        ScorerDef(
            key="example_saturation",
            weight_key="example_saturation_weight",
            default_weight=0.0,  # off by default; user opts in via config
            aggregate_default=0.5,
            optional=True,
            toggle_key="model_example_saturation",
            score_fn=_example_saturation_score_fn,
            api_fields={"example_saturation_score": 0},
        ),
        # `replace=True` so a library switch (which re-runs setup
        # against the new ctx) doesn't trip the duplicate-detection
        # guard.
        replace=True,
    )


# ---------------------------------------------------------------------------
# 7. Custom export mode
# ---------------------------------------------------------------------------


def _example_sidecar_export(src: str, dest: str) -> None:
    """Copy the photo and write a tiny JSON sidecar next to it.

    The handler contract is `(src, dest) -> None`. `src` is the
    original photo path; `dest` is the fully-resolved destination
    path (the export loop has already created the parent directory
    and applied safe-join validation). On failure: raise — the
    export loop catches per-photo failures and continues with the
    rest of the batch.
    """
    import json
    import shutil

    shutil.copy2(src, dest)
    sidecar_path = dest + ".json"
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "exported_by": "bpp_example_plugin",
                "source": src,
                "destination": dest,
            },
            f,
            indent=2,
        )


def _register_export_mode() -> None:
    """Register the example sidecar export mode."""
    from bpp.output.export import register_export_mode

    register_export_mode(
        "example_sidecar",
        _example_sidecar_export,
        description=("Copy + JSON sidecar — demonstrates plugin export-mode contract."),
        # `replace=True` so a library switch (which re-runs setup
        # against the new ctx) doesn't trip the duplicate-detection
        # guard.
        replace=True,
    )


# ---------------------------------------------------------------------------
# 8. Custom dedup strategy
# ---------------------------------------------------------------------------


def _example_score_dedup_fn(
    items: list[dict[str, Any]],
    config: dict[str, Any],
    **_kwargs: object,
) -> list[dict[str, Any]]:
    """Demo strategy: drop nothing, just stamp cluster_size=1.

    A real strategy would:
      * Detect duplicates (perceptual quality, cryptographic content
        hash, learned embeddings, etc.)
      * Pick a representative per cluster (typically the
        highest-scoring photo)
      * Set ``cluster_size`` on the representative so the lightbox
        can display "+N similar photos"
      * Optionally attach ``similar_photos: [{filepath, similarity,
        ...}]`` so the lightbox renders the cluster siblings.

    For this demo, each photo is its own cluster — useful as a
    stress-test (the user picks ``dedupe_strategy: example_score_dedup``
    in their YAML config to get every photo through to selection).
    """
    out: list[dict[str, Any]] = []
    for item in items:
        item["cluster_size"] = 1
        out.append(item)
    return out


def _register_dedupe_strategy() -> None:
    """Register the example dedup strategy.

    Users opt in by setting ``dedupe_strategy: example_score_dedup``
    in their bpp config (YAML or DB-backed). The strategy then
    overrides bpp's auto-pick (CLIP if loaded, else phash) for the
    rest of the recompute path.
    """
    from bpp.dedupe.strategy import DedupeStrategy, register_dedupe_strategy

    register_dedupe_strategy(
        DedupeStrategy(
            name="example_score_dedup",
            dedupe_fn=_example_score_dedup_fn,
            description="Demo: keep every photo (singleton clusters).",
            requires_clip_embeddings=False,
        ),
        replace=True,
    )


# ---------------------------------------------------------------------------
# Entry point — called by bpp.plugins.load_plugin_entry_points()
# ---------------------------------------------------------------------------


def setup() -> None:
    """Register all extensions for this plugin.

    bpp calls this exactly once per process when BPP_ENABLE_PLUGINS=1
    and this package's entry-point is declared in pyproject.toml:

        [project.entry-points."bpp.plugins"]
        example = "bpp_example_plugin.plugin:setup"

    Keep this function fast — it runs on the bpp startup critical path.
    Defer any expensive work (model loading, network calls) to the actual
    detection / scoring call sites.
    """
    log.info("bpp-example-plugin setup() called")
    _register_config_field()
    _register_face_detector()
    _register_ml_model()
    _register_recovery_handler()
    _register_scorer()
    _register_export_mode()
    _register_dedupe_strategy()

    # Smart-album registration may fail gracefully if the registry API
    # changes between bpp versions — log and continue rather than crash.
    try:
        _register_smart_album()
    except Exception:
        log.warning(
            "bpp-example-plugin: smart-album registration failed "
            "(SmartAlbumRegistry API may have changed)",
            exc_info=True,
        )
