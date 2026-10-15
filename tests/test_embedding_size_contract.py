"""A stored face embedding's valid size is its embedder's declared size —
on write, in the startup cleanup and on read.

Before: only 128-number embeddings survived anywhere, so buffalo_s (512)
and any other-size plugin embedder lost every face (dropped on write,
deleted at startup, rejected on read).
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import numpy as np
import pytest

from bpp.scoring.face_embedder_registry import _REGISTRY, FaceEmbedder, register_embedder

_PLUGIN = "__size_plugin__"
_PLUGIN_ID = "openai_clip_vit_b32_onnx"  # MIT, no click-through


@pytest.fixture
def plugin64():
    register_embedder(
        FaceEmbedder(
            name=_PLUGIN,
            embed=lambda img, box: np.arange(1, 65, dtype=np.float64),
            embedding_dim=64,
            registry_id=_PLUGIN_ID,
        )
    )
    yield _PLUGIN
    _REGISTRY._store.pop(_PLUGIN, None)


# ── write ─────────────────────────────────────────────────────────────


def test_write_validation_uses_the_methods_size(plugin64):
    from bpp.web.face_worker import _validate_embedding

    assert _validate_embedding(np.ones(512, np.float32), method="buffalo_s")
    assert _validate_embedding(np.ones(64, np.float32), method=_PLUGIN)
    assert _validate_embedding(np.ones(128, np.float32), method="sface")
    assert _validate_embedding(np.ones(128, np.float32), method=None)
    assert not _validate_embedding(np.ones(64, np.float32), method="sface")


def test_plugin_faces_reach_the_database(plugin64):
    """End to end through phase 5: a 64-number plugin embedding is
    stored, tagged with the plugin's registry id."""
    from bpp.web import face_extraction_phases as phases
    from bpp.web.face_worker import _validate_embedding
    from tests.test_face_extraction_phases import _build_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _build_schema(conn)
    if "producing_model_id" not in {
        r[1] for r in conn.execute("PRAGMA table_info(face_embeddings)")
    }:
        conn.execute("ALTER TABLE face_embeddings ADD COLUMN producing_model_id TEXT")
    emb = np.ones(64, dtype=np.float32)
    out = phases.extract_new_embeddings(
        conn,
        phases.ExtractionPartition(cached_records=[], need_extract=[(0, "/a.jpg", 1)]),
        phases.PreExtractSnapshot(
            stored_method=None,
            method_changed=False,
            old_cluster_photos=None,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        ),
        max_long_side=1024,
        face_confidence=0.3,
        config={"_face_extract_workers": 1, "_face_extract_pool": "thread"},
        extract_one_fn=lambda *_a, **_k: [
            {"bbox": (5, 5, 20, 20), "embedding": emb, "quality": 0.8}
        ],
        validate_bbox_fn=lambda bx, by, bw, bh: (bx, by, bw, bh),
        validate_embedding_fn=_validate_embedding,
        progress_callback=None,
        cancellation_check=None,
        progress_total=1,
        method=_PLUGIN,
    )
    assert out.extracted_count == 1
    row = conn.execute(
        "SELECT length(embedding), producing_model_id FROM face_embeddings"
    ).fetchone()
    assert tuple(row) == (256, _PLUGIN_ID)


# ── startup cleanup ───────────────────────────────────────────────────


