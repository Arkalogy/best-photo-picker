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


Loader = tuple[Callable[[], bool], Callable[[], str], Callable[[], int]]

_PLUGIN_LOADERS: dict[str, Loader] = {}


def register_catalog_loader(
    registry_id: str,
    *,
    is_on_disk: Callable[[], bool],
    ensure_weights: Callable[[], str],
    remove_weights: Callable[[], int],
) -> None:
    """Give a plugin model the picker's Download → Use → Uninstall
    lifecycle: ``is_on_disk`` says whether its weights are present,
    ``ensure_weights`` fetches them (returns the path), ``remove_weights``
    deletes them (returns the bytes freed). A built-in id can't be taken
    over."""
    if registry_id in _builtin_loaders():
        raise ValueError(f"{registry_id!r} is a built-in catalog entry")
    _PLUGIN_LOADERS[registry_id] = (is_on_disk, ensure_weights, remove_weights)


def _reset_for_tests() -> None:
    _PLUGIN_LOADERS.clear()


def catalog_loaders() -> dict[str, Loader]:
    """The built-in loaders, then every plugin's."""
    return {**_builtin_loaders(), **_PLUGIN_LOADERS}


def _builtin_loaders() -> dict[str, Loader]:
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
