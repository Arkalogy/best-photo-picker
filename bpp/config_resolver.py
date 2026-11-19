"""Layered configuration resolver.

bpp's configuration historically lived in three places:

1. ``bpp.config.DEFAULTS`` — code-level defaults baked into the package
2. YAML config file passed via ``--config`` (overlays DEFAULTS at boot)
3. ``settings`` table in the DB — runtime-mutable, persists across restarts

Without a unified resolver, callers had to know which layer to read
from, and DB values came back as strings while DEFAULTS values had
real types — a silent landmine for any code that did
``int(ctx.config.get("k"))`` and got bitten when DB returned "50".

This module is the resolver. ``Config`` walks DB → YAML → DEFAULTS in
precedence order, coerces DB strings to the type implied by DEFAULTS,
and exposes a ``get/set/__getitem__/__contains__`` surface that
matches dict semantics so existing call sites work unchanged.

Future per-user / per-album scoping slots in here naturally — add a
fourth layer above DB (user_settings table keyed by user_id, populated
from a request principal) and update the precedence chain. No callers
need to know.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from bpp.config import DEFAULTS, load_config
from bpp.utils.logging import get_logger

log = get_logger(__name__)


#: Settings that describe this computer or deployment, not a library.
#: The library database travels with its folder and may come from someone
#: else, so these are read from the owner's YAML (or the defaults) only.
MACHINE_KEYS = frozenset(
    {"ssl_context", "behind_proxy", "_face_extract_workers", "_face_extract_pool"}
)


def checked_setting(conn: Any, key: str) -> Any:
    """A library-stored setting read straight from a connection, with the
    same check as :class:`Config`: the stored value when it is inside the
    setting's declared limits, else the default."""
    from bpp.db.settings import get_setting

    raw = get_setting(conn, key)
    value = stored_value(key, raw) if isinstance(raw, str) else _REJECTED
    return DEFAULTS.get(key) if value is _REJECTED else value


_REJECTED = object()
_warned_rejections: set[tuple[str, str]] = set()


def stored_value(key: str, raw: str) -> Any:
    """A library-stored setting as the app may use it: coerced and inside
    the limits its schema declares, else ``_REJECTED`` (warned once). Keys
    without a schema are returned as stored."""
    from bpp.config_schema import get_schema, validate_value

    schema = get_schema(key)
    if schema is None:
        return raw
    if getattr(schema, "type", str) not in (bool, int, float, str):
        # A plugin's list / dict / own type: stored as text the app can't
        # turn back, and its validator was written for the real value.
        # Returned as stored, as before.
        return raw
    try:
        return validate_value(key, raw)
    except Exception as exc:  # a plugin's validator may raise anything
        if (key, raw) not in _warned_rejections:
            _warned_rejections.add((key, raw))
            log.warning("Ignoring the library's stored setting %s: %s", key, exc)
        return _REJECTED


def _coerce(value: str, target_type: type) -> Any:
    """Coerce a DB string to the target type implied by DEFAULTS.

    DB stores everything as strings; DEFAULTS gives us the canonical
    type. Bool/int/float/string handled inline; anything else returns
    the raw string and lets the caller decide.
    """
    if target_type is bool:
        return value.lower() in ("true", "1", "yes", "on")
    if target_type is int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    if target_type is float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    if target_type is str:
        return value
    # Unknown target type — return the raw string. Caller can coerce.
    return value