def test_startup_cleanup_keeps_valid_sizes_per_producer(tmp_path):
    from bpp.db.integrity import prune_corrupt_face_embeddings

    db = tmp_path / "lib.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE face_embeddings (id INTEGER PRIMARY KEY, photo_id INTEGER,"
        " face_index INTEGER, embedding BLOB, producing_model_id TEXT)"
    )
    rows = [
        (np.ones(128, np.float32).tobytes(), "sface_yunet"),  # keep
        (np.ones(128, np.float32).tobytes(), None),  # legacy 128: keep
        (np.ones(512, np.float32).tobytes(), "insightface_buffalo_s"),  # keep
        (np.ones(16, np.float32).tobytes(), "some_plugin_model"),  # unknown producer: keep
        (np.ones(512, np.float32).tobytes(), "sface_yunet"),  # wrong for sface: drop
        (np.ones(256, np.float32).tobytes(), None),  # legacy garbage: drop
        (b"\x00" * 7, "some_plugin_model"),  # not float32-sized: drop
    ]
    conn.executemany(
        "INSERT INTO face_embeddings (photo_id, face_index, embedding, producing_model_id)"
        " VALUES (1, 0, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    assert prune_corrupt_face_embeddings(str(db)) == 3
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 4


# ── read ──────────────────────────────────────────────────────────────


def test_batch_read_keeps_the_active_methods_size_not_the_majority():
    from bpp.db.face_embedding_safety import decode_embeddings_filtered

    rows = [(i, np.ones(128, np.float32).tobytes()) for i in range(3)]
    rows.append((9, np.ones(512, np.float32).tobytes()))
    ids, embs = decode_embeddings_filtered(rows, where="t", dim=512)
    assert ids == [9]
    assert embs[0].shape == (512,)


def test_active_dim_follows_the_setting(plugin64):
    from bpp.db.face_embedding_safety import active_embedding_dim

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    assert active_embedding_dim(conn) == 128
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'buffalo_s')")
    assert active_embedding_dim(conn) == 512
    conn.execute("UPDATE settings SET value=? WHERE key='face_embedding_method'", (_PLUGIN,))
    assert active_embedding_dim(conn) == 64


def test_allowed_sizes_are_computed_once_per_batch():
    from bpp.db import face_embedding_safety as fes

    rows = [(i, np.ones(128, np.float32).tobytes()) for i in range(50)]
    with patch.object(fes, "_allowed_byte_sizes", wraps=fes._allowed_byte_sizes) as sizes:
        fes.decode_embeddings_filtered(rows, where="t")
    assert sizes.call_count == 1


# ── plugin embeddings are checked and normalised ─────────────────────


def _one_face(plugin_name):
    from bpp.scoring import face_embed, face_embed_buffalo

    row = np.array([20, 20, 40, 40] + [0] * 10 + [0.99], dtype=np.float32)
    with (
        patch("bpp.scoring.face_embed_detect.detect_face_rows", return_value=[row]),
        patch("bpp.scoring.face_embed_detect.detect_inverted_face", return_value=None),
        patch.object(face_embed_buffalo, "_validate_yunet_landmarks", return_value=True),
        patch.object(face_embed_buffalo, "_face_quality_from_landmarks", return_value=0.9),
    ):
        return face_embed.extract_face_embeddings(
            np.zeros((96, 96, 3), np.uint8), method=plugin_name
        )


def test_plugin_embedding_is_normalised_like_sface(plugin64):
    from bpp.scoring.face_embed_sface_runtime import SFACE_DISTANCE_SCALE

    (face,) = _one_face(_PLUGIN)
    assert np.linalg.norm(face["embedding"]) == pytest.approx(SFACE_DISTANCE_SCALE, rel=1e-5)


@pytest.mark.parametrize(
    "bad", [np.ones(32), np.array([np.nan] * 64), np.zeros(64)], ids=["size", "nan", "zero"]
)
def test_bad_plugin_vectors_are_dropped(bad):
    register_embedder(
        FaceEmbedder(
            name="__bad__", embed=lambda img, box: bad, embedding_dim=64, registry_id=_PLUGIN_ID
        )
    )
    try:
        assert _one_face("__bad__") == []
    finally:
        _REGISTRY._store.pop("__bad__", None)


def test_plugin_with_unknown_registry_id_warns_once_and_finds_nothing():
    import logging

    from bpp.scoring import model_load_gate

    model_load_gate._reset_blocked_log_for_tests()
    register_embedder(
        FaceEmbedder(
            name="__typo__",
            embed=lambda img, box: np.ones(64),
            embedding_dim=64,
            registry_id="no_such_entry",
        )
    )
    try:
        gate_log = logging.getLogger("bpp.scoring.model_load_gate")
        with patch.object(gate_log, "warning") as warn:
            assert _one_face("__typo__") == []
            assert _one_face("__typo__") == []
        assert len([c for c in warn.call_args_list if "__typo__" in str(c.args)]) == 1
    finally:
        _REGISTRY._store.pop("__typo__", None)


# ── plugins load where faces are extracted ───────────────────────────


