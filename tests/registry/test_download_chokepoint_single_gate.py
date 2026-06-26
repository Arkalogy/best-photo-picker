"""Tests for the single-gate model-download chokepoint.

The contract enforced here:

1. ``bpp.utils.download.download_file`` requires ``registry_id`` —
   callers cannot omit it. The argument has no default; passing
   ``None`` is an explicit opt-out for ancillary downloads.

2. When ``registry_id`` names an unknown entry, the call raises
   ``ValueError`` before any network activity — a typo or a fresh
   model that wasn't registered first does not get a silent pass.

3. When ``registry_id`` names a real entry, the policy gate fires
   BEFORE the fetch. A restricted entry whose acceptance is
   missing / withdrawn / blocked refuses the download with
   ``ModelLoadBlockedError``.

4. A permissive entry passes through silently — no gate friction.

5. No raw-urllib / requests / hf_hub / urlretrieve usage exists in
   the ``bpp/`` source tree outside the small explicit allowlist
   (the chokepoint itself, the signed-manifest fetcher, and the
   update checker). A new contributor adding a download path
   outside this list fails the test.
"""

from __future__ import annotations

import io
import pathlib
from typing import ClassVar
from unittest.mock import patch

import pytest

# ── 1. registry_id is required ──


class TestRegistryIdRequired:
    def test_omitting_registry_id_raises_type_error(self, tmp_path):
        """Static-like contract: the kwarg has no default. The whole
        point of the chokepoint is that every download call site
        confronts the gate question."""
        from bpp.utils.download import download_file

        with pytest.raises(TypeError, match="registry_id"):
            download_file("https://example.com/x", str(tmp_path / "x"))  # type: ignore[call-arg]


# ── 2. unknown id raises before network ──


class TestUnknownRegistryIdRefused:
    def test_unknown_id_raises_value_error(self, tmp_path):
        from bpp.utils.download import download_file

        with (
            patch("urllib.request.urlopen") as mock_urlopen,
            pytest.raises(ValueError, match="unknown registry_id"),
        ):
            download_file(
                "https://example.com/x",
                str(tmp_path / "x"),
                registry_id="this_entry_does_not_exist",
            )
        # Critical: the network call must NOT have fired.
        mock_urlopen.assert_not_called()


# ── 3. policy gate fires before fetch ──


class TestPolicyGateFiresBeforeFetch:
    """A restricted entry without a valid acceptance must refuse the
    download before urlopen is called. This is the central contract
    of the single-gate refactor: an unsigned/unaccepted/restricted
    model cannot reach the network through download_file even if
    the caller has the URL in hand.
    """

    def _restricted_entry_no_acceptance(self):
        """Return the registry id of a known restricted entry, and
        force the policy gate into a non-ALLOW state for the test
        scope."""
        from bpp.registry.model_registry import get_entry

        # buffalo_s is registered as restricted by default.
        entry_id = "insightface_buffalo_s"
        assert get_entry(entry_id) is not None, "test prereq: entry exists"
        return entry_id

    def test_restricted_unaccepted_refuses_download(self, tmp_path):
        from bpp.registry.policy import (
            ModelLoadBlockedError,
            ModelLoadDecision,
            PolicyResult,
        )
        from bpp.utils.download import download_file

        entry_id = self._restricted_entry_no_acceptance()

        # Stub enforce_load_policy_for to raise — we are testing that
        # download_file actually calls it, not that the policy itself
        # is correct (that has its own dedicated suite). This keeps
        # the test hermetic regardless of the local acceptance log
        # state.
        blocked = ModelLoadBlockedError(
            PolicyResult(
                decision=ModelLoadDecision.BLOCKED_NEEDS_ACK,
                reason="test: missing acceptance",
                entry_id=entry_id,
            )
        )
        with (
            patch(
                "bpp.registry.enforce_load_policy_for",
                side_effect=blocked,
            ),
            patch("urllib.request.urlopen") as mock_urlopen,
            pytest.raises(ModelLoadBlockedError),
        ):
            download_file(
                "https://example.com/buffalo_s.zip",
                str(tmp_path / "buffalo_s.zip"),
                registry_id=entry_id,
            )

        # urlopen MUST NOT have been called — gate is pre-network.
        mock_urlopen.assert_not_called()


# ── 4. permissive entry passes silently ──


