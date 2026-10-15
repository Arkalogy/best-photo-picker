"""File-lifecycle registry for every ML model file BPP can download.

Split out of :mod:`bpp.scoring.model_base` for the 500-LOC cap (that
module re-exports these names). Each scoring module — and each plugin —
registers its files here; Settings → Models, the redownload / uninstall
endpoints and the pre-analysis download list all read it.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar

# `name` is the user-facing string the JS sends as the redownload /
# uninstall key. Built-in files appear in Settings → Models through
# their hand-written rows in `bpp/web/models_status.py` (which must use
# the same name — tests/test_model_registry.py checks it); plugin files
# get an automatic "Plugin model: <name>" row.


@dataclass(frozen=True)
class ModelEntry:
    """One row in the ModelRegistry.

    `path` is the on-disk model file path (already resolved at
    registration time — env-var paths like BPP_MODELS_DIR are read
    when the scoring module is imported, which is at app boot).

    `url` is None for models a library manages itself.
    `sha256` is None when no integrity check is configured (the
    test suite warns on this).

    `reset` is a zero-arg callable that clears whatever cache
    layer holds the live model so a redownload / uninstall is
    visible on next `.get()`. ModelSingleton-backed models point
    to `singleton.reset`; modules that use thread-local + module-
    global negative caches (YuNet, SFace) supply a closure that
    clears the right global.

    `legal_id` names the ``bpp.registry`` entry whose license gates
    this file. ModelSingleton-backed files may leave it unset — their
    singleton's ``registry_id`` is used — but every file has one.

    `reset` is excluded from `__eq__` because re-importing a
    scoring module under test (importlib.reload) creates a new
    closure object pointing at the new singleton — equality on
    the bound-method would falsely say "different entry" and
    refuse the re-registration. Path/url/sha are stable identity.
    """

    name: str
    path: str
    url: str | None
    sha256: str | None
    reset: Callable[[], None] = field(compare=False)
    #: ``bpp.registry`` legal-entry id this file belongs to. ``None``
    #: only when the file's ModelSingleton supplies it; the redownload
    #: endpoint refuses a file that declares none (see
    #: ``bpp.web.bp_models._resolve_legal_id``).
    legal_id: str | None = None
    #: The ModelSingleton that downloads this file, when ``reset`` isn't
    #: its bound method (uninstall waits on its download lock).
    owner: object | None = field(default=None, compare=False)


class ModelRegistry:
    """Mutable registry of ML model lifecycle entries.

    Re-registering the same `ModelEntry` for an existing name is a
    no-op (idempotent on module re-import). Different entry for the
    same name raises — pass `replace=True` for tests.

    Origin comes from who registers: a call from a module inside the
    ``bpp`` package registers a built-in file; a call from anywhere else
    (a plugin's import, ``setup()`` or any later hook) a plugin file.
    ``builtin=`` overrides it (tests). A plugin can't take over a
    built-in name, even with ``replace=True``.
    """

    _entries: ClassVar[dict[str, ModelEntry]] = {}
    #: Names registered by plugins — listed under "Plugin model: …".
    _plugin: ClassVar[set[str]] = set()

    @classmethod
    def register(
        cls, entry: ModelEntry, *, replace: bool = False, builtin: bool | None = None
    ) -> None:
        if builtin is None:
            caller = sys._getframe(1).f_globals.get("__name__", "")
            builtin = caller == "bpp" or caller.startswith("bpp.")
        if not builtin and entry.name not in cls._entries:
            # A built-in file whose module hasn't loaded yet would be
            # claimable — load them all first so the name check is complete.
            ensure_builtin_model_files_registered()
        existing = cls._entries.get(entry.name)
        if not builtin and existing is not None and entry.name not in cls._plugin:
            raise ValueError(
                f"ModelRegistry: {entry.name!r} is a built-in model file; a plugin "
                "must register its files under its own names."
            )
        if existing is not None and existing != entry and not replace:
            raise ValueError(
                f"ModelRegistry: {entry.name!r} already registered with a "
                "different entry (pass replace=True if intentional)"
            )
        cls._entries[entry.name] = entry
        if builtin:
            cls._plugin.discard(entry.name)
        else:
            cls._plugin.add(entry.name)

    @classmethod
    def get(cls, name: str) -> ModelEntry | None:
        return cls._entries.get(name)

    @classmethod
    def all(cls) -> list[ModelEntry]:
        return list(cls._entries.values())

    @classmethod
    def names(cls) -> list[str]:
        return list(cls._entries.keys())

    @classmethod
    def _reset_for_tests(cls) -> None:
        cls._entries.clear()
        cls._plugin.clear()


def declared_legal_id(entry: ModelEntry | None) -> str | None:
    """The legal-registry id a registered file belongs to.

    ``ModelEntry.legal_id`` when set; otherwise, for ModelSingleton-
    backed entries, the singleton's ``registry_id`` (read off the bound
    ``reset`` method) — so an entry that forgets ``legal_id`` is still
    recognised as licensed.
    """
    if entry is None:
        return None
    if getattr(entry, "legal_id", None):
        return entry.legal_id
    owner = getattr(entry.reset, "__self__", None)
    rid = getattr(owner, "registry_id", None)
    return rid if isinstance(rid, str) and rid else None


def plugin_model_entries() -> list[ModelEntry]:
    """Registered model files that came from plugins."""
    return [e for e in ModelRegistry.all() if e.name in ModelRegistry._plugin]


def bundled_path_of(entry: ModelEntry) -> str | None:
    """The copy shipped with the code, if the file's ModelSingleton has
    one — such a file is installed without any download."""
    owner = getattr(entry.reset, "__self__", None)
    path = getattr(owner, "bundled_path", None)
    return path if isinstance(path, str) and path else None


def file_present(path: str, bundled_path: str | None) -> bool:
    """Installed: in the cache, or a shipped copy exists (installed on
    first use with no download). The one rule for "present" used by the
    download list and Settings → Models."""
    import os

    p = os.path.expanduser(path)
    return os.path.exists(p) or bool(bundled_path and os.path.exists(bundled_path))


def fetchable_legal_id(entry: ModelEntry) -> str | None:
    """The legal-registry id when bpp itself can download this file, else
    ``None``. Needs a URL, a pinned sha256 (no unverified installs) and a
    registered legal entry (every download runs the license gate)."""
    from bpp.registry.model_registry import get_entry

    legal_id = declared_legal_id(entry)
    if not (entry.url and entry.sha256 and legal_id and get_entry(legal_id)):
        return None
    return legal_id


#: Every scoring module that registers a built-in model file at import.
BUILTIN_MODEL_MODULES = (
    "bpp.scoring.face",
    "bpp.scoring.face_blazeface_fr",
    "bpp.scoring.face_embed",
    "bpp.scoring.face_expression",
    "bpp.scoring.face_hand_filter",
    "bpp.scoring.face_scrfd",
    "bpp.scoring.nudity",
    "bpp.scoring.pose",
    "bpp.scoring.segmentation",
    "bpp.scoring.pets",
    "bpp.scoring.clip_embed",
    "bpp.scoring.clip_tokenizer",
)


def ensure_builtin_model_files_registered() -> None:
    """Import every built-in model module so all built-in file names are
    registered (cheap after the first call)."""
    import importlib

    for mod in BUILTIN_MODEL_MODULES:
        importlib.import_module(mod)
