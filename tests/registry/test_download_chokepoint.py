"""Batch 3 / item 18 — chokepoint that blocks third-party auto-downloads.

The legal-posture rollout specifically called out that the click-through
dialog (Batch 4) is cosmetic if a transitively-imported package
(insightface and friends) can silently fetch a restricted model on
first use behind BPP's back. These tests pin that:

* The exception class :class:`BlockedAutoDownloadError` raises with
  the package + function-path context plaintiffs would want to see.
* :func:`install_third_party_interceptions` patches every known
  auto-downloader whose package is importable.
* :func:`enforce_chokepoint` fails closed (raises) if any expected
  patch is missing when called from a pipeline entry point.
* Late-imported packages (loaded after :mod:`bpp.registry`) still
  get patched via the meta-path post-import hook.
* The per-thread registry-bypass mechanism lets BPP's own
  Batch-4-driven download flow pass through to the underlying
  upstream function without triggering the blocker.

Each test runs against a synthetic "third party" module so the suite
does not depend on insightface being installed in the test
environment. The real insightface case is exercised by the CI test
that imports insightface and asserts the same patches are in place
on the actual upstream package.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from bpp.registry.download_chokepoint import (
    KNOWN_AUTO_DOWNLOADERS,
    BlockedAutoDownloadError,
    _AutoDownloaderEntry,
    _ChokepointPostImportFinder,
    _patch_loaded_module,
    _reset_chokepoint_for_tests,
    enforce_chokepoint,
    enter_registry_download,
    exit_registry_download,
    install_third_party_interceptions,
)


@pytest.fixture(autouse=True)
def _reset_chokepoint() -> Any:
    """Clean chokepoint state between tests so test order does not
    leak patches across assertions.

    Teardown re-installs the production interceptions so subsequent
    test files that go through the face orchestrator (which calls
    :func:`enforce_chokepoint` at the top of every run) don't trip
    over an empty :data:`_patched` set. Without the re-install, the
    orchestrator-driven test files (test_face_extraction_journal,
    test_clustering_stability, etc.) all blew up with
    ``BlockedAutoDownloadError`` after this file ran.
    """
    _reset_chokepoint_for_tests()
    yield
    _reset_chokepoint_for_tests()
    install_third_party_interceptions()


# ── Synthetic third-party module helper ──


def _install_fake_thirdparty(submodule: str, function_name: str) -> tuple[types.ModuleType, Any]:
    """Create a fake third-party module that exposes a callable named
    ``function_name`` and install it under ``sys.modules[submodule]``.

    Returns ``(module, original_callable)`` so the test can later
    invoke the original to confirm it survives patch + restore.
    """

    def original(*args: Any, **kwargs: Any) -> str:
        return f"called with args={args} kwargs={kwargs}"

    module = types.ModuleType(submodule)
    setattr(module, function_name, original)
    sys.modules[submodule] = module
    return module, original


def _uninstall_fake_thirdparty(submodule: str) -> None:
    """Remove a fake third-party module from sys.modules."""
    sys.modules.pop(submodule, None)


# ── BlockedAutoDownloadError shape ──


class TestBlockedAutoDownloadError:
    def test_message_names_the_package_and_function(self) -> None:
        try:
            raise BlockedAutoDownloadError(
                package="insightface",
                function_path="insightface.utils.download.download",
                rationale="research-only weights",
            )
        except BlockedAutoDownloadError as exc:
            msg = str(exc)
            assert "insightface" in msg
            assert "insightface.utils.download.download" in msg
            assert "research-only weights" in msg
            assert "Batch 3" in msg  # points the reader to the plan

    def test_carries_call_args_for_diagnostics(self) -> None:
        try:
            raise BlockedAutoDownloadError(
                package="x",
                function_path="x.y",
                call_args=("model_url",),
                call_kwargs={"dest": "/tmp/x"},
            )
        except BlockedAutoDownloadError as exc:
            assert exc.call_args == ("model_url",)
            assert exc.call_kwargs == {"dest": "/tmp/x"}


# ── Patching an already-loaded module ──


class TestPatchLoadedModule:
    """The Batch-3 install runs ``_patch_loaded_module`` on every
    known auto-downloader whose submodule is already in
    ``sys.modules``. These tests pin the patch shape."""

    _SUB = "tests_fake_pkg_for_chokepoint.downloader"
    _NAME = "download"

    @pytest.fixture(autouse=True)
    def _fake_module(self) -> Any:
        _install_fake_thirdparty(self._SUB, self._NAME)
        yield
        _uninstall_fake_thirdparty(self._SUB)

    def _entry(self) -> _AutoDownloaderEntry:
        return _AutoDownloaderEntry(
            submodule=self._SUB,
            function_name=self._NAME,
            package="tests_fake_pkg_for_chokepoint",
            rationale="synthetic test downloader",
        )

    def test_patched_function_raises_on_call(self) -> None:
        entry = self._entry()
        module = sys.modules[self._SUB]
        applied = _patch_loaded_module(module, entry)
        assert applied is True
        with pytest.raises(BlockedAutoDownloadError) as excinfo:
            module.download("url", dest="/tmp/x")
        msg = str(excinfo.value)
        assert "synthetic test downloader" in msg

    def test_patching_is_idempotent(self) -> None:
        entry = self._entry()
        module = sys.modules[self._SUB]
        first = _patch_loaded_module(module, entry)
        second = _patch_loaded_module(module, entry)
        assert first and second
        # The function reference should be one of our blockers, not
        # wrapped in a second layer.
        download_attr = module.download
        assert getattr(download_attr, "__bpp_chokepoint__", False) is True
        # No nested wrap — wrapped sentinel still points to None.
        assert getattr(download_attr, "__wrapped__", "no") is None

    def test_missing_attr_returns_false_and_does_not_raise(self) -> None:
        entry = _AutoDownloaderEntry(
            submodule=self._SUB,
            function_name="does_not_exist",
            package="tests_fake_pkg_for_chokepoint",
            rationale="rename probe",
        )
        module = sys.modules[self._SUB]
        applied = _patch_loaded_module(module, entry)
        assert applied is False


# ── Late-import coverage via meta-path hook ──


class TestMetaPathHookCoversLateImports:
    """A package loaded *after* bpp.registry was imported still gets
    patched because the meta-path tripwire fires on every subsequent
    import."""

    _SUB = "tests_fake_late_chokepoint_pkg.downloader"
    _NAME = "download"

    def test_late_imported_module_is_patched_by_finder(self, monkeypatch) -> None:
        # Monkey-patch the KNOWN_AUTO_DOWNLOADERS list to include our
        # synthetic entry so the finder catches it.
        original_entries = tuple(KNOWN_AUTO_DOWNLOADERS)
        monkeypatch.setattr(
            "bpp.registry.download_chokepoint.KNOWN_AUTO_DOWNLOADERS",
            (
                *original_entries,
                _AutoDownloaderEntry(
                    submodule=self._SUB,
                    function_name=self._NAME,
                    package="tests_fake_late_chokepoint_pkg",
                    rationale="late-import probe",
                ),
            ),
        )

        # Install the chokepoint with our entry list. The target
        # submodule does NOT yet exist in sys.modules.
        assert self._SUB not in sys.modules
        install_third_party_interceptions()

        # Now load the synthetic module (simulating a third-party
        # package being imported after bpp.registry).
        _install_fake_thirdparty(self._SUB, self._NAME)
        try:
            # Drive the finder directly — this is what Python's import
            # machinery does for every import attempt. The finder's
            # find_spec sweeps sys.modules for unpatched known
            # auto-downloaders before returning None to defer
            # resolution.
            finders = [f for f in sys.meta_path if isinstance(f, _ChokepointPostImportFinder)]
            assert finders, "chokepoint finder not installed"
            finders[0].find_spec("any.module.name", None, None)

            # Now the synthetic module should be patched.
            module = sys.modules[self._SUB]
            assert getattr(module.download, "__bpp_chokepoint__", False) is True
            with pytest.raises(BlockedAutoDownloadError):
                module.download("anything")
        finally:
            _uninstall_fake_thirdparty(self._SUB)


# ── enforce_chokepoint tripwire ──


class TestEnforceChokepoint:
    """Pipeline entry points call enforce_chokepoint before touching
    any model. If a known package is loaded but the patch is missing,
    the call raises — fail closed, no silent-leak window."""

    _SUB = "tests_fake_enforce_pkg.downloader"
    _NAME = "download"

    def test_clean_state_does_not_raise(self, monkeypatch) -> None:
        # Empty the production entries so the test does not depend on
        # whether insightface happens to be importable in the dev env.
        # The contract being pinned is "no unsatisfied entries does not
        # raise" — the contents of the entry list are exercised by
        # other tests.
        monkeypatch.setattr(
            "bpp.registry.download_chokepoint.KNOWN_AUTO_DOWNLOADERS",
            (),
        )
        enforce_chokepoint()  # must not raise

    def test_loaded_but_unpatched_raises(self, monkeypatch) -> None:
        # Add our synthetic entry, then load the module without
        # patching it, then enforce.
        monkeypatch.setattr(
            "bpp.registry.download_chokepoint.KNOWN_AUTO_DOWNLOADERS",
            (
                _AutoDownloaderEntry(
                    submodule=self._SUB,
                    function_name=self._NAME,
                    package="tests_fake_enforce_pkg",
                    rationale="enforce probe",
                ),
            ),
        )
        _install_fake_thirdparty(self._SUB, self._NAME)
        try:
            with pytest.raises(BlockedAutoDownloadError) as excinfo:
                enforce_chokepoint()
            msg = str(excinfo.value)
            assert "tests_fake_enforce_pkg" in msg
        finally:
            _uninstall_fake_thirdparty(self._SUB)

    def test_unloaded_known_package_is_not_an_error(self, monkeypatch) -> None:
        """A known package that is simply not installed in the
        environment must not trigger the tripwire — the production
        case where insightface is genuinely absent should be silent."""
        monkeypatch.setattr(
            "bpp.registry.download_chokepoint.KNOWN_AUTO_DOWNLOADERS",
            (
                _AutoDownloaderEntry(
                    submodule="package_definitely_not_installed.x",
                    function_name="download",
                    package="package_definitely_not_installed",
                    rationale="unloaded probe",
                ),
            ),
        )
        assert "package_definitely_not_installed.x" not in sys.modules
        enforce_chokepoint()  # must not raise


# ── Registry-bypass window for BPP's own download flow ──


class TestRegistryBypass:
    """Batch-4 will drive registry-coordinated downloads by opening
    the per-thread bypass. The blocker checks the bypass flag and
    delegates to the original function when it is active."""

    _SUB = "tests_fake_bypass_pkg.downloader"
    _NAME = "download"

    @pytest.fixture(autouse=True)
    def _seed(self, monkeypatch) -> Any:
        monkeypatch.setattr(
            "bpp.registry.download_chokepoint.KNOWN_AUTO_DOWNLOADERS",
            (
                _AutoDownloaderEntry(
                    submodule=self._SUB,
                    function_name=self._NAME,
                    package="tests_fake_bypass_pkg",
                    rationale="bypass probe",
                ),
            ),
        )
        _install_fake_thirdparty(self._SUB, self._NAME)
        install_third_party_interceptions()
        yield
        _uninstall_fake_thirdparty(self._SUB)

    def test_bypass_lets_call_through_to_original(self) -> None:
        module = sys.modules[self._SUB]
        # Without bypass: raises.
        with pytest.raises(BlockedAutoDownloadError):
            module.download("url")
        # With bypass: returns the original's result.
        enter_registry_download()
        try:
            result = module.download("url", dest="/tmp/x")
        finally:
            exit_registry_download()
        assert "called with" in result

    def test_bypass_does_not_leak_across_threads(self) -> None:
        """The bypass is per-thread. Opening it in this thread must
        not let a concurrent thread's call slip through."""
        import threading

        module = sys.modules[self._SUB]
        enter_registry_download()
        observed: list[bool] = []

        def worker() -> None:
            try:
                module.download("url")
                observed.append(True)
            except BlockedAutoDownloadError:
                observed.append(False)

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        exit_registry_download()
        assert observed == [False], (
            "Bypass leaked across threads — that would let a worker "
            "silently download a restricted model while the main "
            "thread held the bypass open."
        )

    def test_bypass_closes_after_call(self) -> None:
        module = sys.modules[self._SUB]
        enter_registry_download()
        try:
            module.download("url")
        finally:
            exit_registry_download()
        # Subsequent call without bypass must raise again.
        with pytest.raises(BlockedAutoDownloadError):
            module.download("url")


