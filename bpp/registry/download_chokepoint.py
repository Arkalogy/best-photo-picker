"""Block third-party packages from silently auto-downloading models.

Batch 3 / item 18 of the legal-posture rollout. The spec
specifically called out that BPP's click-through dialog (Batch 4) is
cosmetic if a transitively-imported package (insightface and
friends) can silently fetch a restricted model on first use behind
our back. This module installs the structural defence:

* At import time, any known third-party auto-downloader whose
  package is already loaded gets patched with
  :func:`_make_blocked_downloader`. Subsequent calls raise
  :class:`BlockedAutoDownloadError`.

* For packages loaded *after* ``bpp.registry`` (a contributor adds
  ``import insightface`` to a BPP module that's imported later), a
  custom :class:`importlib.abc.MetaPathFinder` watches every import
  and applies the patch as soon as the target submodule materialises
  in ``sys.modules``. The finder leaves resolution to the standard
  finders; it only intercepts the post-load patching.

* :func:`enforce_chokepoint` is the runtime tripwire. Pipeline entry
  points (face extraction, semantic dedup, anywhere that might
  trigger a model load) call it before doing any work. If any
  expected patch is missing the call raises rather than letting
  extraction proceed under a silent-leak risk. This is the "fail
  closed if interception breaks" guarantee Q7 picked.

What this is NOT

* It is not a network-level block. A package that bypasses the
  named downloader and rolls its own HTTPS fetch will not be
  caught here. Adding a new package to :data:`KNOWN_AUTO_DOWNLOADERS`
  is the maintenance path for that case.

* It does not handle restricted-model downloads that BPP itself
  initiates through the (forthcoming) Batch-4 click-through dialog.
  Those downloads use the registry-coordinated path and never go
  through the patched function. See :func:`enter_registry_download`
  for the context-manager BPP's own download flow will eventually
  use to bypass the block.

What it does NOT do today, intentionally

* No protection against `torch.hub.load` style downloads. Add an
  entry when a BPP code path depends on one.

* No protection against `transformers.AutoModel.from_pretrained`.
  Same rule.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from bpp.utils.logging import get_logger

_log = get_logger(__name__)


class BlockedAutoDownloadError(RuntimeError):
    """A third-party package tried to auto-download a model behind
    BPP's back.

    The exception carries the package name, the patched function path,
    and the specific call's args/kwargs so the failure log makes the
    cause unambiguous. The expected user-facing remedy is one of:

    * Configure BPP's model registry to point at the right model and
      let BPP's own download flow (Batch 4) fetch it under the
      click-through dialog.
    * Install the model file manually via BYOM and point BPP at it.

    Production code MUST NOT catch and swallow this. It surfaces a
    silent-leak attempt that the legal posture was designed to
    prevent; suppressing it defeats the chokepoint.
    """

    def __init__(
        self,
        *,
        package: str,
        function_path: str,
        call_args: tuple[Any, ...] = (),
        call_kwargs: dict[str, Any] | None = None,
        rationale: str = "",
    ) -> None:
        self.package = package
        self.function_path = function_path
        self.call_args = call_args
        self.call_kwargs = call_kwargs or {}
        self.rationale = rationale
        msg = (
            f"Third-party package {package!r} attempted to auto-download a "
            f"model via {function_path}. BPP blocks this because it bypasses "
            f"the registry-coordinated click-through gate. "
        )
        if rationale:
            msg += rationale + " "
        msg += (
            "If you need this model, register it in BPP's model registry and "
            "use BPP's own download flow, or provide the file via "
            "Bring-Your-Own-Model. See pm-face-embedder-spike.md, Batch 3."
        )
        super().__init__(msg)


@dataclass(frozen=True)
class _AutoDownloaderEntry:
    """One third-party function we patch.

    ``submodule`` — dotted module path that owns the function.
    ``function_name`` — attribute on the module to replace.
    ``package`` — top-level package name (for user-facing messages).
    ``rationale`` — one-line context shown in the exception message.
    """

    submodule: str
    function_name: str
    package: str
    rationale: str


#: Third-party functions we know about. New entries land here when a
#: new package with auto-download behaviour gets added as a transitive
#: dep. Every entry is exercised by the test suite to catch silent
#: rename/refactor on the upstream side.
KNOWN_AUTO_DOWNLOADERS: tuple[_AutoDownloaderEntry, ...] = (
    _AutoDownloaderEntry(
        submodule="insightface.utils.storage",
        function_name="download",
        package="insightface",
        rationale=(
            "InsightFace's `storage.download()` fetches buffalo_* model "
            "bundles on first use. Those bundles are research/non-commercial "
            "per upstream terms."
        ),
    ),
    _AutoDownloaderEntry(
        submodule="insightface.utils.storage",
        function_name="download_file",
        package="insightface",
        rationale=(
            "Lower-level downloader inside `storage` — patched alongside "
            "`download` because some InsightFace versions call this directly."
        ),
    ),
    _AutoDownloaderEntry(
        submodule="insightface.utils.storage",
        function_name="ensure_available",
        package="insightface",
        rationale=(
            "Convenience helper that wraps `download` + cache-path lookup. "
            "Patched so a caller that bypasses `download` directly still "
            "hits the block."
        ),
    ),
)


# ── State ──

#: Tracks every (submodule, function_name) we have already patched, so
#: re-importing :mod:`bpp.registry` is a no-op rather than wrapping a
#: wrapper.
_patched: set[tuple[str, str]] = set()

#: A list of (submodule, function_name, original_callable) capturing
#: the pre-patch references so tests (and a possible future
#: ``uninstall``) can roll back without guessing.
_originals: list[tuple[str, str, Callable[..., Any]]] = []

#: Per-thread flag that BPP's own download flow (Batch 4) sets while
#: it is in the middle of executing a registry-coordinated download.
#: When set, :func:`_make_blocked_downloader` returns silently rather
#: than raising — that is how the click-through dialog will eventually
#: be allowed to drive the underlying upstream download once the user
#: has explicitly accepted the terms.
_bypass_for_registry = threading.local()


def _is_bypass_active() -> bool:
    return bool(getattr(_bypass_for_registry, "active", False))


# ── Patching machinery ──


def _make_blocked_downloader(entry: _AutoDownloaderEntry) -> Callable[..., Any]:
    """Build the replacement function for one downloader."""

    def blocked(*args: Any, **kwargs: Any) -> Any:
        if _is_bypass_active():
            # Batch 4 will drive registry-coordinated downloads through
            # this branch by setting the per-thread bypass. The actual
            # download then runs via the original function which we
            # call back into.
            original = _get_original(entry)
            if original is None:
                raise BlockedAutoDownloadError(
                    package=entry.package,
                    function_path=f"{entry.submodule}.{entry.function_name}",
                    call_args=args,
                    call_kwargs=kwargs,
                    rationale=(
                        "Registry bypass is active but the original "
                        "function reference was lost. This is a bug in "
                        "the chokepoint plumbing."
                    ),
                )
            return original(*args, **kwargs)
        raise BlockedAutoDownloadError(
            package=entry.package,
            function_path=f"{entry.submodule}.{entry.function_name}",
            call_args=args,
            call_kwargs=kwargs,
            rationale=entry.rationale,
        )

    # Stamp metadata onto the replacement so introspection (tests,
    # debuggers, the meta-path hook) can identify it without guessing.
    blocked.__bpp_chokepoint__ = True  # type: ignore[attr-defined]
    blocked.__bpp_entry__ = entry  # type: ignore[attr-defined]
    blocked.__wrapped__ = None  # type: ignore[attr-defined]
    return blocked


def _get_original(entry: _AutoDownloaderEntry) -> Callable[..., Any] | None:
    for sub, name, orig in _originals:
        if sub == entry.submodule and name == entry.function_name:
            return orig
    return None


def _patch_loaded_module(module: ModuleType, entry: _AutoDownloaderEntry) -> bool:
    """Replace ``entry.function_name`` on ``module`` with the blocker.

    Returns ``True`` if the patch was applied (or was already in place),
    ``False`` if the expected attribute could not be found. The
    enforce-chokepoint tripwire treats a False here as a hard failure.
    """
    key = (entry.submodule, entry.function_name)
    if key in _patched:
        return True
    original = getattr(module, entry.function_name, None)
    if original is None:
        _log.warning(
            "Chokepoint: %s.%s missing — upstream may have renamed it. "
            "Add a new KNOWN_AUTO_DOWNLOADERS entry that targets the new "
            "name and remove this one.",
            entry.submodule,
            entry.function_name,
        )
        return False
    if getattr(original, "__bpp_chokepoint__", False):
        # Already our blocker (e.g. module reload).
        _patched.add(key)
        return True
    blocker = _make_blocked_downloader(entry)
    setattr(module, entry.function_name, blocker)
    _originals.append((entry.submodule, entry.function_name, original))
    _patched.add(key)
    _log.info(
        "Chokepoint: patched %s.%s to block auto-download (%s)",
        entry.submodule,
        entry.function_name,
        entry.rationale,
    )
    return True


# ── Meta-path hook (covers packages loaded after bpp.registry) ──


class _ChokepointPostImportFinder(importlib.abc.MetaPathFinder):
    """Meta-path finder that patches known auto-downloaders after they
    finish loading.

    It does not interfere with normal import resolution. ``find_spec``
    returns ``None`` for every query (meaning "I have no opinion, ask
    the next finder"), but uses the call as a tripwire to check
    whether the target submodule landed in ``sys.modules`` since the
    last sweep, applying the patch when it did.
    """

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        # Every import goes through every meta-path finder. Use the
        # invocation as our cue to check sys.modules for any known
        # auto-downloader whose submodule is now loaded but not yet
        # patched. The pattern matches at the package-name level so an
        # import of `insightface.app` triggers a check for
        # `insightface.utils.download` too.
        for entry in KNOWN_AUTO_DOWNLOADERS:
            key = (entry.submodule, entry.function_name)
            if key in _patched:
                continue
            module = sys.modules.get(entry.submodule)
            if module is not None:
                _patch_loaded_module(module, entry)
        return None  # Defer to other finders for actual resolution.


_finder_installed = False


def install_third_party_interceptions() -> None:
    """Patch every importable known auto-downloader and install the
    post-import hook for the not-yet-imported ones.

    Idempotent: re-imports of :mod:`bpp.registry` re-enter here and do
    not produce double-wrapped functions.
    """
    global _finder_installed
    # Patch packages already loaded.
    for entry in KNOWN_AUTO_DOWNLOADERS:
        module = sys.modules.get(entry.submodule)
        if module is not None:
            _patch_loaded_module(module, entry)
            continue
        try:
            module = importlib.import_module(entry.submodule)
        except ImportError:
            # Not installed in this environment — nothing to patch.
            continue
        _patch_loaded_module(module, entry)
    # Install the meta-path tripwire if not already there. It is
    # inserted at position 0 so it runs before the standard finders;
    # since it never returns a spec, this only matters for the
    # sys.modules sweep timing.
    if not _finder_installed:
        sys.meta_path.insert(0, _ChokepointPostImportFinder())
        _finder_installed = True


# ── Runtime tripwire used by pipeline entry points ──


def enforce_chokepoint() -> None:
    """Fail closed if any expected patch is missing.

    Pipeline entry points (face extraction, semantic dedup, anywhere
    that might trigger a model load) call this before doing any work.
    The check is cheap (a set lookup per known entry) and catches
    silent-leak risk windows that would otherwise go undetected — e.g.
    a contributor edits ``bpp/__init__.py`` and forgets to wire
    :mod:`bpp.registry`, leaving the chokepoint uninstalled.

    Raises :class:`BlockedAutoDownloadError` (rather than a softer
    exception) on the first missing entry, with the rationale showing
    the user how to fix the installation.
    """
    for entry in KNOWN_AUTO_DOWNLOADERS:
        key = (entry.submodule, entry.function_name)
        if key in _patched:
            continue
        module = sys.modules.get(entry.submodule)
        if module is None:
            # Package not loaded → nothing to patch yet. The post-import
            # hook will catch it later. This is the legitimate path
            # taken in environments that don't have insightface
            # installed at all.
            continue
        # Module loaded but patch missing → fail closed.
        raise BlockedAutoDownloadError(
            package=entry.package,
            function_path=f"{entry.submodule}.{entry.function_name}",
            rationale=(
                "Module is loaded but the chokepoint patch is missing. "
                "Most likely cause: bpp.registry was not imported before "
                "this entry point ran, or upstream renamed the function "
                "and the KNOWN_AUTO_DOWNLOADERS entry is now stale."
            ),
        )


# ── Bypass for BPP's own download flow (Batch 4 placeholder) ──


def enter_registry_download() -> None:
    """Open the per-thread bypass window so a Batch-4 click-through-
    driven download can pass through the underlying upstream function
    without raising. MUST be paired with :func:`exit_registry_download`.

    The function is a placeholder until Batch 4 lands the actual
    click-through dialog. Tests can drive it directly to confirm the
    bypass mechanism works.
    """
    _bypass_for_registry.active = True


def exit_registry_download() -> None:
    """Close the per-thread bypass window opened by
    :func:`enter_registry_download`."""
    _bypass_for_registry.active = False


# ── Test-only helpers ──


def _reset_chokepoint_for_tests() -> None:
    """Roll back every patch we have installed and forget the
    sys.meta_path hook.

    Restores the original function on each previously-patched module
    and clears the internal bookkeeping. Tests use this to start from
    a known-clean state without restarting the interpreter.
    """
    global _finder_installed
    for sub, name, original in _originals:
        module = sys.modules.get(sub)
        if module is not None:
            setattr(module, name, original)
    _originals.clear()
    _patched.clear()
    # Drop our finder from sys.meta_path if present.
    sys.meta_path[:] = [f for f in sys.meta_path if not isinstance(f, _ChokepointPostImportFinder)]
    _finder_installed = False
    _bypass_for_registry.active = False