def test_extraction_worker_loads_plugins_once():
    import bpp.plugins
    from bpp.web import face_worker

    bpp.plugins._loaded_once = False
    with (
        patch("bpp.plugins.load_plugin_entry_points") as load,
        patch.object(face_worker, "load_and_downscale", return_value=None),
    ):
        face_worker._extract_one("/a.jpg", 512)
        face_worker._extract_one("/b.jpg", 512)
    assert load.call_count == 1


# ── built-in names are protected ─────────────────────────────────────


def test_plugin_cannot_take_a_builtin_embedder_name():
    import bpp.scoring.face_embed  # noqa: F401

    with pytest.raises(ValueError, match="built-in"):
        register_embedder(
            FaceEmbedder(
                name="sface", embed=lambda i, b: None, embedding_dim=128, registry_id=_PLUGIN_ID
            )
        )


def test_plugin_cannot_take_a_builtin_detector_name():
    import bpp.scoring.face_pipeline  # noqa: F401
    from bpp.scoring.face_detector_registry import FaceDetector, register_detector

    with pytest.raises(ValueError, match="built-in"):
        register_detector(
            FaceDetector(name="yunet", detect=lambda i, c: [], registry_id=_PLUGIN_ID)
        )


# ── switching embedders (review follow-up) ───────────────────────────


def _phase_conn():
    from tests.test_face_extraction_phases import _build_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _build_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(face_embeddings)")}
    if "producing_model_id" not in cols:
        conn.execute("ALTER TABLE face_embeddings ADD COLUMN producing_model_id TEXT")
    return conn


def _add_row(conn, pid, dim, producer, cluster=5):
    conn.execute(
        "INSERT INTO face_embeddings (photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h,"
        " embedding, quality, cluster_id, producing_model_id) VALUES (?,0,0,0,10,10,?,0.5,?,?)",
        (pid, np.ones(dim, np.float32).tobytes(), cluster, producer),
    )


def test_switch_saved_through_settings_wipes_rows_from_the_old_embedder():
    """The picker saves the new method before analysis runs, so the
    setting already equals the current method — the rows decide."""
    from bpp.web import face_extraction_phases as phases

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'buffalo_s')")
    _add_row(conn, 1, 128, "sface_yunet")
    _, changed, snapshot = phases.reconcile_method(conn, "buffalo_s")
    assert changed is True and snapshot == {5: {1}}
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 0


def test_untagged_legacy_rows_of_the_right_size_are_kept():
    from bpp.web import face_extraction_phases as phases

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'sface')")
    _add_row(conn, 1, 128, None)
    _, changed, _ = phases.reconcile_method(conn, "sface")
    assert changed is False
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 1


def test_readers_follow_the_rows_not_the_pending_choice():
    """Between picking a new embedder and the next analysis, the stored
    rows are still the old embedder's — the People panel must keep
    reading them."""
    from bpp.db.face_embedding_safety import active_embedding_dim
    from bpp.web import face_extraction_phases as phases

    conn = _phase_conn()
    phases.reconcile_method(conn, "sface")  # analysis ran with SFace
    conn.execute("UPDATE settings SET value='buffalo_s' WHERE key='face_embedding_method'")
    assert active_embedding_dim(conn) == 128


def test_photo_whose_cached_rows_are_all_unreadable_is_re_extracted():
    from bpp.web import face_extraction_phases as phases

    part = phases.partition_cached_vs_extract(
        [{"filepath": "/a.jpg"}],
        {"/a.jpg": 1},
        {1: [(0, np.ones(128, np.float32).tobytes())]},
        frozenset(),
        dim=512,
    )
    assert part.cached_records == []
    assert part.need_extract == [(0, "/a.jpg", 1)]


def test_plugin_not_loaded_stops_face_analysis_without_wiping():
    from bpp.errors import BppError
    from bpp.web.face_phase_classes import MethodReconcilerPhase

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', '__gone_plugin__')")
    _add_row(conn, 1, 64, "some_plugin_model")

    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.conn = conn
    with pytest.raises(BppError) as exc:
        MethodReconcilerPhase().run(ctx)
    assert "__gone_plugin__" in exc.value.user_message
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 1


def test_unknown_method_in_a_worker_finds_no_faces_instead_of_sface():
    from bpp.scoring import face_embed

    face_embed._unknown_method_warned.discard("__nope__")
    with patch.object(face_embed.log, "warning") as warn:
        assert (
            face_embed.extract_face_embeddings(np.zeros((32, 32, 3), np.uint8), method="__nope__")
            == []
        )
    assert any("__nope__" in str(c.args) for c in warn.call_args_list)


