"""Integration tests for ModelSingleton — focused on the new SHA-256
verification behavior. The unit tests for `download_file()` cover the
hash check itself; this file proves it's plumbed correctly through
ensure_model().

The threat model: a tampered binary returned by a compromised CDN /
MITM / DNS hijack must NEVER end up cached on disk. ML models execute
as code (ONNX custom ops, TFLite XNNPACK, native YOLO inference), so
unverified bytes are RCE on import.
"""

from __future__ import annotations

import hashlib
import io
from unittest.mock import patch

import pytest

from bpp.scoring.model_base import ModelSingleton


def _fake_resp(content: bytes):
    """A urlopen-compatible context manager that yields *content*."""
    resp = io.BytesIO(content)
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda self, *a: None
    return resp


class TestEnsureModelHashCheck:
    def test_matching_hash_keeps_file_and_returns_path(self, tmp_path):
        """Correct hash → ensure_model() succeeds and the file lands at
        model_path."""
        content = b"fake onnx bytes"
        digest = hashlib.sha256(content).hexdigest()
        target = tmp_path / "model.onnx"

        ms = ModelSingleton(
            name="test",
            model_path=target,
            model_url="https://example.com/model.onnx",
            model_sha256=digest,
            create_fn=lambda p: object(),
            registry_id=None,
        )
        with patch("urllib.request.urlopen", return_value=_fake_resp(content)):
            result = ms.ensure_model()

        assert result == target
        assert target.exists()
        assert target.read_bytes() == content

    def test_mismatched_hash_rejects_and_does_not_cache(self, tmp_path):
        """Tampered bytes → ensure_model() returns None, NOTHING ends
        up at model_path. Critical: a future load must not skip the
        verification path because bytes happen to be cached."""
        content = b"tampered onnx bytes"
        wrong_digest = "0" * 64
        target = tmp_path / "model.onnx"

        from bpp.scoring.model_base import ModelIntegrityError

        ms = ModelSingleton(
            name="test",
            model_path=target,
            model_url="https://example.com/model.onnx",
            model_sha256=wrong_digest,
            create_fn=lambda p: object(),
            registry_id=None,
        )
        # D-02: hash mismatch on download propagates as
        # ModelIntegrityError (was: returned None silently).
        with (
            patch("urllib.request.urlopen", return_value=_fake_resp(content)),
            pytest.raises(ModelIntegrityError, match="SHA-256 mismatch"),
        ):
            ms.ensure_model()

        assert not target.exists(), "tampered bytes must NOT be left in the cache"
        tmp_file = target.with_suffix(target.suffix + ".tmp")
        assert not tmp_file.exists(), "tmp file must be cleaned up too"

    def test_no_hash_keeps_legacy_behavior(self, tmp_path):
        """Until every model registration has a hash, leaving model_sha256=None
        must keep working — the field defaults to None for back-compat."""
        content = b"legacy bytes"
        target = tmp_path / "model.onnx"

        ms = ModelSingleton(
            name="test",
            model_path=target,
            model_url="https://example.com/model.onnx",
            model_sha256=None,
            create_fn=lambda p: object(),
            registry_id=None,
        )
        with patch("urllib.request.urlopen", return_value=_fake_resp(content)):
            result = ms.ensure_model()

        assert result == target
        assert target.exists()

    def test_existing_file_with_correct_hash_skips_download(self, tmp_path):
        """If model_path exists AND its bytes match the pinned hash,
        ensure_model() short-circuits without re-downloading.

        D-02: cached files ARE re-verified before use (was: trusted
        as-is). The cost is a hash of the cached bytes per process
        start; the gain is detecting cache tampering between sessions
        (compromised cache dir, cloud-sync overwrite, malicious bind
        mount in Docker). For users who want to skip the check on
        a known-good cache, they can clear model_sha256 in their
        registration — but every shipped registration sets it.
        """
        import hashlib

        content = b"already on disk"
        target = tmp_path / "model.onnx"
        target.write_bytes(content)
        good_hash = hashlib.sha256(content).hexdigest()

        ms = ModelSingleton(
            name="test",
            model_path=target,
            model_url="https://example.com/model.onnx",
            model_sha256=good_hash,
            create_fn=lambda p: object(),
            registry_id=None,
        )
        with patch("urllib.request.urlopen") as urlopen:
            result = ms.ensure_model()
            assert urlopen.call_count == 0, "Matching hash should skip download"

        assert result == target

    def test_existing_file_with_wrong_hash_raises_and_clears_cache(self, tmp_path):
        """D-02 contract: a cached file whose bytes don't match the
        pinned hash is treated as tampered. Raise ModelIntegrityError
        AND remove the bad file so the next caller can re-download
        cleanly. Previously this was a silent skip-the-check pass."""
        from bpp.scoring.model_base import ModelIntegrityError

        target = tmp_path / "model.onnx"
        target.write_bytes(b"tampered cache")

        ms = ModelSingleton(
            name="test",
            model_path=target,
            model_url="https://example.com/model.onnx",
            model_sha256="0" * 64,
            create_fn=lambda p: object(),
            registry_id=None,
        )
        with pytest.raises(ModelIntegrityError, match="cached file"):
            ms.ensure_model()
        assert not target.exists(), "Tampered cache must be deleted on detection"

    def test_bundled_fallback_skips_hash_check(self, tmp_path):
        """Bundled fallback ships in our wheel — controlled by the
        package build, not the network. Don't verify it (and don't
        require the registration to populate model_sha256 with the
        bundled-bytes digest)."""
        bundled = tmp_path / "bundled" / "model.onnx"
        bundled.parent.mkdir(parents=True)
        bundled.write_bytes(b"bundled bytes")

        target = tmp_path / "cache" / "model.onnx"

        ms = ModelSingleton(
            name="test",
            model_path=target,
            model_url="https://example.com/model.onnx",
            model_sha256="0" * 64,  # mismatch — but bundled path is taken first
            bundled_path=str(bundled),
            create_fn=lambda p: object(),
            registry_id=None,
        )
        with patch("urllib.request.urlopen") as urlopen:
            result = ms.ensure_model()
            assert urlopen.call_count == 0, "bundled fallback must short-circuit the download path"

        assert result == target
        assert target.read_bytes() == b"bundled bytes"

    def test_get_propagates_hash_mismatch(self, tmp_path):
        """D-02 inverted contract: a hash mismatch flows up through
        .get() as ModelIntegrityError. Previously get() returned None
        silently (treated as missing-dep), which let a tampered model
        degrade to a 0.0 score with no operator-visible signal —
        exactly what the SHA pin is supposed to prevent."""
        from bpp.scoring.model_base import ModelIntegrityError

        content = b"tampered"
        target = tmp_path / "model.onnx"

        ms = ModelSingleton(
            name="test",
            model_path=target,
            model_url="https://example.com/model.onnx",
            model_sha256="0" * 64,
            create_fn=lambda p: object(),
            registry_id=None,
        )
        with (
            patch("urllib.request.urlopen", return_value=_fake_resp(content)),
            pytest.raises(ModelIntegrityError),
        ):
            ms.get()


