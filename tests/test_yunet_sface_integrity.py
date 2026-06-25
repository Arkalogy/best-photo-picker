"""R4-H3: YuNet and SFace cached file integrity verification.

These two models predate the ModelSingleton migration and use
custom `_ensure_*_model()` helpers. Before this fix, cached files
were trusted as-is — only the first download was SHA-verified.
A tampered cache (compromised dir, cloud-sync overwrite, malicious
bind mount in Docker) would silently load unverified ONNX bytes
into the DNN inference engine.

The fix routes both helpers through `verify_existing()` and lets
ModelIntegrityError propagate. Plain network/import errors still
return False (model unavailable) as before.
"""

from __future__ import annotations

import hashlib
from unittest.mock import patch

import pytest


class TestYunetIntegrity:
    """Cached YuNet ONNX file must SHA-verify before reuse."""

    def test_existing_correct_hash_returns_true(self, tmp_path, monkeypatch):
        from bpp.scoring import face_yunet as face_mod

        target = tmp_path / "yunet.onnx"
        content = b"trusted yunet bytes"
        target.write_bytes(content)
        good_hash = hashlib.sha256(content).hexdigest()

        monkeypatch.setattr(face_mod, "_YUNET_MODEL_PATH", str(target))
        monkeypatch.setattr(face_mod, "_YUNET_MODEL_SHA256", good_hash)

        assert face_mod._ensure_yunet_model() is True
        assert target.exists()

    def test_tampered_cache_raises_integrity_error(self, tmp_path, monkeypatch):
        """The actual R4-H3 fix: a tampered cache file must NOT
        silently load. ModelIntegrityError propagates."""
        from bpp.scoring import face_yunet as face_mod
        from bpp.scoring.model_base import ModelIntegrityError

        target = tmp_path / "yunet.onnx"
        target.write_bytes(b"tampered yunet bytes")
        wrong_hash = "0" * 64

        monkeypatch.setattr(face_mod, "_YUNET_MODEL_PATH", str(target))
        monkeypatch.setattr(face_mod, "_YUNET_MODEL_SHA256", wrong_hash)

        with pytest.raises(ModelIntegrityError, match="cached file"):
            face_mod._ensure_yunet_model()
        assert not target.exists(), "Tampered cache must be deleted"

    def test_download_sha_mismatch_propagates(self, tmp_path, monkeypatch):
        """Download path: SHA mismatch on the freshly-downloaded file
        propagates as ModelIntegrityError (not silently False)."""
        import io

        from bpp.scoring import face_yunet as face_mod
        from bpp.scoring.model_base import ModelIntegrityError

        target = tmp_path / "yunet.onnx"
        # File doesn't exist → download path
        monkeypatch.setattr(face_mod, "_YUNET_MODEL_PATH", str(target))
        monkeypatch.setattr(face_mod, "_YUNET_MODEL_SHA256", "0" * 64)

        fake_resp = io.BytesIO(b"will not match the wrong-zero hash")
        fake_resp.__enter__ = lambda self: self
        fake_resp.__exit__ = lambda self, *a: None

        with (
            patch("urllib.request.urlopen", return_value=fake_resp),
            pytest.raises(ModelIntegrityError),
        ):
            face_mod._ensure_yunet_model()

    def test_network_failure_still_returns_false(self, tmp_path, monkeypatch):
        """Inverse: a plain network/OS error must still return False
        (model unavailable). Don't make every failure mode loud."""
        from bpp.scoring import face_yunet as face_mod

        target = tmp_path / "yunet.onnx"
        monkeypatch.setattr(face_mod, "_YUNET_MODEL_PATH", str(target))
        monkeypatch.setattr(face_mod, "_YUNET_MODEL_SHA256", "ab" * 32)

        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            assert face_mod._ensure_yunet_model() is False


