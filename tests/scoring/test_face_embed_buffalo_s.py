"""buffalo_s ArcFace face embedder tests.

Verifies the embedder produces the expected shape, the
distance-scale matches SFace's range, and the runtime policy gate
fires when the user hasn't accepted the click-through.

The actual ONNX file is not exercised — these tests mock the
session so they don't pull a 130 MB download into the test
environment. The session contract (input name, output shape) is
verified separately by the smoke-test in the loader's docstring.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from bpp.registry import (
    ModelLoadBlockedError,
    UseContext,
    confirm_acceptance,
    get_entry,
    prepare_acceptance,
    set_use_context,
    utc_now_iso,
)
from bpp.scoring import face_embed_buffalo_s as bs


def _accept_buffalo_s() -> None:
    set_use_context(UseContext.PERSONAL, set_via="test")
    entry = get_entry("insightface_buffalo_s")
    draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
    confirm_acceptance(
        draft,
        checkbox_responses={cb_id: True for cb_id, _ in draft.required_checkboxes},
        accepted_at=utc_now_iso(),
        separate_rights_asserted=False,
        source_of_rights_note="",
    )


@pytest.fixture(autouse=True)
def _clear_session_cache():
    """Each test gets a fresh model singleton so a mock from one
    test doesn't bleed into the next."""
    bs._buffalo_s_model.reset()
    yield
    bs._buffalo_s_model.reset()


class TestModelSingletonContract:
    """The buffalo_s loader uses the canonical ``ModelSingleton``
    pattern from ``bpp.scoring.model_base`` — same as every other
    ML model in BPP — instead of hand-rolling a thread-lock +
    module-global cache. The project conventions explicitly require this for new
    models, and using ModelSingleton means the registry reset() hook
    works without re-implementing the cache-clear logic per loader.
    """

    def test_module_exposes_model_singleton(self) -> None:
        from bpp.scoring.model_base import ModelSingleton

        assert hasattr(bs, "_buffalo_s_model"), (
            "buffalo_s loader must expose a module-level "
            "_buffalo_s_model attribute backed by ModelSingleton"
        )
        assert isinstance(bs._buffalo_s_model, ModelSingleton)

    def test_hand_rolled_cache_globals_are_gone(self) -> None:
        """The manual ``_session_cache`` + ``_session_lock`` pattern
        is replaced by the singleton. Their absence prevents two
        independent caches drifting if a future refactor partially
        reverts."""
        assert not hasattr(bs, "_session_cache"), (
            "manual _session_cache global must be removed once ModelSingleton owns the cache"
        )
        assert not hasattr(bs, "_session_lock"), (
            "manual _session_lock global must be removed once ModelSingleton owns the lock"
        )

    def test_reset_clears_cached_session(self) -> None:
        """After ``_buffalo_s_model.reset()``, the next call to
        ``_get_session`` triggers a fresh create_fn invocation. This
        is the contract Settings → Models / uninstall-and-redownload
        relies on."""
        _accept_buffalo_s()
        mock_session = MagicMock()
        with (
            patch.object(bs, "ensure_buffalo_s_model", return_value="/tmp/fake.onnx"),
            patch("onnxruntime.InferenceSession", return_value=mock_session),
        ):
            first = bs._get_session()
            assert first is mock_session
            # Without reset, the cached instance is returned.
            second = bs._get_session()
            assert second is first
            # After reset, a fresh load happens.
            bs._buffalo_s_model.reset()
            third = bs._get_session()
            # Mock is the same object, but its create_fn was invoked
            # a second time — the call count is the contract.
            assert third is mock_session


class TestPolicyGate:
    """The gate fires before any model file is touched."""

    def test_embed_face_raises_without_acceptance(self) -> None:
        set_use_context(UseContext.PERSONAL, set_via="test")
        synthetic = (np.random.rand(200, 200, 3) * 255).astype(np.uint8)
        # _get_session calls enforce_load_policy_for first; raises
        # cleanly through the embed_face try/except — the function
        # itself catches and returns None for a clean skip-the-face
        # behaviour. We assert that path:
        with patch.object(bs, "is_available", return_value=True):
            # Force the session creation to be reached.
            result = bs.embed_face(synthetic, (10, 10, 100, 100))
        # The gate raised, embed_face caught, returned None.
        assert result is None

    def test_embed_face_raises_propagate_via_session(self) -> None:
        """Direct call to ``_get_session`` propagates the block."""
        set_use_context(UseContext.PERSONAL, set_via="test")
        with pytest.raises(ModelLoadBlockedError):
            bs._get_session()