@pytest.mark.parametrize(
    "module_path,attrs",
    [
        # Each model registration must set model_sha256 to a 64-hex string.
        # Bundled-only models with model_url=None are exempt (NudeNet).
        ("bpp.scoring.face_blazeface_fr", ["_fr_detector"]),
        ("bpp.scoring.face_scrfd", ["_scrfd_model"]),
        ("bpp.scoring.face_hand_filter", ["_hand_landmarker"]),
        ("bpp.scoring.face_expression", ["_face_landmarker"]),
        ("bpp.scoring.pose", ["_pose_model"]),
        ("bpp.scoring.segmentation", ["_segmenter"]),
        ("bpp.scoring.pets", ["_yolo"]),
        ("bpp.scoring.clip_embed", ["_clip_visual", "_clip_text"]),
        ("bpp.scoring.clip_tokenizer", ["_vocab"]),
    ],
)
class TestAllRegistrationsHaveHash:
    """Every ModelSingleton with a model_url must declare model_sha256.
    A new registration that ships without a hash silently re-introduces
    the H2 attack surface."""

    def test_registration_has_sha256(self, module_path, attrs):
        import importlib

        mod = importlib.import_module(module_path)
        for attr in attrs:
            singleton = getattr(mod, attr)
            assert singleton.model_url is not None, (
                f"{module_path}.{attr} has no model_url — does it belong here?"
            )
            assert singleton.model_sha256 is not None, (
                f"{module_path}.{attr} downloads from {singleton.model_url} "
                f"but doesn't declare model_sha256. Compute it with: "
                f"shasum -a 256 ~/.cache/bpp/<filename>"
            )
            assert len(singleton.model_sha256) == 64, (
                f"{module_path}.{attr} has model_sha256={singleton.model_sha256!r}, "
                f"expected a 64-char hex string"
            )
            int(singleton.model_sha256, 16)  # raises if not hex


class TestBlazeFaceShortRangeHasHash:
    """face.py uses both a ModelSingleton AND a non-class-based wrapper
    around download_file (for YuNet). Both paths must be hashed."""

    def test_blazeface_short_range_has_hash(self):
        from bpp.scoring.face import _mp_blazeface

        assert _mp_blazeface.model_sha256 is not None
        assert len(_mp_blazeface.model_sha256) == 64