# ── Sanity: the production KNOWN_AUTO_DOWNLOADERS entries cover the
# packages the legal-posture spec named ──


class TestProductionEntries:
    def test_insightface_entries_present(self) -> None:
        modules = {entry.submodule for entry in KNOWN_AUTO_DOWNLOADERS}
        # The actual downloader lives at insightface.utils.storage —
        # the `utils.download` name is just a re-export of the
        # `storage.download` function. The chokepoint patches the
        # canonical location.
        assert "insightface.utils.storage" in modules, (
            "Insightface was the primary upstream named by the legal "
            "review. Removing it from KNOWN_AUTO_DOWNLOADERS defeats "
            "the chokepoint for the model bundles (buffalo_s, "
            "buffalo_l, etc.) most likely to land in BPP."
        )
        # Both download paths inside storage should be patched: the
        # high-level `download` (used by FaceAnalysis.prepare's setup)
        # and the lower-level `download_file` (used by some callers
        # that bypass `download`).
        functions = {(entry.submodule, entry.function_name) for entry in KNOWN_AUTO_DOWNLOADERS}
        assert ("insightface.utils.storage", "download") in functions
        assert ("insightface.utils.storage", "download_file") in functions


# ── Sanity: the finder is installed in sys.meta_path ──


class TestFinderInstallation:
    def test_finder_is_installed_after_install_call(self) -> None:
        install_third_party_interceptions()
        finders = [f for f in sys.meta_path if isinstance(f, _ChokepointPostImportFinder)]
        assert len(finders) == 1, (
            f"Expected exactly one _ChokepointPostImportFinder in "
            f"sys.meta_path; found {len(finders)}."
        )

    def test_repeated_install_does_not_stack_finders(self) -> None:
        install_third_party_interceptions()
        install_third_party_interceptions()
        install_third_party_interceptions()
        finders = [f for f in sys.meta_path if isinstance(f, _ChokepointPostImportFinder)]
        assert len(finders) == 1


