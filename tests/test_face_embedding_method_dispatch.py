"""Tests for the embedder method dispatch (Bug A) and the float32
storage contract on the dlib path (Bug B).

Both bugs were found while re-extracting the demo lib looking for the
"Jackie Dude on dozens of unrelated faces" symptom:

* **Bug A — decorative setting.** ``embedding_method()`` used to ignore
  the ``face_embedding_method`` setting entirely and pick by model
  availability alone, then *write* the picked name back to the DB. The
  setting looked like a toggle but had no path from setting → choice
  anywhere in the code; the worker ran whatever the loader could
  initialise. A user who picked ``dlib`` got SFace silently.

* **Bug B — float32 contract violated on dlib fallback path.**
  ``face_recognition.face_encodings`` returns ``float64``. Two call
  sites (``_extract_dlib`` and ``_supplement_with_scrfd``) appended the
  encoding to results without casting to ``float32``. Storage
  serialised as 1024 bytes (128 doubles), Protection A's read-side
  decoder expects 512 bytes (128 floats), so the rows were silently
  dropped at every read. On the demo lib this lost ~3.4% of faces
  (136 / 3974) — every photo where YuNet missed and dlib found.

These tests pin both fixes at the unit level so a future refactor
can't quietly re-introduce them.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import numpy as np
import pytest

# ── Bug A: embedding_method honors the face_embedding_method setting ──


def _make_settings_db(value: str | None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    if value is not None:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('face_embedding_method', ?)",
            (value,),
        )
        conn.commit()
    return conn


class TestEmbeddingMethodHonorsSetting:
    """The toggle in ``settings.face_embedding_method`` must actually
    select the embedder. Pre-fix it was decorative — write-only."""

    def test_dlib_preference_returns_dlib_even_when_sface_loadable(self) -> None:
        from bpp.scoring import face_embed

        conn = _make_settings_db("dlib")
        # Pretend SFace IS loadable. The user picked dlib; honour it.
        with patch.object(face_embed, "_get_sface_recognizer", return_value=object()):
            assert face_embed.embedding_method(conn) == "dlib"

    def test_sface_preference_returns_sface_when_loadable(self) -> None:
        from bpp.scoring import face_embed

        conn = _make_settings_db("sface")
        with patch.object(face_embed, "_get_sface_recognizer", return_value=object()):
            assert face_embed.embedding_method(conn) == "sface"

    def test_sface_preference_falls_back_to_dlib_when_unavailable(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """User picked sface but the model isn't loadable. Don't silently
        run nothing — fall back to dlib AND log so the operator knows."""
        from bpp.scoring import face_embed

        conn = _make_settings_db("sface")
        with (
            patch.object(face_embed, "_get_sface_recognizer", return_value=None),
            caplog.at_level("WARNING", logger="bpp.scoring.face_embed"),
        ):
            assert face_embed.embedding_method(conn) == "dlib"
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= 30]
        assert any("sface" in m.lower() and "fall" in m.lower() for m in warnings), warnings

    def test_no_setting_falls_through_to_availability_check(self) -> None:
        """Empty / new DB: behaviour matches the pre-fix default
        (SFace if available, dlib otherwise)."""
        from bpp.scoring import face_embed

        conn = _make_settings_db(None)
        # SFace available → sface
        with patch.object(face_embed, "_get_sface_recognizer", return_value=object()):
            assert face_embed.embedding_method(conn) == "sface"
        # SFace unavailable → dlib
        with patch.object(face_embed, "_get_sface_recognizer", return_value=None):
            assert face_embed.embedding_method(conn) == "dlib"

    def test_no_conn_argument_preserves_legacy_behavior(self) -> None:
        """Callers that don't have a conn (rare; mostly test code) get
        the pre-fix availability-only behaviour. Keeps the back-compat
        promise tight — only DB-aware callers get the toggle."""
        from bpp.scoring import face_embed

        with patch.object(face_embed, "_get_sface_recognizer", return_value=object()):
            assert face_embed.embedding_method() == "sface"
        with patch.object(face_embed, "_get_sface_recognizer", return_value=None):
            assert face_embed.embedding_method() == "dlib"

    def test_unknown_setting_value_falls_through_to_availability(self) -> None:
        """Defensive: a typo or an unrecognised value in settings must
        not break extraction. Fall back to availability."""
        from bpp.scoring import face_embed

        conn = _make_settings_db("arcface-typo")
        with patch.object(face_embed, "_get_sface_recognizer", return_value=object()):
            assert face_embed.embedding_method(conn) == "sface"

    def test_broken_settings_table_does_not_crash(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If the settings table doesn't exist (mid-migration DB),
        the function should log + fall back to availability, not
        propagate sqlite3.OperationalError."""
        from bpp.scoring import face_embed

        conn = sqlite3.connect(":memory:")
        # No settings table created — the SELECT will raise.
        with (
            patch.object(face_embed, "_get_sface_recognizer", return_value=object()),
            caplog.at_level("WARNING", logger="bpp.scoring.face_embed"),
        ):
            assert face_embed.embedding_method(conn) == "sface"


