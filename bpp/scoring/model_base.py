"""Thread-safe lazy-loading singleton for ML models.

Eliminates 40-60 lines of boilerplate per model:
- Global variables (_session, _lock, _available)
- Double-checked locking
- Model file download with atomic tmp+rename
- Availability check (import + file)

Usage::

    _clip_visual = ModelSingleton(
        name="CLIP visual",
        model_path=Path.home() / ".cache/bpp/clip-vit-b-32-visual.onnx",
        model_url="https://...",
        create_fn=lambda path: ort.InferenceSession(str(path), ...),
        registry_id="openai_clip_vit_b32_onnx",  # legal-registry entry
        import_check=lambda: __import__("onnxruntime"),
    )

    session = _clip_visual.get()  # Thread-safe, lazy download + init
    if _clip_visual.is_available(): ...
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bpp.errors import IntegrityError as _IntegrityError
from bpp.utils.logging import get_logger

log = get_logger(__name__)


class ModelIntegrityError(_IntegrityError):
    """Raised when a model's bytes fail integrity verification.

    Distinct from the broad "model unavailable" path that
    `ModelSingleton.get()` quietly catches: an integrity failure is a
    *loud* condition that must surface to the operator. A tampered or
    corrupt model file silently degrading to "feature unavailable"
    would defeat the supply-chain protection (the user would just see
    an empty score and assume the photo is safe).

    Subclassed handlers (e.g. NudeNet's bundled-wheel SHA verifier)
    raise this; ModelSingleton.get() lets it propagate by listing
    the type in `fatal_exceptions`. Callers handle it as they would
    any other RuntimeError — typically by aborting the operation
    instead of returning a "best guess" score.
    """


class ModelSingleton:
    """Thread-safe lazy-loading singleton for a single ML model.

    Parameters
    ----------
    name:
        Human-readable name used in log messages.
    model_path:
        Path where the model file is stored (or will be downloaded to).
        Pass ``None`` when the library manages its own model (e.g. NudeNet).
    model_url:
        URL to download the model from.  Pass ``None`` to skip download
        (library-managed models or bundled-only models).
    create_fn:
        ``callable(path: Path) -> model``.  Called exactly once; the result
        is cached.  Receives the resolved *model_path* (or ``None`` when
        *model_path* is ``None``).
    import_check:
        Optional zero-argument callable.  If provided, it is called before
        the model is initialised.  Should raise ``ImportError`` if the
        required package is missing.
    bundled_path:
        Optional path to a bundled copy of the model (ships with the
        package).  When the model is missing from *model_path*, the bundled
        copy is tried before downloading.
    model_sha256:
        Optional SHA-256 of the expected downloaded bytes.  When set,
        the post-download hash is verified and a mismatch deletes the
        file + raises.  Defends against host compromise / MITM /
        upstream tampering — every model we run does ONNX or native
        inference, so unverified bytes are RCE on import.

        Bundled-fallback bytes are NOT verified — they ship in our wheel
        and are part of the trusted build.
    """

    def __init__(
        self,
        name: str,
        model_path: Path | str | None,
        model_url: str | None,
        create_fn: Callable[[Path | None], Any],
        *,
        registry_id: str | None,
        import_check: Callable[[], None] | None = None,
        bundled_path: str | None = None,
        model_sha256: str | None = None,
        fatal_exceptions: tuple[type[BaseException], ...] = (ModelIntegrityError,),
    ) -> None:
        self.name = name
        self.model_path: Path | None = Path(model_path) if model_path is not None else None
        self.model_url = model_url
        self.create_fn = create_fn
        # Keyword-only, required: the legal-registry id (or None for an
        # ancillary detector with no licensing concern). Passed to
        # bpp.utils.download.download_file so the policy gate fires
        # before the network call. See bpp/utils/download.py docstring.
        self.registry_id = registry_id
        self.import_check = import_check
        self.bundled_path: str | None = bundled_path
        self.model_sha256: str | None = model_sha256
        # Exception types that MUST propagate out of `get()` instead of
        # being caught and degraded to "model unavailable". Default is
        # ModelIntegrityError because a tampered/corrupt model bytes
        # silently scoring as "feature unavailable" would defeat the
        # SHA verification we ship for supply-chain defense. Operators
        # adding new model entries can extend this tuple if their
        # create_fn raises a similarly-loud condition.
        self.fatal_exceptions = fatal_exceptions

        self._instance: Any = None
        self._lock = threading.Lock()
        self._available: bool | None = None  # tri-state: None=unknown, True=yes, False=no

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self) -> Any:
        """Return the cached model instance, initialising it on first call.

        Uses double-checked locking so that concurrent threads do not race
        to initialise the model.  Returns ``None`` if the model is unavailable.

        On double-checked locking (DCL) safety in Python:
            DCL is famously broken in C++/Java without explicit memory
            barriers because compilers can reorder writes such that
            another thread sees a partially-constructed object through
            the unsynchronized first check. CPython does NOT have this
            problem for `self._instance`/`self._available` reads:

            1. The GIL serializes bytecode execution. The single STORE
               that publishes the instance (`self._instance = inst`) is
               atomic with respect to any other thread's LOAD of the
               same attribute. There is no "half-published" state.
            2. The second check inside the lock guards against the
               benign "two threads both saw None, second one waits at
               the lock, checks again, finds the instance the first
               built." Without the second check we'd build the model
               twice.
            3. PEP 583 / the language model guarantees that ordinary
               attribute assignment is a single bytecode op.

            If/when CPython moves to no-GIL (PEP 703), this pattern
            stays correct as long as `self._instance` is only ever
            written under the lock — which it is. The first check is
            an unsynchronized fast path, not a correctness path.
        """
        if self._available is False:
            return None
        if self._instance is not None:
            return self._instance

        with self._lock:
            if self._available is False:
                return None
            if self._instance is not None:
                return self._instance

            try:
                # Optional import check (raises ImportError if dep missing)
                if self.import_check is not None:
                    self.import_check()

                # Ensure model file is present
                path = self.ensure_model()
                if path is None and self.model_path is not None:
                    # Download / copy failed
                    self._available = False
                    return None

                self._instance = self.create_fn(path)
                self._available = True
                log.info("%s initialised", self.name)
            except self.fatal_exceptions:
                # Loud failures (integrity mismatch, etc.) propagate
                # so callers must handle them. Don't mark unavailable
                # — a retry shouldn't be possible since the bytes
                # themselves are wrong.
                log.error(
                    "%s init aborted: integrity failure",
                    self.name,
                    exc_info=True,
                )
                raise
            except (ImportError, Exception) as exc:
                log.debug("%s unavailable: %s", self.name, exc)
                self._available = False
                return None

        return self._instance

    def is_available(self) -> bool:
        """Return ``True`` if the model can be loaded.

        Performs a lightweight check:
        - If ``import_check`` is set, verifies the dependency is importable.
        - If ``model_path`` is set, verifies the file exists OR ``model_url``
          is set (meaning it can be downloaded on demand).

        Does **not** actually load the model.
        """
        if self._available is not None:
            return self._available

        # Check optional import
        if self.import_check is not None:
            try:
                self.import_check()
            except ImportError:
                return False

        # Check model file
        if self.model_path is not None:
            file_ready = (
                self.model_path.exists()
                or self.model_url is not None
                or (self.bundled_path is not None and os.path.exists(self.bundled_path))
            )
            if not file_ready:
                return False

        return True

    def ensure_model(self) -> Path | None:
        """Ensure the model file is present, downloading it if needed.

        Steps:
        1. Return immediately if *model_path* is ``None`` (library-managed).
        2. Return *model_path* if it already exists.
        3. Copy from *bundled_path* if it exists.
        4. Download from *model_url* using ``download_file()`` with
           atomic tmp+rename.

        Returns the resolved :class:`~pathlib.Path`, or ``None`` on failure.
        """
        if self.model_path is None:
            # Library manages its own model (e.g. NudeNet)
            return None

        if self.model_path.exists():
            # D-02: verify cached files against the pinned SHA before
            # returning them. The download path already verifies on
            # first fetch, but a cached file could have been replaced
            # between sessions (compromised cache, cloud-sync overwrite,
            # malicious bind mount in Docker, etc.). Without this
            # check, post-download tampering would silently load
            # unverified bytes into ONNX/native inference. Empty
            # `model_sha256` means "unverifiable" (some legacy callers)
            # — accept the file as-is rather than refuse to load.
            if self.model_sha256:
                from bpp.utils.download import verify_existing

                # Lets ModelIntegrityError propagate through get() —
                # listed in fatal_exceptions by default.
                verify_existing(str(self.model_path), sha256=self.model_sha256)
            return self.model_path

        # Try bundled copy first (no network needed)
        if self.bundled_path is not None and os.path.exists(self.bundled_path):
            import shutil

            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.bundled_path, self.model_path)
            log.info("Installed bundled %s model to %s", self.name, self.model_path)
            return self.model_path

        # Download from URL
        if self.model_url is None:
            log.warning("%s model missing and no download URL configured", self.name)
            return None

        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        log.info("Downloading %s to %s ...", self.name, self.model_path)

        import time

        from bpp.utils.download import download_file

        tmp_path = self.model_path.with_suffix(self.model_path.suffix + ".tmp")
        _max_attempts = 3
        for attempt in range(1, _max_attempts + 1):
            try:
                download_file(
                    self.model_url,
                    str(tmp_path),
                    registry_id=self.registry_id,
                    sha256=self.model_sha256,
                )
                os.replace(tmp_path, self.model_path)
                size_mb = self.model_path.stat().st_size // (1024 * 1024)
                log.info("%s downloaded (%d MB)", self.name, size_mb)
                return self.model_path
            except ModelIntegrityError:
                # SHA mismatch is unrecoverable — propagate immediately so
                # the caller hears about tampering/corruption without retry.
                if tmp_path.exists():
                    tmp_path.unlink()
                log.error("Integrity failure downloading %s from %s", self.name, self.model_url)
                raise
            except Exception as exc:
                if tmp_path.exists():
                    tmp_path.unlink()
                if attempt < _max_attempts:
                    delay = 2**attempt
                    log.warning(
                        "Failed to download %s (attempt %d/%d): %s — retrying in %ds",
                        self.name,
                        attempt,
                        _max_attempts,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    log.error(
                        "Failed to download %s after %d attempts: %s",
                        self.name,
                        _max_attempts,
                        exc,
                    )
        return None

    def reset(self) -> None:
        """Clear the cached model instance (e.g. for model re-download).

        The next call to :meth:`get` will re-initialise from disk.
        """
        with self._lock:
            self._instance = None
            self._available = None


# ── ModelRegistry ──────────────────────────────────────────────────
#
# Centralizes the (path, url, sha256, reset) lookup for every ML
# model the user can redownload / uninstall via Settings → Advanced →
# ML Models.
#
# Pattern parallels `WorkerRegistry` (workers) and `SmartAlbumRegistry`
# (smart album types). Each scoring module appends its registration(s)
# at module load — see `bpp/scoring/face.py` for a typical example.
#
# `name` is the user-facing string the JS sends as the redownload /
# uninstall key — it MUST match the corresponding `_file_info(name=...)`
# call in `bpp/web/models_status.py`. Tests in
# `tests/test_model_registry.py` enforce that.


from dataclasses import dataclass, field  # noqa: E402
from typing import ClassVar  # noqa: E402


@dataclass(frozen=True)
class ModelEntry:
    """One row in the ModelRegistry.

    `path` is the on-disk model file path (already resolved at
    registration time — env-var paths like BPP_MODELS_DIR are read
    when the scoring module is imported, which is at app boot).

    `url` is None for bundled-in-wheel models (today: NudeNet).
    `sha256` is None when no integrity check is configured (the
    test suite warns on this).

    `reset` is a zero-arg callable that clears whatever cache
    layer holds the live model so a redownload / uninstall is
    visible on next `.get()`. ModelSingleton-backed models point
    to `singleton.reset`; modules that use thread-local + module-
    global negative caches (YuNet, SFace) supply a closure that
    clears the right global.

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


class ModelRegistry:
    """Mutable registry of ML model lifecycle entries.

    Re-registering the same `ModelEntry` for an existing name is a
    no-op (idempotent on module re-import). Different entry for the
    same name raises — pass `replace=True` for tests.
    """

    _entries: ClassVar[dict[str, ModelEntry]] = {}

    @classmethod
    def register(cls, entry: ModelEntry, *, replace: bool = False) -> None:
        existing = cls._entries.get(entry.name)
        if existing is not None and existing != entry and not replace:
            raise ValueError(
                f"ModelRegistry: {entry.name!r} already registered with a "
                "different entry (pass replace=True if intentional)"
            )
        cls._entries[entry.name] = entry

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
