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
        # Through the install hold: never imported while (or after) the
        # in-app installer rewrites it, until the restart.
        import_check=install_hold.requires("onnxruntime"),
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
from bpp.scoring.install_hold import HeldForRestart
from bpp.scoring.model_load_gate import MemoizedLoadGate
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

    SHA verifiers (``bpp.utils.download.verify_existing`` /
    ``download_file``) raise this; ModelSingleton.get() lets it propagate by listing
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
        Pass ``None`` when the module manages the file itself (e.g. the
        NudeNet runner, whose ``create_fn`` fetches + verifies it).
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

        A copy installed from *bundled_path* is verified against it too.
    """

    def __init__(
        self,
        name: str,
        model_path: Path | str | None,
        model_url: str | None,
        create_fn: Callable[[Path | None], Any],
        *,
        registry_id: str,
        import_check: Callable[[], None] | None = None,
        bundled_path: str | None = None,
        model_sha256: str | None = None,
        fatal_exceptions: tuple[type[BaseException], ...] = (ModelIntegrityError,),
    ) -> None:
        self.name = name
        self.model_path: Path | None = Path(model_path) if model_path is not None else None
        self.model_url = model_url
        self.create_fn = create_fn
        # Keyword-only, required: the bpp.registry entry for these
        # weights. It is the only place the model's license is written;
        # get() refuses to load the model until the entry's license is
        # accepted (when it needs acceptance), and download_file runs
        # the same gate before any network call.
        if not registry_id:
            raise ValueError(
                f"ModelSingleton {name!r}: registry_id is required. Every "
                "model needs a bpp.registry entry that records its license."
            )
        self.registry_id = registry_id
        self._gate = MemoizedLoadGate(registry_id)
        self._blocked_logged = False
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
        self._download_lock = threading.Lock()
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
        if not self._license_allows_load():
            return None
        return self._get_loaded()

    def get_or_raise(self) -> Any:
        """Like :meth:`get`, but a license refusal raises
        :class:`~bpp.registry.policy.ModelLoadBlockedError` instead of
        returning ``None``. The license is checked once per call (cached
        until the acceptance files change), so it suits per-photo paths."""
        if self._available is False:
            return None
        self._gate.enforce()
        return self._get_loaded()

    def _get_loaded(self) -> Any:
        """The model instance, loading it on first use (license already
        checked by the caller)."""
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
            except HeldForRestart as exc:
                # Installed from the app: loads after a restart (or at once,
                # if the hold is lifted) — not a verdict to cache.
                log.debug("%s waits for a restart: %s", self.name, exc)
                return None
            except (ImportError, Exception) as exc:
                log.debug("%s unavailable: %s", self.name, exc)
                self._available = False
                return None

        return self._instance

    def _license_allows_load(self) -> bool:
        """Run the registry's load gate. Its result (pass or block) is
        cached only until the acceptance files change, so an accept or a
        revoke takes effect on the next call."""
        from bpp.registry.policy import ModelLoadBlockedError

        try:
            self._gate.enforce()
        except ModelLoadBlockedError as exc:
            if not self._blocked_logged:
                self._blocked_logged = True
                log.info("%s is off until its license is accepted: %s", self.name, exc)
            return False
        return True

    def enforce_license(self) -> None:
        """Raise :class:`~bpp.registry.policy.ModelLoadBlockedError` unless
        the registry entry's license allows loading. The decision is
        cached until the acceptance files change (each call still stats
        those two files)."""
        self._gate.enforce()

    def license_blocked(self) -> bool:
        """True while the registry entry's license isn't accepted (so a
        missing file is "off until accepted", not a download failure)."""
        return not self._license_allows_load()

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

        Returns the resolved :class:`~pathlib.Path`, or ``None`` on failure
        (:meth:`ensure_model_or_raise` says why).
        """
        return self._ensure_serialized()[0]

    def ensure_model_or_raise(self) -> Path | None:
        """Like :meth:`ensure_model`, but a failure raises ``RuntimeError``
        carrying just the reason in plain words (callers add their own
        "Couldn't download …")."""
        path, reason = self._ensure_serialized()
        if path is None and self.model_path is not None:
            raise RuntimeError(reason or "unknown error")
        return path

    def _ensure_serialized(self) -> tuple[Path | None, str | None]:
        # One download at a time per model: concurrent callers wait and
        # then find the file present, instead of fetching it again.
        with self._download_lock:
            return self._ensure_locked()

    def _ensure_locked(self) -> tuple[Path | None, str | None]:
        """Steps:

        1. Nothing to do when *model_path* is ``None`` (library-managed).
        2. A cached file is verified against the pinned SHA; a file that
           fails is deleted and fetched again once (below).
        3. Copy from *bundled_path* if it exists.
        4. Download from *model_url* via ``download_file()`` to a
           per-writer temp file, then rename into place.

        Returns ``(path, None)`` or ``(None, reason)``.
        """
        if self.model_path is None:
            # create_fn manages the file itself
            return None, None

        if self.model_path.exists():
            # Verify cached files against the pinned SHA before
            # returning them. The download path already verifies on
            # first fetch, but a cached file could have been replaced
            # or truncated between sessions (compromised cache,
            # cloud-sync overwrite, a crash mid-copy). Empty
            # `model_sha256` means "unverifiable" — accept as-is.
            if not self.model_sha256:
                return self.model_path, None
            from bpp.utils.download import verify_existing

            try:
                verify_existing(str(self.model_path), sha256=self.model_sha256)
                return self.model_path, None
            except ModelIntegrityError:
                # verify_existing deleted the bad file. With nowhere to
                # get good bytes from, stay loud (get() propagates it).
                if self.model_url is None and not self._bundled_exists():
                    raise
                log.warning("Cached %s model fails its integrity check; fetching again", self.name)
                repairing = True
        else:
            repairing = False

        # Try bundled copy first (no network needed)
        if self._bundled_exists():
            import shutil

            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(self.bundled_path), self.model_path)
            if self.model_sha256:
                # Checked like any other copy: a bundle that doesn't match
                # its pin would otherwise load (and be "repaired" from
                # itself) forever.
                from bpp.utils.download import verify_existing

                verify_existing(str(self.model_path), sha256=self.model_sha256)
            log.info("Installed bundled %s model to %s", self.name, self.model_path)
            return self.model_path, None

        # Download from URL
        if self.model_url is None:
            log.warning("%s model missing and no download URL configured", self.name)
            return None, "it has no download source"
        # Never fetch weights whose license hasn't been accepted (and
        # don't burn the retry loop on a refusal that can't change).
        if not self._license_allows_load():
            return None, "its license isn't accepted (Settings → Models)"
        return self._download(repairing)

    def _bundled_exists(self) -> bool:
        return self.bundled_path is not None and os.path.exists(self.bundled_path)

    def _download(self, repairing: bool) -> tuple[Path | None, str | None]:
        """Fetch the weights; the retry loop lives in ``model_download``."""
        from bpp.scoring.model_download import download_model

        return download_model(self, repairing)

    def remove_files(self) -> int:
        """Delete the model file and any partial downloads; returns the
        bytes freed. Waits for a download in progress, so its retry
        can't put the model back after the user removed it."""
        from bpp.utils.download import remove_partial_downloads

        freed = 0
        with self._download_lock:
            if self.model_path is not None:
                if self.model_path.exists():
                    size = self.model_path.stat().st_size
                    self.model_path.unlink()  # OSError propagates: not freed
                    freed = size
                remove_partial_downloads(str(self.model_path))
        self.reset()
        return freed

    @property
    def download_lock(self) -> threading.Lock:
        """Held while this model's file is fetched (for removers that
        manage the file themselves)."""
        return self._download_lock

    def reset(self) -> None:
        """Clear the cached model instance (e.g. for model re-download).

        The next call to :meth:`get` will re-initialise from disk.
        """
        with self._lock:
            self._instance = None
            self._available = None
            self._blocked_logged = False
        # Re-check the license on next use (a revoke or re-download).
        self._gate.reset()


# The file-lifecycle registry lives in bpp.scoring.model_lifecycle;
# re-exported here so existing ``from bpp.scoring.model_base import
# ModelEntry, ModelRegistry`` imports keep working.
from bpp.scoring.model_lifecycle import (  # noqa: E402, F401
    ModelEntry,
    ModelRegistry,
    declared_legal_id,
    plugin_model_entries,
)