class Config:
    """Layered config view: DB → YAML → DEFAULTS, with type coercion.

    Constructed once per WebAppState. Holds a snapshot of the YAML
    overlay (immutable for this lifetime) and a reference to the DB
    connection-getter (so DB reads always see fresh values).

    Backward compat: instances support ``get(key, default)``,
    ``[key]``, ``in``, and iteration — anywhere ``ctx.config`` was a
    dict before, ``Config`` works as a drop-in replacement.

    Mutation goes through ``set(key, value)`` which writes to the DB
    layer (persisting across restarts). YAML is read-only at runtime.
    """

    def __init__(self, yaml_overlay: dict[str, Any], get_conn: Any | None = None):
        # YAML and DEFAULTS are stable for the WebAppState lifetime;
        # cache the merged view as the "no-DB" baseline.
        self._yaml = dict(yaml_overlay) if yaml_overlay else {}
        self._get_conn = get_conn  # callable returning sqlite3.Connection, or None

    # ── Read path ────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """Return the resolved value for ``key``.

        Precedence: DB → YAML → DEFAULTS → caller's ``default``.
        DB values get coerced to the type of DEFAULTS[key] when present;
        otherwise returned as the raw string.
        """
        # 1. DB layer (highest priority)
        db_value = self._db_get(key)
        if db_value is not None:
            db_value = stored_value(key, db_value)
        if db_value is not None and db_value is not _REJECTED:
            if not isinstance(db_value, str):
                return db_value  # checked and coerced by its schema
            target_type = type(DEFAULTS.get(key)) if key in DEFAULTS else None
            if target_type is None and key in self._yaml:
                target_type = type(self._yaml[key])
            if target_type is not None:
                return _coerce(db_value, target_type)
            return db_value

        # 2. YAML overlay
        if key in self._yaml:
            return self._yaml[key]

        # 3. Defaults
        if key in DEFAULTS:
            return DEFAULTS[key]

        # 4. Caller's default (matches dict.get behavior)
        return default

    def __getitem__(self, key: str) -> Any:
        sentinel = object()
        value = self.get(key, sentinel)
        if value is sentinel:
            raise KeyError(key)
        return value

    def __contains__(self, key: str) -> bool:
        if self._db_get(key) is not None:
            return True
        return key in self._yaml or key in DEFAULTS

    def __iter__(self) -> Iterator[str]:
        """Iterate the union of all keys across layers, deduplicated."""
        seen: set[str] = set()
        for key in self._db_keys():
            if key not in seen:
                seen.add(key)
                yield key
        for key in self._yaml:
            if key not in seen:
                seen.add(key)
                yield key
        for key in DEFAULTS:
            if key not in seen:
                seen.add(key)
                yield key

    def keys(self) -> list[str]:
        """List of all keys across layers (Mapping protocol — needed
        for `dict(config)` to work)."""
        return list(iter(self))

    def values(self) -> list[Any]:
        return [self.get(key) for key in self]

    def items(self) -> list[tuple[str, Any]]:
        return [(key, self.get(key)) for key in self]

    def as_dict(self) -> dict[str, Any]:
        """Flat merged snapshot. Used by /api/settings to serialize."""
        return {key: self.get(key) for key in self}

    # ── Write path ───────────────────────────────────────────────────

    def set(self, key: str, value: Any) -> None:
        """Persist a setting to the DB layer (overrides YAML / DEFAULTS).

        Stored as ``str(value)``; coercion back to the right type
        happens on read via the DEFAULTS-implied target type.

        routes through ``bpp.config_schema.validate_value``
        for any key that has a registered schema entry. Type
        coercion + bounds + choices + custom validators all run at
        write time so a misconfig fails loudly rather than silently
        corrupting a later read. Unschematized keys (plugin keys
        without a schema, free-form strings) pass through unchanged.
        """
        if self._get_conn is None:
            raise RuntimeError("Config.set called without a DB connection")
        from bpp.config_schema import validate_value
        from bpp.db.settings import set_setting

        coerced = validate_value(key, value)
        set_setting(self._get_conn(), key, coerced)

    def __setitem__(self, key: str, value: Any) -> None:
        """Dict-style assignment delegates to .set() — persists to DB."""
        self.set(key, value)

    # ── Internals ────────────────────────────────────────────────────

    def _db_get(self, key: str) -> str | None:
        if self._get_conn is None or key in MACHINE_KEYS:
            return None
        from bpp.db.settings import get_setting

        try:
            return get_setting(self._get_conn(), key)
        except Exception:
            # DB isn't ready yet (early in startup) or transient failure —
            # fall through to YAML/DEFAULTS rather than blowing up. Log
            # at warning so a corrupt settings table that silently drops
            # the user's overrides doesn't disappear without a trace.
            log.warning(
                "Config DB read failed for %r — falling back to YAML/DEFAULTS",
                key,
                exc_info=True,
            )
            return None

    def _db_keys(self) -> list[str]:
        if self._get_conn is None:
            return []
        from bpp.db.settings import get_all_settings

        try:
            stored = get_all_settings(self._get_conn())
            for key in sorted(MACHINE_KEYS & stored.keys()):
                if ("machine", key) not in _warned_rejections:
                    _warned_rejections.add(("machine", key))
                    log.warning(
                        "Ignoring %s from the library's settings: it configures this "
                        "computer, so set it in the YAML config file instead",
                        key,
                    )
            return [k for k in stored if k not in MACHINE_KEYS]
        except Exception:
            log.warning(
                "Config DB enumerate failed — settings layer skipped",
                exc_info=True,
            )
            return []


def make_config(
    config_path: str | None,
    get_conn: Any | None = None,
) -> Config:
    """Construct a Config from a YAML path + DB connection getter.

    Mirrors the old ``load_config(path)`` shape so callers that build
    a config without a WebAppState (CLI commands) can use it.
    """
    yaml_overlay: dict[str, Any] = {}
    if config_path:
        full = load_config(config_path)
        # `load_config` already merges over DEFAULTS — strip the
        # defaults so we keep YAML-only keys for the overlay layer.
        yaml_overlay = {k: v for k, v in full.items() if v != DEFAULTS.get(k)}
    return Config(yaml_overlay, get_conn)