# ── Real-world insightface integration (run when insightface is installed) ──


def _insightface_available() -> bool:
    """Return ``True`` if the real ``insightface`` package can be
    imported in this environment.

    The chokepoint test suite primarily uses synthetic modules so the
    suite stays portable, but the synthetic coverage cannot catch the
    one regression we care most about: an upstream release renaming
    ``download`` to ``download_file_v2`` (or similar) and silently
    bypassing :data:`KNOWN_AUTO_DOWNLOADERS`. The real-import test
    below catches that the moment it lands."""
    try:
        import importlib.util

        return importlib.util.find_spec("insightface") is not None
    except (ImportError, ValueError):
        return False


@pytest.mark.skipif(
    not _insightface_available(),
    reason="insightface is not installed in this environment",
)
class TestRealInsightFaceChokepoint:
    """When ``insightface`` is installed (developer machines, the
    spike venv, and the e2e CI run that exercises buffalo_s), the
    chokepoint must have patched the *real* package — not just our
    synthetic test modules. Catches upstream renames in
    ``insightface.utils.storage``: if a future release renames
    ``download`` to ``download_v2``, this test fails immediately,
    and the maintainer knows to update
    :data:`KNOWN_AUTO_DOWNLOADERS`."""

    def test_every_known_insightface_entry_is_patched_on_real_module(
        self,
    ) -> None:
        # bpp.registry's import installs the chokepoint. Re-import so
        # the autouse fixture's reset doesn't leave the real module
        # un-patched for this test.
        import importlib

        import bpp.registry  # noqa: F401  (triggers chokepoint install)

        install_third_party_interceptions()

        insightface_entries = [e for e in KNOWN_AUTO_DOWNLOADERS if e.package == "insightface"]
        assert insightface_entries, (
            "test setup error: KNOWN_AUTO_DOWNLOADERS no longer includes any insightface entries"
        )

        unpatched: list[str] = []
        renamed: list[str] = []
        for entry in insightface_entries:
            try:
                module = importlib.import_module(entry.submodule)
            except ImportError as exc:
                pytest.fail(
                    f"Cannot import {entry.submodule} even though insightface is installed: {exc!s}"
                )
            fn = getattr(module, entry.function_name, None)
            if fn is None:
                renamed.append(f"{entry.submodule}.{entry.function_name}")
                continue
            if not getattr(fn, "__bpp_chokepoint__", False):
                unpatched.append(f"{entry.submodule}.{entry.function_name}")

        assert not renamed, (
            "These KNOWN_AUTO_DOWNLOADERS entries no longer exist on "
            "the real insightface package — upstream likely renamed "
            "them: " + ", ".join(renamed) + ". Update KNOWN_AUTO_DOWNLOADERS in "
            "bpp/registry/download_chokepoint.py to track the new "
            "names; otherwise restricted-model downloads can slip "
            "past the gate."
        )
        assert not unpatched, (
            "These insightface entries are present on the real "
            "package but not patched by the chokepoint: "
            + ", ".join(unpatched)
            + ". The patch installation logic is silently skipping "
            "them — investigate install_third_party_interceptions()."
        )

    def test_calling_real_patched_function_raises_block(self) -> None:
        import importlib

        import bpp.registry  # noqa: F401

        install_third_party_interceptions()

        storage = importlib.import_module("insightface.utils.storage")
        fn = getattr(storage, "download", None)
        if fn is None:
            pytest.skip("insightface.utils.storage.download not present")
        with pytest.raises(BlockedAutoDownloadError) as excinfo:
            fn("some_model_name")
        assert "insightface" in str(excinfo.value)