class TestSfaceIntegrity:
    """Cached SFace ONNX file must SHA-verify before reuse."""

    def test_existing_correct_hash_returns_true(self, tmp_path, monkeypatch):
        from bpp.scoring import face_embed_sface_runtime as fe_mod

        target = tmp_path / "sface.onnx"
        content = b"trusted sface bytes"
        target.write_bytes(content)
        good_hash = hashlib.sha256(content).hexdigest()

        monkeypatch.setattr(fe_mod, "_SFACE_MODEL_PATH", str(target))
        monkeypatch.setattr(fe_mod, "_SFACE_MODEL_SHA256", good_hash)

        assert fe_mod._ensure_sface_model() is True
        assert target.exists()

    def test_tampered_cache_raises_integrity_error(self, tmp_path, monkeypatch):
        from bpp.scoring import face_embed_sface_runtime as fe_mod
        from bpp.scoring.model_base import ModelIntegrityError

        target = tmp_path / "sface.onnx"
        target.write_bytes(b"tampered sface bytes")

        monkeypatch.setattr(fe_mod, "_SFACE_MODEL_PATH", str(target))
        monkeypatch.setattr(fe_mod, "_SFACE_MODEL_SHA256", "0" * 64)

        with pytest.raises(ModelIntegrityError, match="cached file"):
            fe_mod._ensure_sface_model()
        assert not target.exists()

    def test_download_sha_mismatch_propagates(self, tmp_path, monkeypatch):
        import io

        from bpp.scoring import face_embed_sface_runtime as fe_mod
        from bpp.scoring.model_base import ModelIntegrityError

        target = tmp_path / "sface.onnx"
        monkeypatch.setattr(fe_mod, "_SFACE_MODEL_PATH", str(target))
        monkeypatch.setattr(fe_mod, "_SFACE_MODEL_SHA256", "0" * 64)

        fake_resp = io.BytesIO(b"won't match the zero hash")
        fake_resp.__enter__ = lambda self: self
        fake_resp.__exit__ = lambda self, *a: None

        with (
            patch("urllib.request.urlopen", return_value=fake_resp),
            pytest.raises(ModelIntegrityError),
        ):
            fe_mod._ensure_sface_model()

    def test_network_failure_returns_false(self, tmp_path, monkeypatch):
        from bpp.scoring import face_embed_sface_runtime as fe_mod

        target = tmp_path / "sface.onnx"
        monkeypatch.setattr(fe_mod, "_SFACE_MODEL_PATH", str(target))
        monkeypatch.setattr(fe_mod, "_SFACE_MODEL_SHA256", "ab" * 32)

        with patch("urllib.request.urlopen", side_effect=OSError("network error")):
            assert fe_mod._ensure_sface_model() is False


class TestClipIntegrityInAnalyzeWorker:
    """R4-M1: the analyze worker's CLIP phase used to catch bare
    Exception around `ensure_model()` and silently downgrade any
    failure to a "skip phase 3" warning. ModelIntegrityError must
    propagate as a fatal event — a tampered cached model or MITM'd
    download is exactly the case the SHA pin defends against.
    """

    def test_integrity_error_aborts_clip_phase(self, monkeypatch):
        """Mock clip_embed.ensure_model to raise ModelIntegrityError;
        the worker must emit an `error` event (not `warning`) and
        let the exception propagate."""
        import threading

        from bpp.scoring import clip_embed as ce_mod
        from bpp.scoring.model_base import ModelIntegrityError
        from bpp.web.analyze_phases import run_clip_phase

        def _raise_integrity(*a, **kw):
            raise ModelIntegrityError("test: CLIP cache tampered")

        monkeypatch.setattr(ce_mod, "ensure_model", _raise_integrity)
        # Also short-circuit the availability check so we reach the
        # ensure_model() call rather than the early return
        monkeypatch.setattr(ce_mod, "is_available", lambda: True)

        # Capture emitted events
        emitted: list[dict] = []

        # run_clip_phase is the Phase 3 entry point. It runs against an
        # existing analysis DB; we pass empty `valid` so the only thing that
        # runs is the model-readiness check — exactly where the failure surfaces.
        with pytest.raises(ModelIntegrityError, match="CLIP cache tampered"):
            run_clip_phase(None, [], emit=emitted.append, cancel_event=threading.Event())

        # The error event must be present (not a warning)
        error_events = [e for e in emitted if e.get("type") == "error"]
        warning_events = [e for e in emitted if e.get("type") == "warning"]
        assert error_events, f"CLIP integrity failure must emit an 'error' event, got {emitted}"
        assert "integrity" in error_events[0]["message"].lower()
        assert not warning_events, (
            "Integrity must NOT emit 'warning' (that's the downgrade-to-skip "
            "path that this fix closes)"
        )

    def test_generic_failure_still_skips_quietly(self, monkeypatch):
        """Inverse: a normal RuntimeError (network, missing dep, etc.)
        from ensure_model still triggers the skip path. Don't make
        every CLIP failure mode loud — only integrity ones."""
        import threading

        from bpp.scoring import clip_embed as ce_mod
        from bpp.web.analyze_phases import run_clip_phase

        def _raise_runtime(*a, **kw):
            raise RuntimeError("test: simulated network failure")

        monkeypatch.setattr(ce_mod, "ensure_model", _raise_runtime)
        monkeypatch.setattr(ce_mod, "is_available", lambda: True)

        emitted: list[dict] = []

        # Should NOT raise — generic failures gracefully skip phase 3
        result = run_clip_phase(None, [], emit=emitted.append, cancel_event=threading.Event())
        assert result == 0
        warning_events = [e for e in emitted if e.get("type") == "warning"]
        assert warning_events, "Generic CLIP failure should emit a warning"