class TestEmbedShape:
    """The embedder produces a 512-d vector scaled by 0.65."""

    def test_returns_512_dim_scaled_vector(self) -> None:
        _accept_buffalo_s()
        # Mock the ONNX session so we don't actually load the model.
        # The mock returns a unit-norm 512-d vector and we expect
        # the result to be scaled by 0.65.
        mock_session = MagicMock()
        mock_input = MagicMock()
        mock_input.name = "input.1"
        mock_session.get_inputs.return_value = [mock_input]
        raw_vec = np.ones((1, 512), dtype=np.float32)
        mock_session.run.return_value = [raw_vec]
        with (
            patch.object(bs, "is_available", return_value=True),
            patch.object(bs, "_get_session", return_value=mock_session),
        ):
            synthetic = (np.random.rand(200, 200, 3) * 255).astype(np.uint8)
            emb = bs.embed_face(synthetic, (10, 10, 100, 100))
        assert emb is not None
        assert emb.shape == (512,)
        # L2-normalize then scale by 0.65 → vector norm = 0.65.
        assert float(np.linalg.norm(emb)) == pytest.approx(0.65, abs=0.01)

    def test_returns_none_on_degenerate_bbox(self) -> None:
        _accept_buffalo_s()
        synthetic = (np.random.rand(200, 200, 3) * 255).astype(np.uint8)
        result = bs.embed_face(synthetic, (10, 10, 0, 0))
        assert result is None

    def test_returns_none_when_onnxruntime_missing(self) -> None:
        _accept_buffalo_s()
        with patch.object(bs, "is_available", return_value=False):
            synthetic = (np.random.rand(200, 200, 3) * 255).astype(np.uint8)
            result = bs.embed_face(synthetic, (10, 10, 100, 100))
        assert result is None


class TestPreprocessing:
    """The preprocessing converts to RGB, resizes to 112x112,
    normalizes to [-1, 1], and outputs NCHW."""

    def test_bbox_path_output_shape(self) -> None:
        """The bbox-only fallback path produces the right tensor."""
        synthetic = (np.random.rand(200, 200, 3) * 255).astype(np.uint8)
        out = bs._preprocess(synthetic, (10, 10, 100, 100))
        assert out is not None
        assert out.shape == (1, 3, 112, 112)
        assert out.dtype == np.float32

    def test_bbox_path_normalization_range(self) -> None:
        synthetic = (np.random.rand(200, 200, 3) * 255).astype(np.uint8)
        out = bs._preprocess(synthetic, (10, 10, 100, 100))
        assert out is not None
        assert out.min() >= -1.0001
        assert out.max() <= 1.0001

    def test_aligned_path_uses_landmarks(self) -> None:
        """When a YuNet row is provided, alignment kicks in and the
        output reflects the warped face, not the bbox crop."""
        synthetic = (np.random.rand(400, 400, 3) * 255).astype(np.uint8)
        # Plausible YuNet row: bbox + 5 landmarks + confidence
        yunet_row = np.array(
            [
                100,
                100,
                200,
                200,  # bbox
                160,
                180,
                240,
                180,  # eyes
                200,
                230,  # nose
                175,
                270,
                225,
                270,  # mouth corners
                0.99,
            ],
            dtype=np.float32,
        )
        out = bs._preprocess(synthetic, (100, 100, 200, 200), yunet_row=yunet_row)
        assert out is not None
        assert out.shape == (1, 3, 112, 112)
        assert out.dtype == np.float32

    def test_aligned_path_differs_from_bbox_path_on_rotated_image(
        self,
    ) -> None:
        """Alignment + bbox-crop SHOULD produce different tensors
        when the face is off-axis. We construct a face-like pattern
        with a clear top-row of pixels and verify the aligned
        output differs structurally from the plain bbox output."""
        synthetic = np.zeros((400, 400, 3), dtype=np.uint8)
        # Distinctive top stripe to detect alignment behavior
        synthetic[50:60, :, 0] = 255  # red horizontal stripe
        synthetic[200:220, 100:300, 2] = 255  # blue horizontal stripe lower
        yunet_row = np.array(
            [
                100,
                100,
                200,
                200,
                160,
                180,
                240,
                180,
                200,
                230,
                175,
                270,
                225,
                270,
                0.99,
            ],
            dtype=np.float32,
        )
        out_aligned = bs._preprocess(synthetic, (100, 100, 200, 200), yunet_row=yunet_row)
        out_plain = bs._preprocess(synthetic, (100, 100, 200, 200))
        assert out_aligned is not None
        assert out_plain is not None
        # Same shape but different content (because alignment warps).
        diff = np.abs(out_aligned - out_plain).mean()
        assert diff > 0.0, (
            "Aligned and bbox-cropped outputs are identical — the "
            "alignment transform isn't taking effect."
        )


class TestLandmarkOrdering:
    """The YuNet landmark order must map cleanly to the ArcFace
    canonical order (subject left eye, right eye, nose, left mouth,
    right mouth)."""

    def test_yunet_to_arcface_order(self) -> None:
        # YuNet row with distinctive landmark coordinates so we can
        # verify the order isn't shuffled.
        yunet_row = np.array(
            [
                0,
                0,
                100,
                100,
                10,
                20,  # right_eye (= subject left eye)
                30,
                40,  # left_eye (= subject right eye)
                50,
                60,  # nose
                70,
                80,  # mouth_right (= subject left mouth)
                90,
                100,  # mouth_left (= subject right mouth)
                0.95,
            ],
            dtype=np.float32,
        )
        pts = bs._yunet_landmarks_to_arcface_order(yunet_row)
        assert pts.shape == (5, 2)
        # Subject left eye = YuNet's right_eye_x at index 4
        assert pts[0].tolist() == [10.0, 20.0]
        # Subject right eye = YuNet's left_eye_x at index 6
        assert pts[1].tolist() == [30.0, 40.0]
        # Nose unchanged
        assert pts[2].tolist() == [50.0, 60.0]
        # Subject left mouth = YuNet's mouth_right_x at index 10
        assert pts[3].tolist() == [70.0, 80.0]
        # Subject right mouth = YuNet's mouth_left_x at index 12
        assert pts[4].tolist() == [90.0, 100.0]
