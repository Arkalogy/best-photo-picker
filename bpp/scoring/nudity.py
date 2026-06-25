"""ML-based nudity detection using NudeNet (optional dependency).

When ``pip install bppicker[nudity]`` is run, the ``nudenet`` Python
package is installed for the runtime inference code. The model file
itself (``320n.onnx``) is NOT used from the nudenet wheel —
``bpp.utils.download.download_file`` fetches a separately-pinned copy
from notAI-tech/NudeNet on GitHub through the same registry-coordinated
chokepoint that every other restricted model uses. The detector is
constructed via ``NudeDetector(model_path=...)`` against our own file.

Why bypass the wheel-bundled file? Two reasons:

1. **Single chokepoint.** Every restricted-license model in bpp goes
   through the same ``download_file`` gate — the registry policy
   check fires before the network call, the SHA-256 is verified
   before the bytes are exposed to ONNX, and the chokepoint window
   blocks any sibling library trying to fetch in the same scope.
   The wheel-bundled path was the lone exception; collapsing it
   eliminates a parallel codepath.
2. **License posture.** NudeNet is AGPL-3.0; the pip-bundled
   distribution path commingles bpp's MIT code with NudeNet's GPL
   model weights through the installer. Decoupling the model from
   the package keeps the rights chain clean: the user opts in to
   the GPL Python package separately, and the model is fetched only
   after the click-through legal acceptance is on file.

Scoring logic
-------------
The ``score_nudity`` function returns a float in [0, 1]:

* **Primary labels** (``FEMALE_GENITALIA_EXPOSED``, ``MALE_GENITALIA_EXPOSED``,
  ``ANUS_EXPOSED``) contribute their detection confidence at **full weight**.
* **Secondary labels** (``BUTTOCKS_EXPOSED``, ``FEMALE_BREAST_EXPOSED``)
  contribute at **30 %** weight — enough to flag obvious cases without
  penalising beach or bath photos heavily.
* The final score is ``min(1.0, max_primary + 0.3 * max_secondary)``.
* If no relevant detections are found the score is **0.0** (no penalty).

Thread safety
-------------
The ``NudeDetector`` is initialised lazily as a module-level singleton.
ONNX Runtime inference is thread-safe, so sharing the detector across the
parallel analysis workers is fine.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from bpp.scoring.model_base import ModelSingleton
from bpp.utils.logging import get_logger

log = get_logger(__name__)

# ── Model: NudeNet 320n (downloaded via registry chokepoint) ──────
#
# What:    binary classifier for explicit content. Powers the optional
#          "filter NSFW" smart album / scoring penalty.
# Where:   notAI-tech/NudeNet on GitHub, fetched through our canonical
#          download_file gate. Commit-pinned URL so a future repo
#          history rewrite (force-push to v3 branch) can't silently
#          swap the file.
# Why this one: 320n is NudeNet's "nano" model (320x320 input,
#          smallest weight bundle); larger variants exist for higher
#          precision but the per-photo budget here is tight.
# License: AGPL-3.0 (NudeNet) — bpp's [nudity] extra is opt-in for
#          this reason. The registry entry's click-through fires
#          before this URL is fetched (enforce_load_policy_for inside
#          download_file).
# To bump: bump the URL's commit SHA + the matching weight hash in
#          ``bpp/registry/builtins.py`` (NUDENET_ENTRY). Re-validate
#          detection quality before pinning a new commit.
REGISTRY_ID = "nudenet_320n"

#: Upstream URL pinned to the notAI-tech/NudeNet commit that
#: published 320n.onnx at the SHA below. Commit-pinned (not branch-
#: or tag-pinned) so the URL is byte-stable even if the repo's
#: ``v3`` branch is force-pushed.
NUDENET_MODEL_URL = (
    "https://raw.githubusercontent.com/notAI-tech/NudeNet/"
    "21ccea100712f0844a4f6bb66c9f3972b2c32f85/nudenet/320n.onnx"
)

#: SHA-256 of 320n.onnx fetched from NUDENET_MODEL_URL. download_file
#: verifies this before the bytes are written to disk; ONNX never
#: sees unverified bytes.
NUDENET_MODEL_SHA256 = "c15d8273adad2d0a92f014cc69ab2d6c311a06777a55545f2c4eb46f51911f0f"

_MODEL_FILENAME = "320n.onnx"


def _model_cache_dir() -> Path:
    """Where to cache the NudeNet ONNX file. Honours BPP_MODELS_DIR
    via the shared :func:`bpp.utils.paths.models_dir`."""
    from bpp.utils.paths import models_dir

    return Path(models_dir()) / "nudenet"


def _local_model_path() -> Path:
    return _model_cache_dir() / _MODEL_FILENAME


def is_on_disk() -> bool:
    """Return True if the locally-cached NudeNet ONNX file exists.

    Cheap existence check only — does NOT verify the SHA. The picker
    uses this to decide whether to surface "Download (~11.6 MB)" or
    "Use this model" as the next step. A tampered cache is caught and
    re-downloaded at load time by :func:`ensure_nudenet_model`; the
    picker never deletes files.
    """
    return _local_model_path().exists()


def ensure_nudenet_model() -> str:
    """Download + verify the NudeNet 320n ONNX. Returns the local path.

    Routes through the canonical :func:`bpp.utils.download.download_file`
    so the registry policy gate fires BEFORE the network call. If the
    cached file already exists and verifies against
    :data:`NUDENET_MODEL_SHA256`, no network call is made.

    Raises :class:`bpp.scoring.model_base.ModelIntegrityError` on a
    hash mismatch at either the cached layer or the freshly-downloaded
    file.
    """
    from bpp.scoring.model_base import ModelIntegrityError
    from bpp.utils.download import download_file, verify_existing

    local = _local_model_path()
    if local.exists():
        try:
            verify_existing(str(local), sha256=NUDENET_MODEL_SHA256)
            return str(local)
        except ModelIntegrityError:
            log.warning("Cached NudeNet model fails integrity check; re-downloading")
            with contextlib.suppress(OSError):
                local.unlink()

    local.parent.mkdir(parents=True, exist_ok=True)
    tmp = local.with_suffix(".onnx.tmp")
    log.info("Downloading NudeNet 320n model (~11.6 MB) from upstream GitHub mirror")
    try:
        download_file(
            NUDENET_MODEL_URL,
            str(tmp),
            registry_id=REGISTRY_ID,
            sha256=NUDENET_MODEL_SHA256,
        )
    except Exception:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    os.replace(tmp, local)
    log.info("NudeNet model installed at %s", local)
    return str(local)


def remove_local_weights() -> int:
    """Delete the cached NudeNet ONNX. Returns the bytes freed.

    Used by the picker's Uninstall action for the NudeNet catalog
    entry. Also resets the in-process ModelSingleton so the next
    load re-runs the ensure → download → verify chain. Symmetric
    counterpart to :func:`ensure_nudenet_model`.
    """
    freed = 0
    local = _local_model_path()
    if local.exists():
        freed = local.stat().st_size
        with contextlib.suppress(OSError):
            local.unlink()
    tmp = local.with_suffix(".onnx.tmp")
    if tmp.exists():
        with contextlib.suppress(OSError):
            tmp.unlink()
    _nudenet.reset()
    return freed


def _create_nude_detector(_path):
    """Construct the NudeDetector against our registry-fetched
    model file. The policy gate fires inside
    :func:`ensure_nudenet_model` before any bytes hit disk."""
    from nudenet import NudeDetector

    model_path = ensure_nudenet_model()
    return NudeDetector(model_path=model_path)


#: Module-level singleton — the picker's catalog hook (in
#: bpp.web.bp_model_registry._catalog_loaders) drives explicit
#: Download / Uninstall through ``ensure_nudenet_model`` and
#: ``remove_local_weights`` above; the loader proper goes through
#: ``_get_detector`` which fires the policy gate before
#: ``_create_nude_detector`` runs.
_nudenet = ModelSingleton(
    name="NudeNet",
    model_path=None,  # managed by ensure_nudenet_model
    model_url=None,  # download routed via ensure_nudenet_model
    create_fn=_create_nude_detector,
    registry_id=REGISTRY_ID,
    import_check=lambda: __import__("nudenet"),
)


# ---------------------------------------------------------------------------
# Availability check — mirrors face_embed.is_available()
# ---------------------------------------------------------------------------


def is_available() -> bool:
    """Return *True* if the ``nudenet`` package is importable."""
    return _nudenet.is_available()


# ---------------------------------------------------------------------------
# Singleton detector
# ---------------------------------------------------------------------------


def _get_detector():
    """Return (and lazily create) the shared ``NudeDetector`` instance.

    Enforces the registry policy gate FIRST. NudeNet is licensed
    under AGPL-3.0; the click-through acceptance dialog must have
    been completed (with separate-rights assertion if the user is
    in commercial mode) before the model downloads.
    """
    from bpp.registry import enforce_load_policy_for

    enforce_load_policy_for(REGISTRY_ID)
    return _nudenet.get()


# ---------------------------------------------------------------------------
# Label sets
# ---------------------------------------------------------------------------

# Labels that indicate exposed genitalia — the primary safety concern.
# These MUST match NudeNet v3's label set (nudenet.nudenet.__labels);
# 320n.onnx is a v3 model. The v2-era names (EXPOSED_GENITALIA_F, …) never
# match v3 output, which silently zeroed every nudity score —
# test_nudity.py asserts these sets against the installed package.
_GENITAL_LABELS = frozenset(
    {
        "FEMALE_GENITALIA_EXPOSED",
        "MALE_GENITALIA_EXPOSED",
        "ANUS_EXPOSED",
    }
)

# Secondary labels — flagged at reduced weight so that beach / bath photos
# are not aggressively penalised.
_SECONDARY_LABELS = frozenset(
    {
        "BUTTOCKS_EXPOSED",
        "FEMALE_BREAST_EXPOSED",
    }
)

# ---------------------------------------------------------------------------
# Public scoring function
# ---------------------------------------------------------------------------


def score_nudity(filepath: str) -> float:
    """Compute a nudity score in [0, 1] for the image at *filepath*.

    The score is dominated by exposed-genitalia detections.  Secondary
    exposures (buttocks, breasts) contribute at 30 % weight.

    Returns ``0.0`` when no relevant detections are found **or** when the
    detector raises an exception during a SINGLE-IMAGE detect() call
    (safe default — no penalty applied for transient image errors).

    Integrity failures (`ModelIntegrityError`) are NOT swallowed here.
    They propagate up so the caller / pipeline can abort cleanly
    rather than silently report every photo as 0.0 with a tampered
    model.

    .. note::

       ``NudeNet.detect()`` accepts a file path and handles its own
       resizing internally, so the original (not downscaled) image is used
       for maximum accuracy.
    """
    detector = _get_detector()
    if detector is None:
        return 0.0
    try:
        detections = detector.detect(filepath)
    except Exception as e:
        log.debug("NudeNet detection failed for %s: %s", filepath, e)
        return 0.0

    if not detections:
        return 0.0

    max_genital = 0.0
    max_secondary = 0.0

    for det in detections:
        label = det.get("class", "")
        confidence = det.get("score", 0.0)

        if label in _GENITAL_LABELS:
            max_genital = max(max_genital, confidence)
        elif label in _SECONDARY_LABELS:
            max_secondary = max(max_secondary, confidence)

    score = max_genital + 0.3 * max_secondary
    return min(1.0, score)
