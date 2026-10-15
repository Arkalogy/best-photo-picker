"""Face detectors and embedders registered by plugins actually run."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from bpp.scoring import face_pipeline
from bpp.scoring.face_detector_registry import _REGISTRY as _DET_REGISTRY
from bpp.scoring.face_detector_registry import FaceDetector, register_detector
from bpp.scoring.face_embedder_registry import _REGISTRY as _EMB_REGISTRY
from bpp.scoring.face_embedder_registry import FaceEmbedder, register_embedder

#: MIT, no click-through — a plugin model the gate lets through.
_FREE_ID = "openai_clip_vit_b32_onnx"
#: Research-only and not accepted in tests — the gate refuses it.
_BLOCKED_ID = "insightface_scrfd_25g"
_BOX = (10, 12, 30, 30, 0.95)


@pytest.fixture
def plugin_detector():
    calls: list = []

    def _register(registry_id=_FREE_ID, toggle_key=None, fail=False):
        def detect(image, min_confidence):
            calls.append(min_confidence)
            if fail:
                raise RuntimeError("plugin bug")
            return [_BOX]

        register_detector(
            FaceDetector(
                name="__plugin_det__",
                detect=detect,
                registry_id=registry_id,
                toggle_key=toggle_key,
            )
        )
        return calls

    yield _register
    _DET_REGISTRY._store.pop("__plugin_det__", None)


def _blank():
    return np.zeros((96, 96, 3), dtype=np.uint8)


def test_plugin_detector_runs_and_its_faces_are_kept(plugin_detector):
    calls = plugin_detector()
    faces, _ = face_pipeline._collect_detections(_blank(), 0.3, {})
    assert calls
    assert _BOX in faces


def test_plugin_detector_respects_its_toggle(plugin_detector):
    calls = plugin_detector(toggle_key="model_plugin_det")
    face_pipeline._collect_detections(_blank(), 0.3, {"model_plugin_det": False})
    assert calls == []


@pytest.mark.no_preaccept_permissive
def test_plugin_detector_waits_for_its_license(plugin_detector):
    calls = plugin_detector(registry_id=_BLOCKED_ID)
    face_pipeline._collect_detections(_blank(), 0.3, {})
    assert calls == []


def test_crashing_plugin_detector_warns_once_and_keeps_builtins(plugin_detector):
    # Count calls on the module logger itself: caplog depends on logger
    # levels that an earlier test's logging setup may have changed.
    from bpp.scoring import face_detector_registry as reg

    plugin_detector(fail=True)
    reg._reset_plugin_warnings_for_tests()
    builtin = [(1, 2, 3, 4, 0.9)]
    with (
        patch.object(face_pipeline, "_collect_builtin_detections", return_value=(builtin, True)),
        patch.object(reg._log, "warning") as warn,
    ):
        faces, upright = face_pipeline._collect_detections(_blank(), 0.3, {})
        face_pipeline._collect_detections(_blank(), 0.3, {})
    assert faces == builtin and upright is True
    assert [c.args[1] for c in warn.call_args_list] == ["__plugin_det__"]


@pytest.fixture
def plugin_embedder():
    register_embedder(
        FaceEmbedder(
            name="__plugin_emb__",
            embed=lambda image, box: np.ones(64, dtype=np.float64),
            embedding_dim=64,
            registry_id=_FREE_ID,
        )
    )
    yield "__plugin_emb__"
    _EMB_REGISTRY._store.pop("__plugin_emb__", None)


def test_selected_plugin_embedder_produces_its_embeddings(plugin_embedder):
    from bpp.scoring import face_embed, face_embed_buffalo

    row = np.array([20, 20, 40, 40] + [0] * 10 + [0.99], dtype=np.float32)
    with (
        patch("bpp.scoring.face_embed_detect.detect_face_rows", return_value=[row]),
        patch("bpp.scoring.face_embed_detect.detect_inverted_face", return_value=None),
        patch.object(face_embed_buffalo, "_validate_yunet_landmarks", return_value=True),
        patch.object(face_embed_buffalo, "_face_quality_from_landmarks", return_value=0.9),
    ):
        results = face_embed.extract_face_embeddings(_blank(), method=plugin_embedder)
    assert len(results) == 1
    assert results[0]["embedding"].shape == (64,)
    assert results[0]["embedding"].dtype == np.float32
    assert results[0]["bbox"] == (20, 20, 40, 40)


def test_stored_model_id_comes_from_the_embedder(plugin_embedder):
    from bpp.scoring.face_embed import producing_model_id_for

    assert producing_model_id_for(plugin_embedder) == _FREE_ID


def test_setting_naming_a_plugin_embedder_selects_it(plugin_embedder):
    import sqlite3

    from bpp.scoring.face_embed import embedding_method

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', ?)", (plugin_embedder,))
    assert embedding_method(conn) == plugin_embedder


def test_stored_embeddings_of_any_registered_size_decode(plugin_embedder):
    """buffalo_s (512) and plugin embeddings were rejected on read as
    "corrupt" because only 128-number embeddings were accepted."""
    from bpp.db.face_embedding_safety import decode_embedding

    for dim in (128, 512, 64):
        arr = decode_embedding(np.ones(dim, np.float32).tobytes(), where="t")
        assert arr is not None and arr.shape == (dim,), dim
    # A size no embedder produces is still treated as corrupt.
    assert decode_embedding(np.ones(256, np.float32).tobytes(), where="t") is None


def test_mixed_sizes_in_one_batch_keep_the_majority_size():
    """np.stack over mixed sizes crashed the People panel once; a batch
    keeps only the dominant size."""
    from bpp.db.face_embedding_safety import decode_embeddings_filtered

    rows = [(1, np.ones(128, np.float32).tobytes()), (2, np.ones(128, np.float32).tobytes())]
    rows.append((3, np.ones(512, np.float32).tobytes()))
    ids, embs = decode_embeddings_filtered(rows, where="t")
    assert ids == [1, 2]
    np.stack(embs)
