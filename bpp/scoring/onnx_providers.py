"""ONNX Runtime execution-provider selection — single source of truth.

Three call sites create ``onnxruntime.InferenceSession`` instances:

  * :mod:`bpp.scoring.face_scrfd` — SCRFD face detector
  * :mod:`bpp.scoring.clip_embed` — CLIP visual + text encoders
  * :mod:`bpp.scoring.pets` — YOLOv11n pet detector

Historically each one hardcoded ``providers=["CPUExecutionProvider"]``,
which is the safest default but leaves performance on the table for
operators with hardware acceleration available. This module gives all
three a single, opt-in switch:

  * ``BPP_ONNX_PROVIDERS`` env var, comma-separated list of provider
    names (e.g. ``"CoreMLExecutionProvider,CPUExecutionProvider"``)
  * Empty / unset → CPU only (current behaviour, untouched)
  * Unknown provider names are filtered out with a warning, *not*
    a crash; CPU is always appended as a final fallback so the
    session still loads even if the user typo'd.

Why opt-in and not auto-detect: hardware-accelerated providers vary
in stability and correctness across ONNX Runtime versions, OS
versions, and the specific model graphs we ship. A user who installed
``bppicker`` from PyPI on a fresh M3 might get CoreML acceleration —
or might trip a CoreML driver bug that returns garbage embeddings.
Either is fine for the user who *chose* it; neither is acceptable as
a silent default.

Documented hardware-support matrix (also in README → Hardware):

  | Provider                     | Status                  |
  |------------------------------|-------------------------|
  | CPUExecutionProvider         | tested, default         |
  | CoreMLExecutionProvider      | supported, **untested** |
  | CUDAExecutionProvider        | supported, **untested** |
  | DmlExecutionProvider (Win)   | supported, **untested** |

"Supported" means the code path exists and will accept the provider
name. "Tested" means we run it in CI on every push. Anything between
the two is on the user's risk surface.
"""

from __future__ import annotations

import os

from bpp.utils.logging import get_logger

log = get_logger(__name__)

#: Environment variable read on every call. Comma-separated list of
#: ONNX Runtime execution provider names, in priority order.
_ENV_KEY = "BPP_ONNX_PROVIDERS"

#: Fallback provider — always present, always last. ONNX Runtime
#: ships this with every wheel, so the session can always load.
_CPU = "CPUExecutionProvider"

#: Provider names we recognize as "supported but untested." Names
#: outside this set still pass through (so a future ONNX Runtime
#: provider can be opted-in without a code change), they just don't
#: get logged as a known-good selection.
_KNOWN_PROVIDERS: frozenset[str] = frozenset(
    {
        _CPU,
        "CoreMLExecutionProvider",
        "CUDAExecutionProvider",
        "DmlExecutionProvider",
        "TensorrtExecutionProvider",
        "OpenVINOExecutionProvider",
    }
)


def _available_providers() -> set[str]:
    """Return the set of providers the installed onnxruntime supports.

    Used to filter out env-var entries the user requested but that
    aren't actually compiled into their wheel — silently dropping
    them is friendlier than crashing the session, and we still log
    the mismatch so an operator can debug why their CoreML opt-in
    didn't take effect.
    """
    try:
        import onnxruntime as ort
    except ImportError:
        return {_CPU}
    try:
        return set(ort.get_available_providers())
    except Exception:
        return {_CPU}


def get_providers() -> list[str]:
    """Return the ONNX Runtime providers list to pass to InferenceSession.

    Reads ``BPP_ONNX_PROVIDERS`` and filters against what the installed
    onnxruntime wheel actually supports. Always appends
    ``CPUExecutionProvider`` as the final fallback so the session can
    always be created even when the user-requested provider is
    unavailable in the wheel.
    """
    raw = os.environ.get(_ENV_KEY, "").strip()
    if not raw:
        return [_CPU]

    requested = [p.strip() for p in raw.split(",") if p.strip()]
    available = _available_providers()
    selected: list[str] = []
    for p in requested:
        if p in available:
            selected.append(p)
        else:
            log.warning(
                "%s requested provider %r is not in the installed onnxruntime "
                "wheel (%s); skipping. Install onnxruntime-gpu / "
                "onnxruntime-coreml / etc. to enable it, or remove from "
                "%s to silence this warning.",
                _ENV_KEY,
                p,
                sorted(available),
                _ENV_KEY,
            )
    if _CPU not in selected:
        selected.append(_CPU)

    if selected != [_CPU]:
        log.info(
            "ONNX Runtime providers in priority order: %s "
            "(supported but untested by upstream CI; see README → Hardware)",
            selected,
        )
    return selected
