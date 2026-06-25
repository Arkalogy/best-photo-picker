"""Tests for bpp.utils.download.

Every test passes ``registry_id`` explicitly: ``None`` for ancillary
cases that are not exercising the policy gate, and a real legal-
registry id for the gate tests. The whole point of the chokepoint is
that the argument is REQUIRED; the absence of a default keeps the
gate honest.
"""

from __future__ import annotations

import io
import os
from unittest.mock import patch

import pytest

from bpp.utils.download import download_file


class TestDownloadFileCleanup:
    """download_file must remove partial dest on failure."""

    def test_cleans_up_partial_file_on_network_error(self, tmp_path):
        dest = str(tmp_path / "model.onnx")

        with (
            pytest.raises(OSError),
            patch("urllib.request.urlopen", side_effect=OSError("connection refused")),
        ):
            download_file(
                "https://example.com/model.onnx",
                dest,
                registry_id=None,
            )

        assert not os.path.exists(dest), "Partial file should be removed on failure"

    def test_cleans_up_partial_file_on_write_error(self, tmp_path):
        """Simulate a write error mid-transfer."""
        dest = str(tmp_path / "model.onnx")

        class FakeResp:
            def read(self, n=-1):
                raise OSError("disk full")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        with pytest.raises(OSError), patch("urllib.request.urlopen", return_value=FakeResp()):
            download_file(
                "https://example.com/model.onnx",
                dest,
                registry_id=None,
            )

        assert not os.path.exists(dest), "Partial file should be removed on write error"

    def test_successful_download_keeps_file(self, tmp_path):
        """On success, dest file should exist with content."""
        dest = str(tmp_path / "model.onnx")
        content = b"fake model data"

        fake_resp = io.BytesIO(content)
        fake_resp.__enter__ = lambda self: self
        fake_resp.__exit__ = lambda self, *a: None

        with patch("urllib.request.urlopen", return_value=fake_resp):
            download_file(
                "https://example.com/model.onnx",
                dest,
                registry_id=None,
            )

        assert os.path.exists(dest)
        with open(dest, "rb") as f:
            assert f.read() == content


class TestDownloadFileSha256:
    """When `sha256` is supplied, the downloaded bytes must match —
    a corrupted / tampered binary must never end up usable in the
    cache. This is the integrity check that defends against host
    compromise and MITM."""

    def _fake_resp(self, content: bytes):
        resp = io.BytesIO(content)
        resp.__enter__ = lambda self: self
        resp.__exit__ = lambda self, *a: None
        return resp

    def test_match_keeps_file(self, tmp_path):
        """Correct hash → file is kept and download returns normally."""
        import hashlib

        dest = str(tmp_path / "model.onnx")
        content = b"fake model data"
        digest = hashlib.sha256(content).hexdigest()

        with patch("urllib.request.urlopen", return_value=self._fake_resp(content)):
            download_file(
                "https://example.com/model.onnx",
                dest,
                registry_id=None,
                sha256=digest,
            )

        assert os.path.exists(dest), "File must be kept on a hash match"
        with open(dest, "rb") as f:
            assert f.read() == content

    def test_mismatch_deletes_and_raises(self, tmp_path):
        """Wrong hash → file is deleted and ModelIntegrityError is raised."""
        from bpp.scoring.model_base import ModelIntegrityError

        dest = str(tmp_path / "model.onnx")
        content = b"fake model data"
        wrong_digest = "0" * 64

        with (
            patch("urllib.request.urlopen", return_value=self._fake_resp(content)),
            pytest.raises(ModelIntegrityError, match="SHA-256 mismatch"),
        ):
            download_file(
                "https://example.com/model.onnx",
                dest,
                registry_id=None,
                sha256=wrong_digest,
            )

        assert not os.path.exists(dest), (
            "Mismatched bytes must be removed — leaving them on disk would "
            "let a future load skip the verification path entirely"
        )

    def test_no_sha256_no_check(self, tmp_path):
        """Backwards compat: omitting `sha256` keeps the old behavior."""
        dest = str(tmp_path / "model.onnx")
        content = b"anything goes"

        with patch("urllib.request.urlopen", return_value=self._fake_resp(content)):
            download_file(
                "https://example.com/model.onnx",
                dest,
                registry_id=None,
            )

        assert os.path.exists(dest)

    def test_match_is_case_insensitive_and_strip_safe(self, tmp_path):
        """Hashes copy-pasted from `shasum -a 256` may have leading /
        trailing whitespace or uppercase hex. Both must work."""
        import hashlib

        dest = str(tmp_path / "model.onnx")
        content = b"fake model data"
        digest = hashlib.sha256(content).hexdigest().upper() + "  \n"

        with patch("urllib.request.urlopen", return_value=self._fake_resp(content)):
            download_file(
                "https://example.com/model.onnx",
                dest,
                registry_id=None,
                sha256=digest,
            )

        assert os.path.exists(dest)


