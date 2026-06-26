"""Tests for Protection A — defense against corrupt face embedding BLOBs.

What this pins
--------------
The Jun-2 demo lib incident: 136 face_embeddings rows had 1024-byte
BLOBs (256-d garbage) interleaved with 3,838 valid 512-byte rows
(128-d float32). ``GET /api/v1/faces/clusters`` did

    np.stack([np.frombuffer(b, dtype=np.float32) for b in blobs])

and crashed with ``ValueError: all input arrays must have the same
shape``, taking the sidebar and People panel with it.

``decode_embedding`` is the contract enforcer. These tests pin:
1. The decoder returns ``None`` for every corruption shape we've seen
   (wrong size, NaN, inf, zero-norm, None).
2. The batch helper drops bad rows but preserves IDs of survivors.
3. Counters tick once per category so /health can surface drift.
4. The defense-in-depth shape filter in ``face_cluster.py`` drops
   minority shapes before ``np.stack`` so the cluster pass survives
   even if a bad row slips past the read-boundary check.
"""

from __future__ import annotations

import numpy as np
import pytest

from bpp.db.face_embedding_safety import (
    EMBEDDING_DIM,
    decode_embedding,
    decode_embeddings_filtered,
    get_counters,
    reset_counters_for_tests,
    reset_log_state_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_counters():
    reset_counters_for_tests()
    reset_log_state_for_tests()
    yield


def _valid_blob() -> bytes:
    """A unit-norm 128-d float32 — the contract."""
    rng = np.random.default_rng(seed=42)
    v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
    return (v / np.linalg.norm(v)).tobytes()


class TestDecodeEmbedding:
    def test_valid_blob_round_trips(self) -> None:
        arr = decode_embedding(_valid_blob(), where="t.valid")
        assert arr is not None
        assert arr.shape == (EMBEDDING_DIM,)
        assert arr.dtype == np.float32

    def test_none_blob_returns_none(self) -> None:
        assert decode_embedding(None, where="t.none") is None
        assert get_counters()["decoded_bad_size"] == 1

    def test_wrong_size_returns_none(self) -> None:
        """The exact failure shape from Jun-2: 256-d instead of 128-d."""
        blob = np.random.rand(256).astype(np.float32).tobytes()
        assert decode_embedding(blob, where="t.256d") is None
        assert get_counters()["decoded_bad_size"] == 1

    def test_undersize_blob_returns_none(self) -> None:
        """Truncated write — half the bytes the contract requires."""
        blob = np.random.rand(64).astype(np.float32).tobytes()
        assert decode_embedding(blob, where="t.short") is None
        assert get_counters()["decoded_bad_size"] == 1

    def test_nan_values_returns_none(self) -> None:
        blob = np.full(EMBEDDING_DIM, np.nan, dtype=np.float32).tobytes()
        assert decode_embedding(blob, where="t.nan") is None
        assert get_counters()["decoded_non_finite"] == 1

    def test_inf_values_returns_none(self) -> None:
        """Matches the Jun-2 corruption — bytes interpreted as float32
        produced norm=inf (values at 2^65)."""
        blob = np.full(EMBEDDING_DIM, np.inf, dtype=np.float32).tobytes()
        assert decode_embedding(blob, where="t.inf") is None
        assert get_counters()["decoded_non_finite"] == 1

    def test_zero_norm_returns_none(self) -> None:
        blob = np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes()
        assert decode_embedding(blob, where="t.zero") is None
        assert get_counters()["decoded_zero_norm"] == 1

    def test_counters_persist_across_calls(self) -> None:
        decode_embedding(_valid_blob(), where="t.a")
        decode_embedding(None, where="t.b")
        decode_embedding(np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes(), where="t.c")
        c = get_counters()
        assert c["decoded_ok"] == 1
        assert c["decoded_bad_size"] == 1
        assert c["decoded_zero_norm"] == 1


class TestSkipWarningLogging:
    """P-03: skip warnings carry the row_id on first occurrence per
    (where, reason) pair, and are rate-limited so a library with
    thousands of bad rows doesn't drown the server log."""

    def test_first_warning_includes_row_id(self, caplog: pytest.LogCaptureFixture) -> None:
        bad = np.random.rand(256).astype(np.float32).tobytes()
        with caplog.at_level("WARNING", logger="bpp.db.face_embedding_safety"):
            decode_embedding(bad, where="t.first", row_id=4242)
        msgs = [r.getMessage() for r in caplog.records]
        assert any("row_id=4242" in m for m in msgs), msgs
        assert any("bad_size" in m for m in msgs), msgs

    def test_repeats_are_rate_limited(self, caplog: pytest.LogCaptureFixture) -> None:
        """Same (where, reason) repeated 99 times → one log line, not 99.
        On the 100th, the summary line appears with the suppressed count."""
        bad = np.random.rand(256).astype(np.float32).tobytes()
        with caplog.at_level("WARNING", logger="bpp.db.face_embedding_safety"):
            for i in range(100):
                decode_embedding(bad, where="t.flood", row_id=i)
        msgs = [r.getMessage() for r in caplog.records if "t.flood" in r.getMessage()]
        # Exactly two emits: the first row + the 100th summary line.
        assert len(msgs) == 2, f"expected 2 lines, got {len(msgs)}: {msgs}"
        # First line names row_id=0 (the offending row that fired it).
        assert "row_id=0" in msgs[0]
        # Summary names how many were suppressed since.
        assert "skipped 99 more rows" in msgs[1]

    def test_different_reasons_track_independently(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A bad_size row and a zero_norm row are independent reasons —
        each gets its own first-occurrence warning."""
        bad_size = np.random.rand(256).astype(np.float32).tobytes()
        zero = np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes()
        with caplog.at_level("WARNING", logger="bpp.db.face_embedding_safety"):
            decode_embedding(bad_size, where="t.mix", row_id="a")
            decode_embedding(zero, where="t.mix", row_id="b")
        msgs = [r.getMessage() for r in caplog.records if "t.mix" in r.getMessage()]
        assert len(msgs) == 2
        assert any("bad_size" in m and "row_id='a'" in m for m in msgs)
        assert any("zero_norm" in m and "row_id='b'" in m for m in msgs)


class TestDecodeEmbeddingsFiltered:
    def test_drops_bad_rows_preserves_ids_of_survivors(self) -> None:
        good_a = _valid_blob()
        # Generate a second distinct valid blob for ID-tracking confidence.
        rng = np.random.default_rng(seed=99)
        v = rng.standard_normal(EMBEDDING_DIM).astype(np.float32)
        good_b = (v / np.linalg.norm(v)).tobytes()
        bad_size = np.random.rand(256).astype(np.float32).tobytes()
        zero = np.zeros(EMBEDDING_DIM, dtype=np.float32).tobytes()

        items = [
            ("a", good_a),
            ("bad-size", bad_size),
            ("b", good_b),
            ("zero", zero),
        ]
        ids, embs = decode_embeddings_filtered(items, where="t.batch")
        assert ids == ["a", "b"]
        assert len(embs) == 2
        assert all(e.shape == (EMBEDDING_DIM,) for e in embs)

    def test_all_bad_returns_empty_lists(self) -> None:
        items = [
            (1, None),
            (2, np.random.rand(64).astype(np.float32).tobytes()),
        ]
        ids, embs = decode_embeddings_filtered(items, where="t.all-bad")
        assert ids == []
        assert embs == []

    def test_empty_input_returns_empty_lists(self) -> None:
        ids, embs = decode_embeddings_filtered([], where="t.empty")
        assert ids == []
        assert embs == []


class TestFilterToMajorityShapeDefenseInDepth:
    """``face_cluster._filter_to_majority_shape`` is the last line of
    defense — if a bad embedding somehow bypasses the read-boundary
    check (e.g., a caller bypasses ``decode_embedding`` entirely), the
    shape filter drops minority shapes BEFORE ``np.stack`` so cluster
    operations survive instead of 500-ing the whole endpoint.

    This was the Jun-2 incident's actual crash site."""

    def test_homogeneous_input_passes_through(self) -> None:
        from bpp.scoring.face_cluster import _filter_to_majority_shape

        embs = [np.ones(128, dtype=np.float32) for _ in range(5)]
        out = _filter_to_majority_shape(embs, where="t")
        assert out is embs  # same list when no filter needed

    def test_mixed_shapes_drops_minority(self) -> None:
        from bpp.scoring.face_cluster import _filter_to_majority_shape

        embs = [np.ones(128, dtype=np.float32) for _ in range(3)] + [
            np.ones(256, dtype=np.float32) for _ in range(1)
        ]
        out = _filter_to_majority_shape(embs, where="t.mixed")
        assert len(out) == 3
        assert all(e.shape == (128,) for e in out)

    def test_cluster_faces_survives_mixed_input(self) -> None:
        """The full crash repro: cluster_faces called with mixed shapes.
        Pre-Protection-A this raised ValueError; now it should cluster
        the majority and log the minority away."""
        from bpp.scoring.face_cluster import cluster_faces

        good = [np.random.RandomState(s).rand(128).astype(np.float32) for s in range(10)]
        bad = [np.full(256, 1e30, dtype=np.float32)]
        labels = cluster_faces(good + bad, threshold=0.6)
        # Got SOME labels back instead of a crash, and len matches the
        # majority shape's count (one bad row dropped).
        assert isinstance(labels, list)
        assert len(labels) == len(good)