class TestFaceWorkerIntegrityPropagation:
    """R5-H3: face_worker._extract_one used to wrap the entire
    embedding extraction path in a broad `except Exception`,
    swallowing ModelIntegrityError as "no faces in this photo."
    R4-H3 made YuNet/SFace cached verification raise — but the
    error stopped here. Net effect: every analyzed photo silently
    reported zero faces while the user saw a successful run."""

    def test_integrity_error_propagates_through_extract_one(self, monkeypatch):
        from bpp.scoring.model_base import ModelIntegrityError
        from bpp.web import face_worker

        def _raise_integrity(*a, **kw):
            raise ModelIntegrityError("test: face model tampered")

        # Patch extract_face_embeddings inside face_worker's namespace
        # (it's imported at module top, so face_worker.extract_face_embeddings
        # is the alias we need to override).
        monkeypatch.setattr(face_worker, "extract_face_embeddings", _raise_integrity)

        # load_and_downscale must succeed so we reach the embedding call
        monkeypatch.setattr(face_worker, "load_and_downscale", lambda fp, ms: object())

        with pytest.raises(ModelIntegrityError, match="face model tampered"):
            face_worker._extract_one("/fake/path.jpg", max_long_side=512)


class TestPetsIntegrityPropagation:
    """R5-H3: pets.detect_pets_from_file used to swallow integrity
    failures as "no pets detected." Plus pets.ensure_model() didn't
    verify cached files (the R4-H3 fix touched YuNet/SFace but not
    pets)."""

    def test_ensure_model_verifies_cached_pets_file(self, tmp_path, monkeypatch):
        from bpp.scoring import pets as pets_mod
        from bpp.scoring.model_base import ModelIntegrityError

        target = tmp_path / "yolo.onnx"
        target.write_bytes(b"tampered yolo bytes")

        monkeypatch.setattr(pets_mod, "_get_model_path", lambda: str(target))
        monkeypatch.setattr(pets_mod, "_MODEL_SHA256", "0" * 64)

        with pytest.raises(ModelIntegrityError, match="cached file"):
            pets_mod.ensure_model()
        # Bad cache file deleted
        assert not target.exists()

    def test_detect_pets_propagates_integrity(self, monkeypatch):
        from bpp.scoring import pets as pets_mod
        from bpp.scoring.model_base import ModelIntegrityError

        def _raise_integrity(*a, **kw):
            raise ModelIntegrityError("test: pet model tampered")

        # Make detect_pets blow up with integrity error
        monkeypatch.setattr(pets_mod, "detect_pets", _raise_integrity)
        # Make cv2.imread return a fake non-None image
        import numpy as np

        fake_img = np.zeros((10, 10, 3), dtype=np.uint8)
        monkeypatch.setattr(pets_mod.cv2, "imread", lambda *a: fake_img)

        with pytest.raises(ModelIntegrityError):
            pets_mod.detect_pets_from_file("/fake/x.jpg")