class TestVerifyExisting:
    """D-02: verify_existing reads a cached file and re-hashes it
    against the pinned SHA. Used by ModelSingleton.ensure_model
    BEFORE returning a cached model — the previous code returned
    cached files unconditionally, leaving post-download tampering
    undetected.
    """

    def test_match_returns_silently(self, tmp_path):
        """Correct hash → no exception."""
        import hashlib

        from bpp.utils.download import verify_existing

        path = str(tmp_path / "model.onnx")
        content = b"trusted bytes"
        with open(path, "wb") as f:
            f.write(content)
        digest = hashlib.sha256(content).hexdigest()

        verify_existing(path, sha256=digest)
        # File still on disk
        assert os.path.exists(path)

    def test_mismatch_deletes_and_raises(self, tmp_path):
        """Tampered cache → ModelIntegrityError + file deleted."""
        from bpp.scoring.model_base import ModelIntegrityError
        from bpp.utils.download import verify_existing

        path = str(tmp_path / "model.onnx")
        with open(path, "wb") as f:
            f.write(b"tampered bytes")
        wrong = "f" * 64

        with pytest.raises(ModelIntegrityError, match="cached file"):
            verify_existing(path, sha256=wrong)
        assert not os.path.exists(path), (
            "Bad cached file must be removed so the next caller can re-download cleanly"
        )


class TestEnsureModelVerifiesCache:
    """End-to-end: ModelSingleton.ensure_model on a CACHED model file
    must verify against the pinned SHA before returning the path.
    Without this, post-download tampering would silently load
    unverified bytes into ONNX/native inference.
    """

    def test_cached_file_with_bad_sha_raises_integrity_error(self, tmp_path):
        from bpp.scoring.model_base import ModelIntegrityError, ModelSingleton

        # Pre-populate the "cache" with content that doesn't match
        # the pinned SHA, simulating post-download tampering.
        model_path = tmp_path / "fake_model.onnx"
        model_path.write_bytes(b"tampered post-download")
        wrong_pin = "0" * 64

        singleton = ModelSingleton(
            name="Test",
            model_path=model_path,
            model_url="https://example.com/never-fetched",
            create_fn=lambda p: object(),
            registry_id=None,
            model_sha256=wrong_pin,
        )

        with pytest.raises(ModelIntegrityError):
            singleton.ensure_model()

    def test_cached_file_with_correct_sha_returns_path(self, tmp_path):
        import hashlib

        from bpp.scoring.model_base import ModelSingleton

        model_path = tmp_path / "good_model.onnx"
        content = b"trusted cache"
        model_path.write_bytes(content)
        good_pin = hashlib.sha256(content).hexdigest()

        singleton = ModelSingleton(
            name="Test",
            model_path=model_path,
            model_url="https://example.com/never-fetched",
            create_fn=lambda p: object(),
            registry_id=None,
            model_sha256=good_pin,
        )

        assert singleton.ensure_model() == model_path

    def test_get_propagates_cache_tampering(self, tmp_path):
        """Top-level get() must let ModelIntegrityError out — that's
        the whole point. Without this, the post-download tampering
        case silently degrades to model-unavailable."""
        from bpp.scoring.model_base import ModelIntegrityError, ModelSingleton

        model_path = tmp_path / "fake_model.onnx"
        model_path.write_bytes(b"tampered")
        wrong_pin = "0" * 64

        singleton = ModelSingleton(
            name="Test",
            model_path=model_path,
            model_url="https://example.com/never-fetched",
            create_fn=lambda p: object(),
            registry_id=None,
            model_sha256=wrong_pin,
        )

        with pytest.raises(ModelIntegrityError):
            singleton.get()
