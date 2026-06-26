"""R8-H12d: public face detector + embedder registries.

The face detection pipeline used to expose only a private
``_DETECTORS`` dict — plugin authors couldn't introspect the
list of available detectors or register their own without
patching ``bpp.scoring.face`` directly. Same for embedders
(SFace + dlib were inline implementation details).

This test class locks the public registry contract so a future
refactor can't quietly remove the API surface.
"""

from __future__ import annotations


class TestFaceDetectorRegistry:
    def test_yunet_registered(self):
        # Importing face.py is what populates the registry.
        import bpp.scoring.face  # noqa: F401
        from bpp.scoring.face_detector_registry import get_detector

        d = get_detector("yunet")
        assert d is not None
        assert d.name == "yunet"
        assert d.toggle_key == "model_yunet"
        assert d.license_id

    def test_scrfd_registered(self):
        import bpp.scoring.face  # noqa: F401
        from bpp.scoring.face_detector_registry import get_detector

        assert get_detector("scrfd") is not None

    def test_haar_fallback_registered(self):
        """Even the always-on Haar fallback shows up in the
        registry — plugin authors auditing the pipeline shouldn't
        have to discover that detector by reading the orchestrator
        source."""
        import bpp.scoring.face  # noqa: F401
        from bpp.scoring.face_detector_registry import get_detector

        haar = get_detector("haar")
        assert haar is not None
        assert haar.toggle_key is None  # always on

    def test_list_detectors_returns_all_known_built_ins(self):
        import bpp.scoring.face  # noqa: F401
        from bpp.scoring.face_detector_registry import list_detectors

        names = {d.name for d in list_detectors()}
        # Built-ins from face.py
        assert {"yunet", "scrfd", "blazeface_fr", "mediapipe_sr", "dlib", "haar"}.issubset(names)

    def test_register_replaces_existing_entry(self):
        from bpp.scoring.face_detector_registry import (
            FaceDetector,
            get_detector,
            register_detector,
        )

        def _stub_detect(_img, _conf):
            return []

        register_detector(
            FaceDetector(
                name="__test_dup__",
                detect=_stub_detect,
                toggle_key=None,
                license_id="MIT",
            )
        )
        first = get_detector("__test_dup__")
        register_detector(
            FaceDetector(
                name="__test_dup__",
                detect=_stub_detect,
                toggle_key=None,
                license_id="Apache-2.0",
            )
        )
        second = get_detector("__test_dup__")
        assert second is not first
        assert second.license_id == "Apache-2.0"

    def test_run_optional_detector_invokes_registered(self):
        from bpp.scoring.face_detector_registry import (
            FaceDetector,
            register_detector,
            run_optional_detector,
        )

        calls: list[tuple] = []

        def _spy(img, conf):
            calls.append((id(img), conf))
            return [(0, 0, 10, 10, 0.9)]

        register_detector(
            FaceDetector(
                name="__test_spy__",
                detect=_spy,
                toggle_key=None,
                license_id="MIT",
            )
        )

        import numpy as np

        result = run_optional_detector("__test_spy__", np.zeros((20, 20, 3), dtype=np.uint8), 0.3)
        assert result == [(0, 0, 10, 10, 0.9)]
        assert len(calls) == 1

    def test_run_optional_detector_unknown_returns_empty(self):
        import numpy as np

        from bpp.scoring.face_detector_registry import run_optional_detector

        # Unregistered name → empty list, no exception. Plugin code
        # can probe for an optional detector without try/except.
        result = run_optional_detector(
            "__no_such_detector__", np.zeros((20, 20, 3), dtype=np.uint8), 0.3
        )
        assert result == []


class TestFaceEmbedderRegistry:
    def test_sface_registered(self):
        # Importing face_embed populates the registry.
        import bpp.scoring.face_embed  # noqa: F401
        from bpp.scoring.face_embedder_registry import get_embedder

        sface = get_embedder("sface")
        assert sface is not None
        assert sface.embedding_dim == 128
        assert sface.license_id

    def test_dlib_registered(self):
        import bpp.scoring.face_embed  # noqa: F401
        from bpp.scoring.face_embedder_registry import get_embedder

        dlib = get_embedder("dlib")
        assert dlib is not None
        assert dlib.embedding_dim == 128

    def test_list_embedders_includes_built_ins(self):
        import bpp.scoring.face_embed  # noqa: F401
        from bpp.scoring.face_embedder_registry import list_embedders

        names = {e.name for e in list_embedders()}
        assert "sface" in names
        assert "dlib" in names

    def test_run_optional_embedder_unknown_returns_none(self):
        import numpy as np

        from bpp.scoring.face_embedder_registry import run_optional_embedder

        result = run_optional_embedder(
            "__no_such_embedder__", np.zeros((20, 20, 3), dtype=np.uint8), (0, 0, 10, 10)
        )
        assert result is None


# ─── R9-extensibility-M3: registration emits a debug-log breadcrumb ──


class TestRegistrationLogs:
    """Plugin authors debugging "did my entry land?" need a log
    breadcrumb. The registries used to be silent — now every
    register_* emits a debug-level message so an operator who turns
    on --debug sees the full registration sequence at startup."""

    def test_register_detector_emits_debug(self, caplog):
        from bpp.scoring.face_detector_registry import (
            FaceDetector,
            register_detector,
        )

        with caplog.at_level("DEBUG", logger="bpp.scoring.face_detector_registry"):
            register_detector(
                FaceDetector(
                    name="__r9_log_test_detector__",
                    detect=lambda img, conf: [],
                    license_id="MIT",
                    description="test",
                )
            )

        msg = " ".join(rec.message for rec in caplog.records)
        assert "__r9_log_test_detector__" in msg
        assert "MIT" in msg

    def test_register_embedder_emits_debug(self, caplog):
        import numpy as np

        from bpp.scoring.face_embedder_registry import (
            FaceEmbedder,
            register_embedder,
        )

        def _embed(img, box):
            return np.zeros(128, dtype=np.float32)

        with caplog.at_level("DEBUG", logger="bpp.scoring.face_embedder_registry"):
            register_embedder(
                FaceEmbedder(
                    name="__r9_log_test_embedder__",
                    embed=_embed,
                    embedding_dim=128,
                    license_id="Apache-2.0",
                )
            )

        msg = " ".join(rec.message for rec in caplog.records)
        assert "__r9_log_test_embedder__" in msg
        assert "dim=128" in msg

    def test_register_field_emits_debug(self, caplog):
        from bpp.config_schema import ConfigField, register_field

        with caplog.at_level("DEBUG", logger="bpp.config_schema"):
            register_field(
                ConfigField(
                    key="__r9_log_test_field__",
                    type=int,
                    category="Plugins",
                )
            )

        msg = " ".join(rec.message for rec in caplog.records)
        assert "__r9_log_test_field__" in msg
        assert "Plugins" in msg
