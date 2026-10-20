"""Catalog entries: runtime-fetched models with no install wiring in the
``ModelRegistry`` (buffalo_s, NudeNet, LaMa). Each maps to (is-on-disk,
ensure-weights, remove-weights) so the picker can offer Download → Use →
Uninstall for them like for installable entries. Adding a catalog entry
means adding one row here."""

from __future__ import annotations

import functools
from collections.abc import Callable


@functools.cache
def loader_selected_ids() -> frozenset[str]:
    """Registry ids the loaders of kinds without a default select by
    themselves (content filter, pet detector, object removal): what the
    runtime uses, for the picker to show as running. Computed once; the
    ids are module constants."""
    from bpp.ai import inpainting
    from bpp.scoring import nudity, pets

    return frozenset({nudity.REGISTRY_ID, pets.REGISTRY_ID, inpainting.REGISTRY_ID})


def catalog_loaders() -> dict[
    str,
    tuple[Callable[[], bool], Callable[[], str], Callable[[], int]],
]:
    from bpp.ai.inpainting import ensure_lama_model
    from bpp.ai.inpainting import is_on_disk as lama_is_on_disk
    from bpp.ai.inpainting import remove_local_weights as lama_remove
    from bpp.scoring.face_embed_buffalo_s import ensure_buffalo_s_model
    from bpp.scoring.face_embed_buffalo_s import is_on_disk as buffalo_s_is_on_disk
    from bpp.scoring.face_embed_buffalo_s import remove_local_weights as buffalo_s_remove
    from bpp.scoring.nudity import ensure_nudenet_model
    from bpp.scoring.nudity import is_on_disk as nudenet_is_on_disk
    from bpp.scoring.nudity import remove_local_weights as nudenet_remove

    return {
        "insightface_buffalo_s": (buffalo_s_is_on_disk, ensure_buffalo_s_model, buffalo_s_remove),
        "nudenet_320n": (nudenet_is_on_disk, ensure_nudenet_model, nudenet_remove),
        "lama_inpaint_research": (lama_is_on_disk, ensure_lama_model, lama_remove),
    }
