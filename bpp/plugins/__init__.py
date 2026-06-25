"""Auto-load third-party plugin packages via setuptools entry-points.

Rounds 8 and 9 added public registries (FaceDetectorRegistry,
FaceEmbedderRegistry, ConfigSchema, WorkerRegistry, SmartAlbumRegistry)
designed for plugin authors to extend without forking. But nothing
actually IMPORTED third-party plugin packages — registration happens
as an import side effect, so a plugin wheel installed in the venv
sits dormant unless the app already imports it.

This module provides the missing piece: ``load_plugin_entry_points()``
walks the ``bpp.plugins`` entry-point group and loads each registered
callable. Plugin authors declare in their ``pyproject.toml``:

    [project.entry-points."bpp.plugins"]
    my_extension = "my_pkg.bpp_plugin:setup"

The ``setup`` callable takes no arguments and is expected to perform
its registrations at module / function scope (e.g. call
``register_detector(...)`` or ``register_field(...)``).

Loader contract:
  * **Off by default**. Plugin loading is opt-in via the
    ``BPP_ENABLE_PLUGINS=1`` environment variable. Without it, any
    ``bpp.plugins`` entry-points in the venv are ignored — including
    benign ones. Rationale: a malicious package on PYTHONPATH can
    declare an entry-point and run arbitrary code at startup, with
    the same privileges as bpp itself. Trust contract is documented
    in SECURITY.md → Plugins.
  * **Idempotent** — the loader tracks loaded entry-points in a
    process-global set, so repeated calls (one from the web app, one
    from a CLI command in the same process) don't double-register.
  * **Best-effort** — a single broken plugin logs a warning with
    `exc_info` and the loader continues with the rest. Startup is
    NEVER aborted by a plugin failure.
  * **Lazy** — only fires when called; the module itself has no
    import-time side effects beyond defining the function.

Production callsites: ``bpp.web.app.create_app`` calls this once
before constructing ``WebAppState``, so registries are populated
before any request can read them. The CLI ``do_pick`` /
``do_analyze`` paths call it before consuming the scoring registry.
"""

from __future__ import annotations

import importlib.metadata as _meta
import os
import threading
from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from bpp import __version__ as _BPP_VERSION
from bpp.utils.logging import get_logger

_log = get_logger(__name__)

#: Setuptools entry-point group. Plugin authors declare their setup
#: callable under this group in their package's pyproject.toml.
PLUGIN_ENTRY_POINT_GROUP = "bpp.plugins"


@runtime_checkable
class BppPluginAPI(Protocol):
    """Optional Protocol that plugin modules SHOULD conform to.

    Conformance is duck-typed — a plugin's module-level entry point
    only has to be a zero-arg callable. The metadata attributes
    below are optional but recommended; bpp reads them at load time
    to enforce a version contract and surface clearer log lines.

    Plugin module shape:

        # my_pkg/bpp_plugin.py
        __plugin_name__ = "my-bpp-plugin"
        __plugin_version__ = "1.2.3"
        __bpp_version_required__ = ">=0.1,<0.3"

        def setup() -> None:
            ...

    All four are public attributes by convention. The loader reads
    them from `getattr(module, '__plugin_name__', None)` etc., so a
    plugin that omits them still loads (silently, with no version
    check). A plugin that declares ``__bpp_version_required__`` and
    does not match the running ``bpp.__version__`` is skipped with a
    warning — better than letting the plugin crash mid-registration
    on an API drift.

    Versioning policy bpp commits to:
      * Semver. Plugins should pin lower bound + an exclusive upper
        bound on the next major (e.g. ``>=0.1,<0.3``).
      * Registry signatures stay stable across minor versions. Adding
        a new optional field on a registered dataclass is a minor
        bump; removing or repurposing a field is a major bump.
      * Removed registries get a deprecation log two minor versions
        ahead of removal.
    """

    def setup(self) -> None: ...


#: env-var flag the operator must explicitly set to enable
#: third-party plugin loading. Default off — see SECURITY.md.
PLUGIN_ENABLE_ENV = "BPP_ENABLE_PLUGINS"