class TestPermissiveEntryPassesThrough:
    def test_mit_entry_downloads_normally(self, tmp_path):
        from bpp.utils.download import download_file

        # insightface_scrfd_25g is registered as MIT — the gate
        # short-circuits to ALLOW without any acceptance row.
        # (opencv_yunet is also permissive but Apache 2.0, so it
        # now requires acceptance and would fail this test on a
        # fresh acceptance log.)
        content = b"scrfd model bytes"
        resp = io.BytesIO(content)
        resp.__enter__ = lambda self: self
        resp.__exit__ = lambda self, *a: None

        dest = str(tmp_path / "scrfd.onnx")
        with patch("urllib.request.urlopen", return_value=resp):
            download_file(
                "https://example.com/scrfd.onnx",
                dest,
                registry_id="insightface_scrfd_25g",
            )

        assert pathlib.Path(dest).read_bytes() == content


# ── 5. chokepoint bypass is opened during the fetch ──


class TestBypassWindowDuringFetch:
    """While ``download_file`` is running, the per-thread bypass
    window must be open — that is what lets the call (and any
    nested upstream auto-downloader on the same thread) reach the
    network. The window must be closed again after the fetch, so a
    later thread does NOT inherit it.
    """

    def test_bypass_open_during_fetch_closed_after(self, tmp_path):
        from bpp.registry.download_chokepoint import _is_bypass_active
        from bpp.utils.download import download_file

        observed: dict[str, bool] = {}

        def fake_urlopen(*_a, **_k):
            observed["during"] = _is_bypass_active()
            resp = io.BytesIO(b"x")
            resp.__enter__ = lambda self: self  # type: ignore[method-assign]
            resp.__exit__ = lambda self, *_x: None  # type: ignore[method-assign]
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            download_file(
                "https://example.com/x",
                str(tmp_path / "x"),
                registry_id=None,
            )

        assert observed.get("during") is True, (
            "Bypass window must be open during the urlopen call so "
            "any upstream auto-downloader nested inside inherits it."
        )
        assert _is_bypass_active() is False, (
            "Bypass window must be closed again after the fetch — "
            "leaving it open would let a later unrelated call sneak past "
            "the chokepoint patches."
        )


# ── 6. repo-wide guard: no alternate download primitives ──


class TestNoAlternateDownloadPrimitives:
    """Every model fetch must go through bpp.utils.download.download_file.
    A grep over the source tree forbids urlretrieve / requests.get / etc.
    outside an explicit allowlist.

    The allowlist:

    * ``bpp/utils/download.py`` — the canonical implementation itself.
    * ``bpp/registry/download_chokepoint.py`` — patches third-party
      auto-downloaders; references them by string, not by call.
    * ``bpp/registry/remote_registry.py`` — fetches the signed
      registry manifest with its own redirect-allowlist handler.
      Not a model download.
    * ``bpp/web/update_checker.py`` — checks GitHub for a new
      release; not a model download.
    """

    ALLOWLIST: ClassVar[set[str]] = {
        "bpp/utils/download.py",
        "bpp/registry/download_chokepoint.py",
        "bpp/registry/remote_registry.py",
        "bpp/web/update_checker.py",
    }

    # Patterns that indicate a download path that should have gone
    # through download_file instead.
    FORBIDDEN_PATTERNS: ClassVar[tuple[str, ...]] = (
        "urlretrieve",
        "urllib.request.urlopen",
        "hf_hub_download",
        "snapshot_download",
        # `requests.get(` / `requests.post(` — both can pull bytes.
        # Match only on the open paren so import lines like
        # `from requests import get` don't falsely trip; the open
        # paren on the call site is what creates the download.
        "requests.get(",
        "requests.post(",
    )

    def _iter_source_files(self):
        repo_root = pathlib.Path(__file__).resolve().parents[2]
        bpp_root = repo_root / "bpp"
        for path in bpp_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(repo_root).as_posix()
            yield rel, path

    def test_no_forbidden_download_primitive_outside_allowlist(self):
        offenders: list[tuple[str, str, int]] = []
        for rel, path in self._iter_source_files():
            if rel in self.ALLOWLIST:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                for pattern in self.FORBIDDEN_PATTERNS:
                    if pattern in line:
                        offenders.append((rel, pattern, lineno))

        assert offenders == [], (
            "Found download primitives outside the canonical chokepoint. "
            "Every model fetch must go through bpp.utils.download.download_file. "
            "Offenders:\n" + "\n".join(f"  {f}:{ln} — `{pat}`" for f, pat, ln in offenders)
        )