# ── Bug B: dlib path stores float32, not float64 ──


class TestDlibPathStoresFloat32:
    """Pre-fix ``_extract_dlib`` and ``_supplement_with_scrfd`` returned
    ``face_recognition.face_encodings`` output verbatim (float64,
    1024 bytes per 128-d vector). The storage contract is float32
    (512 bytes), so Protection A's read-side decoder dropped every
    such row as "wrong size 1024." 3.4% of demo lib faces vanished
    silently. Cast at the call sites makes the storage contract
    uniform across embedders."""

    def _fake_face_recognition_module(self, encoding_dtype):
        """Return an object that quacks like the face_recognition
        module, returning one fixed encoding of the requested dtype.
        Lets us verify dtype-handling without invoking dlib."""

        class _Stub:
            @staticmethod
            def face_encodings(rgb, known_face_locations=None):
                return [np.ones(128, dtype=encoding_dtype)]

            @staticmethod
            def face_landmarks(rgb, locations=None, model="small"):
                # Minimal valid landmarks payload: one dict per
                # location with the keys the validator expects.
                return [
                    {
                        "left_eye": [(0, 0)],
                        "right_eye": [(0, 0)],
                        "nose_tip": [(0, 0)],
                    }
                    for _ in (locations or [])
                ]

        return _Stub()

    def test_extract_dlib_returns_float32_when_encoder_returns_float64(self) -> None:
        """Drive _extract_dlib by patching its dependencies. The
        returned embedding MUST be float32 regardless of what the
        encoder produced — that's the storage contract."""
        import sys

        from bpp.scoring import face as face_mod
        from bpp.scoring import face_embed_extractors

        fake_face_recognition = self._fake_face_recognition_module(np.float64)
        # Bbox with high quality so the path doesn't reject the face.
        fake_detection = [(100, 100, 200, 200, 0.95)]

        with (
            patch.dict(sys.modules, {"face_recognition": fake_face_recognition}),
            patch.object(
                face_mod,
                "detect_faces_with_confidence",
                return_value=fake_detection,
            ),
            patch.object(
                face_embed_extractors,
                "_validate_face_landmarks",
                return_value=True,
            ),
            patch.object(
                face_embed_extractors,
                "_dlib_face_quality",
                return_value=0.95,
            ),
        ):
            results = face_embed_extractors._extract_dlib(
                image=np.zeros((400, 400, 3), dtype=np.uint8),
                min_confidence=0.2,
                embedding_confidence=0.5,
                min_quality=0.0,
            )

        assert len(results) == 1, results
        emb = results[0]["embedding"]
        assert emb.dtype == np.float32, (
            f"dlib path must cast to float32; got {emb.dtype} "
            f"(would serialise as {emb.nbytes} bytes, breaks Protection A's "
            f"512-byte expectation and silently drops the face)"
        )
        assert emb.shape == (128,)
        # Critical regression check: serialised size matches the
        # 512-byte contract Protection A enforces at read time.
        assert len(emb.tobytes()) == 512


# ── Integration shape: pipeline plumbing of method ──


