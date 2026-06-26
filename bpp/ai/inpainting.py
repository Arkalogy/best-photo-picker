"""Object removal via LaMa inpainting.

Requires optional dependency: pip install bppicker[inpaint]
Model (~200MB, ``big-lama.pt``) is pre-fetched via bpp's verified-
download helper (SHA-256 pinned, 600 s timeout) and the path is
handed to ``simple_lama_inpainting`` via the library's documented
``LAMA_MODEL`` env-var override. The library then loads the
already-verified file via ``torch.jit.load`` and skips its own
``torch.hub.download_url_to_file`` (which has no SHA-256
verification and no enforced timeout — the original integrity gap).

History (kept here for the next reviewer): prior to this fix, the
weights were fetched directly by the library and a compromised
GitHub Releases host or MITM'd proxy could substitute different
torch-checkpoint bytes. Torch checkpoints are pickle-based and can
execute code at unpickling time → RCE. The
``simple_lama_inpainting`` dep is opt-in
(``pip install bppicker[inpaint]``), so the previous footgun was
limited to users who'd installed that extra; still, the asymmetry
with every other bpp model (all SHA-pinned + verified) was a real
gap. This fix closes it.
"""

from __future__ import annotations

import os
from io import BytesIO

from PIL import Image

from bpp.scoring.model_base import ModelSingleton
from bpp.utils.logging import get_logger
from bpp.utils.paths import models_dir

log = get_logger(__name__)

# Upstream URL hardcoded in simple_lama_inpainting/models/model.py:7-10.
# We fetch the same file ourselves but verify SHA-256 before letting
# torch.jit.load see it.
_LAMA_MODEL_URL = (
    "https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt"
)
# SHA-256 of big-lama.pt at the URL above. Computed locally on
# 2026-05-07 (size: 205,803,670 bytes). Pin both the bytes and the
# upstream contract — if the release is ever overwritten or the URL
# starts redirecting elsewhere, our verified-download path refuses to
# load and the user gets a clear error rather than silent code
# execution. Re-pin only after auditing the new upstream artifact.
_LAMA_MODEL_SHA256 = "7ba7aa7ac37a4d41fdbbeba3a2af7ead18058552997e3a3cd1a3b2210c9e6b4c"
_LAMA_MODEL_PATH = models_dir() / "big-lama.pt"


def _import_check() -> None:
    """Raise ImportError if simple_lama_inpainting is not installed."""
    import simple_lama_inpainting  # noqa: F401


def _create_lama(verified_path):
    """Construct SimpleLama against an already-verified LaMa file.

    By the time ``ModelSingleton`` calls this, ``ensure_model()`` has
    either downloaded the file with SHA-256 verification or
    re-verified an existing cached file — see
    ``bpp.scoring.model_base.ModelSingleton.ensure_model``. We just
    point ``simple_lama_inpainting`` at the verified path via its
    documented ``LAMA_MODEL`` env-var override (the library's
    ``models/model.py`` checks this env var BEFORE calling
    ``torch.hub.download_url_to_file``, so its unsafe download path
    never runs).
    """
    from simple_lama_inpainting import SimpleLama

    os.environ["LAMA_MODEL"] = str(verified_path)
    log.info("Loading LaMa inpainting model from %s", verified_path)
    instance = SimpleLama()
    log.info("LaMa model loaded")
    return instance


# Same lifecycle as every other bpp ML model: ModelSingleton checks
# `model_path.exists() + verify SHA`, downloads if missing, calls
# `create_fn(verified_path)`. The Settings → Advanced → ML Models
# panel reads this registration to surface a Redownload affordance,
# Uninstall control, and the pinned-download consent prompt before
# the first inpaint click.
_LAMA = ModelSingleton(
    name="LaMa inpainting",
    model_path=_LAMA_MODEL_PATH,
    model_url=_LAMA_MODEL_URL,
    model_sha256=_LAMA_MODEL_SHA256,
    create_fn=_create_lama,
    registry_id="lama_inpaint_research",
    import_check=_import_check,
)


def is_available() -> bool:
    """Check if inpainting dependencies are installed."""
    return _LAMA.is_available()


# ── Catalog-loader hooks ────────────────────────────────────────────
#
# LaMa is a runtime-fetched catalog entry (weights pulled on demand,
# not listed in the download manifest). The Settings → Models picker
# drives its Review → Download → Use → Uninstall lifecycle through
# this trio, registered in bpp.web.bp_model_registry._catalog_loaders.
# Mirrors the nudity / buffalo_s catalog hooks.


def is_on_disk() -> bool:
    """Return True if the locally-cached LaMa weight file exists.

    Cheap existence check only (no SHA verify) — the picker reads it to
    decide between "Download" and "Uninstall". A tampered cache is
    caught and re-fetched at load time by :func:`ensure_lama_model`.
    """
    return _LAMA_MODEL_PATH.exists()