# Process-wide guard so repeated calls don't re-invoke a plugin's
# setup function (which would re-register entries / emit duplicate
# debug logs / re-do whatever side effects the plugin chose).
_loaded_lock = threading.Lock()
_loaded: set[str] = set()
# Latched flag so the "plugins disabled" INFO log fires at most once
# per process — three startup paths call the loader and we don't
# want the message in triplicate.
_disabled_log_emitted = False


def _plugins_enabled() -> bool:
    """Return True iff the operator explicitly opted in via env var.

    Accept the standard truthy strings (``1`` / ``true`` / ``yes``,
    case-insensitive) so the flag matches BPP_TRUSTED_PROXIES and
    other env-var conventions. Empty / unset / any other value =
    disabled.
    """
    raw = os.environ.get(PLUGIN_ENABLE_ENV, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _read_plugin_metadata(target: Any, ep_id: str) -> dict[str, str]:
    """Pull `__plugin_name__` / `__plugin_version__` /
    `__bpp_version_required__` off the loaded plugin target.

    The entry-point target is usually a function (the `setup`
    callable). Plugin metadata lives on the MODULE that defines that
    function, so we look up the module via `target.__module__` and
    inspect `sys.modules`. Falls back to scanning `target` directly
    in case a plugin author put the metadata on the function itself.
    """
    import sys

    module_name = getattr(target, "__module__", None)
    module = sys.modules.get(module_name) if module_name else None

    out: dict[str, str] = {}
    for attr in ("__plugin_name__", "__plugin_version__", "__bpp_version_required__"):
        # Prefer module-level metadata; fall back to attribute on the
        # target itself (e.g. when the entry point IS the module).
        value = getattr(module, attr, None) if module else None
        if value is None:
            value = getattr(target, attr, None)
        if isinstance(value, str) and value:
            out[attr] = value
    if out:
        _log.debug("Plugin %r metadata: %s", ep_id, out)
    return out


def _check_bpp_version_requirement(spec_str: str, plugin_id: str) -> bool:
    """Return True if the running bpp version satisfies *spec_str*.

    Returns True if the spec string is unparseable or empty (skip the
    check rather than refuse to load — better to give the plugin a
    shot than block on a malformed pin). False only when the spec
    parses cleanly AND `bpp.__version__` doesn't satisfy it.
    """
    if not spec_str:
        return True
    try:
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
        from packaging.version import InvalidVersion, Version
    except ImportError:
        # `packaging` is a pip dependency so this should never fire;
        # if it does, we err on the side of loading the plugin.
        _log.debug(
            "Plugin %r: packaging library unavailable; skipping version check",
            plugin_id,
        )
        return True

    try:
        spec = SpecifierSet(spec_str)
    except InvalidSpecifier:
        _log.warning(
            "Plugin %r declared __bpp_version_required__=%r, but the "
            "spec is unparseable. Loading anyway. Use a setuptools-style "
            "spec like '>=0.1,<0.3'.",
            plugin_id,
            spec_str,
        )
        return True

    try:
        running = Version(_BPP_VERSION)
    except InvalidVersion:
        _log.debug(
            "bpp.__version__=%r is not a valid PEP-440 version; "
            "skipping plugin version check for %r",
            _BPP_VERSION,
            plugin_id,
        )
        return True

    return running in spec


def _iter_entry_points(group: str) -> Iterable[Any]:
    """Yield the entry-points registered in *group*, handling the
    Python 3.10+ selectable API and the legacy dict shape used by
    some installer backends."""
    try:
        eps = _meta.entry_points()
    except Exception:
        # importlib.metadata can raise if the metadata is unreadable
        # (corrupt install, partial removal). Log + give up silently
        # so the app still boots.
        _log.warning("Could not enumerate entry points", exc_info=True)
        return ()

    # Python 3.10+ selectable API: entry_points() returns an
    # EntryPoints group object that supports `.select(group=...)`.
    select = getattr(eps, "select", None)
    if callable(select):
        return select(group=group)

    # Older fallback: dict-of-list shape.
    if isinstance(eps, dict):
        return eps.get(group, ())

    return ()


def load_plugin_entry_points() -> int:
    """Load every entry-point in ``bpp.plugins``.

    Returns the count of plugins newly loaded by this call (0 on
    re-invocation after the initial load OR when plugins are disabled).

    Each entry-point's loaded value is called as a no-arg function.
    Failures are logged and skipped — startup never aborts because of
    a plugin.

    gated on ``BPP_ENABLE_PLUGINS=1`` — without the opt-in,
    this is a quiet no-op. The first call per process logs an INFO
    breadcrumb so an operator who installed a plugin and is wondering
    why it didn't load can find the env-var requirement.
    """
    global _disabled_log_emitted

    if not _plugins_enabled():
        with _loaded_lock:
            should_log = not _disabled_log_emitted
            _disabled_log_emitted = True
        if should_log:
            _log.info(
                "Plugin loading disabled (set %s=1 to enable). "
                "See SECURITY.md → Plugins for the trust contract.",
                PLUGIN_ENABLE_ENV,
            )
        return 0

    # don't hold the lock while invoking plugin setup() —
    # a plugin whose setup takes 5s would otherwise block every other
    # call to the loader for the same duration. Capture the work-list
    # under lock, release, run setup() outside, re-acquire to mark
    # loaded.
    pending: list[tuple[str, Any]] = []
    with _loaded_lock:
        for ep in _iter_entry_points(PLUGIN_ENTRY_POINT_GROUP):
            ep_id = f"{ep.name}={getattr(ep, 'value', '?')}"
            if ep_id in _loaded:
                continue
            try:
                target = ep.load()
            except Exception:
                _log.warning("Plugin %r failed to import; skipping", ep_id, exc_info=True)
                _loaded.add(ep_id)  # don't keep retrying
                continue
            # Mark loaded BEFORE running setup so a concurrent caller
            # racing in doesn't queue a duplicate.
            _loaded.add(ep_id)
            pending.append((ep_id, target))

    newly_loaded = 0
    for ep_id, target in pending:
        # Read optional metadata + check the bpp version requirement
        # BEFORE running setup(). A plugin built for a future bpp
        # major would otherwise crash mid-registration on a missing
        # registry / changed signature; far better to skip with a
        # clear log than to ship a broken partial state.
        metadata = _read_plugin_metadata(target, ep_id)
        version_required = metadata.get("__bpp_version_required__", "")
        if version_required and not _check_bpp_version_requirement(version_required, ep_id):
            _log.warning(
                "Plugin %r requires bpp%s but bpp.__version__=%r "
                "doesn't satisfy it. Skipping. Either upgrade/downgrade "
                "bpp or update the plugin's __bpp_version_required__.",
                ep_id,
                version_required,
                _BPP_VERSION,
            )
            newly_loaded += 1  # mark as processed; don't retry
            continue

        plugin_label = metadata.get("__plugin_name__", ep_id)
        plugin_version = metadata.get("__plugin_version__", "")
        version_suffix = f" v{plugin_version}" if plugin_version else ""

        try:
            if callable(target):
                target()
            else:
                # Non-callable target is fine — the import side effect
                # may have done the registration. Surface at INFO (not
                # DEBUG) so the plugin author has a breadcrumb that
                # their entry point loaded by import-side-effects vs.
                # by an explicit setup call.
                _log.info(
                    "Plugin %s%s loaded (target not callable; relying on import-time side effects)",
                    plugin_label,
                    version_suffix,
                )
                newly_loaded += 1
                continue
        except Exception:
            _log.warning(
                "Plugin %s%s setup function raised; partial registrations "
                "may remain. Continuing with other plugins.",
                plugin_label,
                version_suffix,
                exc_info=True,
            )
        else:
            _log.info("Loaded plugin %s%s", plugin_label, version_suffix)
        newly_loaded += 1
    return newly_loaded


def _reset_for_tests() -> None:
    """Test hook: clear the loaded-plugins set so a parametrized test
    can simulate fresh process startup. Production code never calls
    this — it would let a stale plugin's setup re-run."""
    global _disabled_log_emitted
    with _loaded_lock:
        _loaded.clear()
        _disabled_log_emitted = False
