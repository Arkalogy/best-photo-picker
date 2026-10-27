"""Every model switch a scoring run honors: the built-in Settings
toggles plus the ``toggle_key`` of each registered detector and scorer,
so a plugin's switch is stored, read and shown like a built-in's."""

from __future__ import annotations

from bpp.constants import MODEL_TOGGLE_KEYS


def all_toggle_keys() -> tuple[str, ...]:
    """Built-in toggle keys first, then the registered plugin keys. Loads
    the plugins first: a spawned analysis process reads the switches before
    anything else would have loaded them, and a missing key means "on"."""
    from bpp.plugins import ensure_loaded_once
    from bpp.scoring.face_detector_registry import list_detectors

    ensure_loaded_once()
    from bpp.scoring.registry import iter_scorers

    keys: list[str] = list(MODEL_TOGGLE_KEYS)
    for det in list_detectors():
        if det.toggle_key and det.toggle_key not in keys:
            keys.append(det.toggle_key)
    for scorer in iter_scorers():
        if scorer.toggle_key and scorer.toggle_key not in keys:
            keys.append(scorer.toggle_key)
    return tuple(keys)


def toggle_on(value: object) -> bool:
    """A stored switch as a bool. Built-in keys come back from Config as
    bools; a plugin's key has no DEFAULTS entry, so its value is the raw
    settings string ("false"), which ``bool()`` would read as on."""
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def toggles_from_config(config: dict) -> dict[str, bool]:
    """``{toggle_key: on?}`` for every switch, unset meaning on."""
    return {key: toggle_on(config.get(key, True)) for key in all_toggle_keys()}