def ensure_lama_model() -> str:
    """Download + verify the LaMa weights NOW. Returns the local path.

    Routes through :meth:`ModelSingleton.ensure_model`, which calls the
    canonical :func:`bpp.utils.download.download_file` gate
    (``registry_id="lama_inpaint_research"``) — the policy gate fires
    BEFORE the network call, so the explicit "Download" button cannot
    bypass license acceptance.
    """
    # Bracket the fetch with start/finish logs so a hang or slow
    # download is visible in server.log (a ~200 MB file fetch was
    # previously silent until the endpoint logged completion).
    # ``ensure_model`` is a no-op verify when the file is already
    # cached, so the wording stays neutral ("Ensuring"/"ready").
    log.info("Ensuring LaMa inpainting weights (source=%s)", _LAMA_MODEL_URL)
    path = _LAMA.ensure_model()
    if path is None:
        raise RuntimeError(
            f"LaMa weights could not be downloaded "
            f"(registry_id=lama_inpaint_research, source={_LAMA_MODEL_URL}; "
            f"ensure_model returned None — check network access and the "
            f"pinned upstream URL)."
        )
    log.info("LaMa inpainting weights ready: %s", path)
    return str(path)


def remove_local_weights() -> int:
    """Delete the cached LaMa weights. Returns the bytes freed.

    Backs the picker's Uninstall action and resets the in-process
    singleton so the next load re-runs ensure → download → verify.
    Symmetric counterpart to :func:`ensure_lama_model`. Idempotent.

    A failed unlink is logged at WARNING rather than swallowed: the
    Uninstall would otherwise report success while the 200 MB file
    stays on disk, with no trail to diagnose why (project convention:
    nothing should be silent).
    """
    freed = 0
    if _LAMA_MODEL_PATH.exists():
        freed = _LAMA_MODEL_PATH.stat().st_size
        try:
            _LAMA_MODEL_PATH.unlink()
        except OSError:
            freed = 0
            log.warning(
                "Failed to delete LaMa weights at %s",
                _LAMA_MODEL_PATH,
                exc_info=True,
            )
    tmp = _LAMA_MODEL_PATH.with_suffix(_LAMA_MODEL_PATH.suffix + ".tmp")
    if tmp.exists():
        try:
            tmp.unlink()
        except OSError:
            log.warning("Failed to delete LaMa temp file at %s", tmp, exc_info=True)
    _LAMA.reset()
    return freed


def _get_model():
    """Return the lazily-initialised LaMa model, or None if unavailable.

    Enforces the registry policy gate FIRST. LaMa weights are
    research-only / non-commercial; the click-through acceptance
    dialog must have been completed (with separate-rights assertion
    in commercial mode) before the model loads. Raises
    :class:`bpp.registry.ModelLoadBlockedError` otherwise.

    Kept as a public-ish symbol because existing tests patch it directly.
    Internally delegates to the ModelSingleton — same thread-safe lazy
    init as every other ML model in the codebase.
    """
    from bpp.registry import enforce_load_policy_for

    enforce_load_policy_for("lama_inpaint_research")
    return _LAMA.get()


def inpaint(image: Image.Image, mask: Image.Image) -> Image.Image:
    """Remove masked area from image using LaMa inpainting.

    Args:
        image: RGB input image.
        mask: Grayscale or binary mask. White (255) = area to remove.

    Returns:
        Inpainted RGB image (same size as input).

    Raises:
        RuntimeError: If simple-lama-inpainting is not installed.
        ValueError: If image/mask sizes don't match.
    """
    if image.size != mask.size:
        raise ValueError(f"Image size {image.size} doesn't match mask size {mask.size}")

    # Ensure correct modes
    if image.mode != "RGB":
        image = image.convert("RGB")
    if mask.mode != "L":
        mask = mask.convert("L")

    model = _get_model()
    if model is None:
        raise RuntimeError("Inpainting not available. Install with: pip install bppicker[inpaint]")
    return model(image, mask)


def inpaint_from_bytes(image_bytes: bytes, mask_bytes: bytes) -> bytes:
    """Convenience: accept and return PNG bytes."""
    # Context managers release the BytesIO-backed Image handles before
    # inpaint() runs. convert() returns a fresh Image instance so the
    # source handles can close at the end of each `with`.
    with Image.open(BytesIO(image_bytes)) as img_in:
        image = img_in.convert("RGB")
    with Image.open(BytesIO(mask_bytes)) as mask_in:
        mask = mask_in.convert("L")

    result = inpaint(image, mask)

    buf = BytesIO()
    result.save(buf, format="PNG")
    return buf.getvalue()