class TestMethodThreadsThroughPipeline:
    """End-to-end: the user setting → Phase 1 resolution → Phase 5
    per-photo extractor call → ``extract_face_embeddings(method=...)``.
    Pre-fix the chain was broken at the very first hop; this is the
    backstop that catches a regression at any of the three layers."""

    def test_extract_face_embeddings_with_method_dlib_skips_sface(self) -> None:
        from bpp.scoring import face_embed

        # When method='dlib' is passed, _extract_sface MUST NOT run.
        # The function should jump straight to _extract_dlib.
        with (
            patch.object(face_embed, "_extract_sface") as sface_spy,
            patch.object(face_embed, "_extract_dlib", return_value=[]) as dlib_spy,
        ):
            face_embed.extract_face_embeddings(
                np.zeros((100, 100, 3), dtype=np.uint8),
                method="dlib",
            )
            sface_spy.assert_not_called()
            dlib_spy.assert_called_once()

    def test_extract_face_embeddings_no_method_uses_sface_then_dlib(self) -> None:
        """Back-compat: callers that don't pass method get the pre-fix
        behaviour (SFace first, dlib fallback only when SFace yields None)."""
        from bpp.scoring import face_embed

        with (
            patch.object(face_embed, "_extract_sface", return_value=[{"x": 1}]) as sface_spy,
            patch.object(face_embed, "_extract_dlib") as dlib_spy,
        ):
            out = face_embed.extract_face_embeddings(
                np.zeros((100, 100, 3), dtype=np.uint8),
            )
            sface_spy.assert_called_once()
            dlib_spy.assert_not_called()
            assert out == [{"x": 1}]

    def test_extract_face_embeddings_method_sface_when_sface_returns_none_falls_back(
        self,
    ) -> None:
        """Even with explicit method='sface', if SFace finds no faces
        (returns None for "no detections"), the dlib fallback should
        still run — same as no-method behaviour."""
        from bpp.scoring import face_embed

        with (
            patch.object(face_embed, "_extract_sface", return_value=None),
            patch.object(face_embed, "_extract_dlib", return_value=[]) as dlib_spy,
        ):
            face_embed.extract_face_embeddings(
                np.zeros((100, 100, 3), dtype=np.uint8),
                method="sface",
            )
            dlib_spy.assert_called_once()


class TestProducingModelIdMapping:
    """Bug (2026-06-17 review): buffalo_s embeddings were tagged with the
    bare method string "buffalo_s" instead of the registry id
    "insightface_buffalo_s", so the derived-data purge (which deletes
    WHERE producing_model_id = <registry id> at model-removal time) would
    silently miss them — a restricted model's embeddings would linger after
    removal. The inline if/elif dropped the buffalo_s cell; the fix is one
    shared map. These tests pin the full state space so a new embedder
    can't reintroduce the gap.
    """

    def test_buffalo_s_maps_to_registry_id(self) -> None:
        from bpp.scoring.face_embed import producing_model_id_for

        assert producing_model_id_for("buffalo_s") == "insightface_buffalo_s"

    def test_known_methods_map_to_registry_ids(self) -> None:
        from bpp.scoring.face_embed import producing_model_id_for

        assert producing_model_id_for("sface") == "sface_yunet"
        assert producing_model_id_for("dlib") == "dlib_face_recognition_resnet_v1"

    def test_byom_method_passes_through(self) -> None:
        from bpp.scoring.face_embed import producing_model_id_for

        assert producing_model_id_for("byom_abc123") == "byom_abc123"

    def test_every_mapped_id_is_a_real_registry_entry(self) -> None:
        """The whole point: every method must resolve to an id the registry
        actually knows, or the purge can never match it. Catches a new
        embedder whose registry id drifts from its mapped value."""
        from bpp.registry.model_registry import get_entry
        from bpp.scoring.face_embed import EMBEDDING_METHOD_TO_REGISTRY_ID

        for method, registry_id in EMBEDDING_METHOD_TO_REGISTRY_ID.items():
            assert get_entry(registry_id) is not None, (
                f"method {method!r} maps to {registry_id!r} which is not a "
                f"registered model entry — the derived-data purge would miss "
                f"these embeddings on removal"
            )