def test_decode_rows_keeps_only_the_library_size():
    from bpp.db.face_embedding_safety import decode_rows

    conn = _phase_conn()
    blobs = [np.ones(128, np.float32).tobytes(), np.ones(512, np.float32).tobytes()]
    kept = decode_rows(conn, [(1, blobs[0]), (2, blobs[1])], where="t")
    assert [i for i, _ in kept] == [1]


def test_plugin_box_off_the_top_left_is_dropped():
    from bpp.scoring.face_detector_registry import (
        FaceDetector,
        register_detector,
        run_plugin_detectors,
    )

    register_detector(
        FaceDetector(
            name="__offimg__",
            detect=lambda img, c: [(-200, -200, 50, 50, 0.99), (10, 10, 20, 20, 0.9)],
            registry_id=_PLUGIN_ID,
        )
    )
    faces = run_plugin_detectors(np.zeros((100, 100, 3), np.uint8), 0.3, {}, max_detections=10)
    assert faces == [(10, 10, 20, 20, 0.9)]


def test_plugin_returning_none_is_not_reported_as_broken():
    from bpp.scoring import face_embed

    register_embedder(
        FaceEmbedder(
            name="__none__", embed=lambda img, box: None, embedding_dim=64, registry_id=_PLUGIN_ID
        )
    )
    try:
        with patch.object(face_embed.log, "warning") as warn:
            assert _one_face("__none__") == []
        assert not [c for c in warn.call_args_list if "unusable vector" in str(c.args)]
    finally:
        _REGISTRY._store.pop("__none__", None)


@pytest.mark.no_preaccept_permissive
def test_blocked_plugin_check_is_cached():
    from bpp.registry.policy import ModelLoadBlockedError, ModelLoadDecision, PolicyResult
    from bpp.scoring.model_load_gate import license_allows

    blocked = ModelLoadBlockedError(
        PolicyResult(
            decision=ModelLoadDecision.BLOCKED_NEEDS_ACK,
            reason="x",
            entry_id="insightface_scrfd_25g",
        )
    )
    with patch("bpp.registry.enforce_load_policy_for", side_effect=blocked) as check:
        for _ in range(20):
            assert license_allows("insightface_scrfd_25g", feature="t") is False
    assert check.call_count == 1


def test_rows_tagged_with_the_bare_method_name_are_not_wiped():
    """Older versions tagged rows 'sface' instead of 'sface_yunet' —
    upgrading must not wipe a user's faces."""
    from bpp.web import face_extraction_phases as phases

    conn = _phase_conn()
    conn.execute("INSERT INTO settings VALUES ('face_embedding_method', 'sface')")
    _add_row(conn, 1, 128, "sface")
    _add_row(conn, 2, 128, "sface_yunet")
    _, changed, _ = phases.reconcile_method(conn, "sface")
    assert changed is False
    assert conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0] == 2


def test_float64_embeddings_are_stored_as_float32():
    from bpp.web import face_extraction_phases as phases
    from bpp.web.face_worker import _validate_embedding

    conn = _phase_conn()
    phases.extract_new_embeddings(
        conn,
        phases.ExtractionPartition(cached_records=[], need_extract=[(0, "/a.jpg", 1)]),
        phases.PreExtractSnapshot(
            stored_method=None,
            method_changed=False,
            old_cluster_photos=None,
            stale_photo_ids=frozenset(),
            dismissed_slots=frozenset(),
        ),
        max_long_side=1024,
        face_confidence=0.3,
        config={"_face_extract_workers": 1, "_face_extract_pool": "thread"},
        extract_one_fn=lambda *_a, **_k: [
            {"bbox": (5, 5, 20, 20), "embedding": np.ones(128), "quality": 0.8}
        ],
        validate_bbox_fn=lambda bx, by, bw, bh: (bx, by, bw, bh),
        validate_embedding_fn=_validate_embedding,
        progress_callback=None,
        cancellation_check=None,
        progress_total=1,
        method="sface",
    )
    assert conn.execute("SELECT length(embedding) FROM face_embeddings").fetchone()[0] == 512
