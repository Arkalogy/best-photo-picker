"""Tests for nudity detection scoring module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestNudityAvailability:
    def test_is_available_returns_bool(self):
        from bpp.scoring.nudity import is_available

        assert isinstance(is_available(), bool)


class TestNudityScoring:
    def test_no_detections_returns_zero(self):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = []

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            assert score_nudity("/fake/image.jpg") == 0.0

    def test_genital_detection(self):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = [
            {"class": "FEMALE_GENITALIA_EXPOSED", "score": 0.95, "box": [0, 0, 100, 100]},
        ]

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            assert score_nudity("/fake/image.jpg") == 0.95

    def test_secondary_only_reduced_weight(self):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = [
            {"class": "BUTTOCKS_EXPOSED", "score": 0.80, "box": [0, 0, 100, 100]},
        ]

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            score = score_nudity("/fake/image.jpg")
            assert abs(score - 0.24) < 0.01  # 0.3 * 0.80

    def test_combined_primary_and_secondary(self):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = [
            {"class": "MALE_GENITALIA_EXPOSED", "score": 0.70, "box": [0, 0, 50, 50]},
            {"class": "BUTTOCKS_EXPOSED", "score": 0.90, "box": [50, 50, 100, 100]},
        ]

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            score = score_nudity("/fake/image.jpg")
            assert abs(score - 0.97) < 0.01  # 0.70 + 0.3 * 0.90

    def test_score_capped_at_one(self):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = [
            {"class": "FEMALE_GENITALIA_EXPOSED", "score": 0.99, "box": [0, 0, 50, 50]},
            {"class": "BUTTOCKS_EXPOSED", "score": 0.95, "box": [50, 50, 100, 100]},
        ]

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            assert score_nudity("/fake/image.jpg") <= 1.0

    def test_exception_returns_zero(self):
        mock_detector = MagicMock()
        mock_detector.detect.side_effect = RuntimeError("model error")

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            assert score_nudity("/fake/image.jpg") == 0.0

    def test_anus_is_primary_label(self):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = [
            {"class": "ANUS_EXPOSED", "score": 0.85, "box": [0, 0, 50, 50]},
        ]

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            assert score_nudity("/fake/image.jpg") == 0.85

    def test_irrelevant_labels_ignored(self):
        mock_detector = MagicMock()
        mock_detector.detect.return_value = [
            {"class": "FACE_FEMALE", "score": 0.99, "box": [0, 0, 100, 100]},
            {"class": "FEMALE_BREAST_COVERED", "score": 0.95, "box": [0, 0, 100, 100]},
        ]

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            assert score_nudity("/fake/image.jpg") == 0.0


class TestRecomputeSensitivePolicy:
    """Sensitivity is a pick-time policy, NOT a scoring penalty.

    nudity_score (and the legacy skin_score) no longer change a photo's
    aggregate_score. The `sensitive_in_picks` config gates auto-picks
    only: "allow" (default) lets sensitive photos compete; "exclude"
    filters them out of the candidate pool."""

    def _make_item(self, fp, skin=0.0, nudity=None):
        item = {
            "filepath": fp,
            "date": "2024-01-01T00:00:00",
            "date_day": "2024-01-01",
            "date_month": "2024-01",
            "blur_raw": 200.0,  # log-sigmoid(200, mid=200, k=1.5) == 0.5
            "blur_score": 0.5,
            "exposure_score": 0.5,
            "face_score": 0.5,
            "composition_score": 0.5,
            "skin_score": skin,
            "phash": hash(fp) & 0xFFFFFFFFFFFFFFFF,
        }
        if nudity is not None:
            item["nudity_score"] = nudity
        return item

    @staticmethod
    def _config(**overrides):
        config = {
            "hash_distance_threshold": 10,
            "time_window_seconds": 15,
            "global_hash_distance_threshold": 0,
            "max_per_day": 99,
            "min_per_month": 0,
            "max_per_month": 0,
            "blur_weight": 0.25,
            "exposure_weight": 0.25,
            "face_weight": 0.25,
            "composition_weight": 0.25,
        }
        config.update(overrides)
        return config

    def test_nudity_score_does_not_change_aggregate(self):
        """A high nudity_score leaves the aggregate score untouched."""
        from bpp.web.recompute import RecomputeOptions, recompute

        analysis = [
            self._make_item("/a.jpg", skin=0.05, nudity=0.95),
            self._make_item("/b.jpg", skin=0.05, nudity=0.0),
        ]
        scores = recompute(RecomputeOptions(analysis, self._config(), k=2))["score_map"]
        # Identical inputs apart from nudity → identical scores (no penalty).
        assert scores["/a.jpg"] == pytest.approx(scores["/b.jpg"])
        assert scores["/a.jpg"] == pytest.approx(0.5)

    def test_skin_score_does_not_change_aggregate(self):
        """The legacy skin_score heuristic no longer penalizes scoring."""
        from bpp.web.recompute import RecomputeOptions, recompute

        analysis = [
            self._make_item("/a.jpg", skin=0.90),  # no nudity_score
            self._make_item("/b.jpg", skin=0.05),
        ]
        scores = recompute(RecomputeOptions(analysis, self._config(), k=2))["score_map"]
        assert scores["/a.jpg"] == pytest.approx(scores["/b.jpg"])

    def test_exclude_mode_filters_sensitive_from_picks(self):
        """sensitive_in_picks='exclude' drops sensitive photos from picks."""
        from bpp.web.recompute import RecomputeOptions, recompute

        analysis = [
            self._make_item("/a.jpg", nudity=0.95),  # sensitive
            self._make_item("/b.jpg", nudity=0.0),
        ]
        result = recompute(
            RecomputeOptions(analysis, self._config(sensitive_in_picks="exclude"), k=2)
        )
        sel = result["selected_paths"]
        assert "/a.jpg" not in sel
        assert "/b.jpg" in sel

    def test_allow_mode_keeps_sensitive_in_picks(self):
        """The default 'allow' mode lets sensitive photos compete."""
        from bpp.web.recompute import RecomputeOptions, recompute

        analysis = [
            self._make_item("/a.jpg", nudity=0.95),  # sensitive
            self._make_item("/b.jpg", nudity=0.0),
        ]
        result = recompute(RecomputeOptions(analysis, self._config(), k=2))
        assert "/a.jpg" in result["selected_paths"]


# ─── Label-set sync with the installed NudeNet package ───────────


class TestLabelsMatchNudeNetPackage:
    """The 320n.onnx model is NudeNet v3; its output class names live in
    ``nudenet.nudenet.__labels``. Our scoring sets MUST be a subset of
    that list — the original v2-era names (``EXPOSED_GENITALIA_F``, …)
    never matched v3 output, which silently zeroed every photo's
    nudity score library-wide. This guard makes the next model/package
    bump fail loudly instead."""

    def test_scoring_labels_exist_in_package_label_list(self):
        nudenet_mod = pytest.importorskip("nudenet.nudenet")

        from bpp.scoring.nudity import _GENITAL_LABELS, _SECONDARY_LABELS

        # Module-level dunder — getattr to dodge class-body name mangling.
        package_labels = set(getattr(nudenet_mod, "__labels"))
        ours = _GENITAL_LABELS | _SECONDARY_LABELS
        unknown = ours - package_labels
        assert not unknown, (
            f"scoring labels {sorted(unknown)} are not in the installed "
            f"NudeNet package's class list — score_nudity would silently "
            f"return 0.0 for everything. Package labels: {sorted(package_labels)}"
        )


# ─── SHA-256 verification of bundled NudeNet model ────────────────


class TestNudeNetModelIntegrity:
    """The NudeNet 320n model is fetched via the canonical
    ``bpp.utils.download.download_file`` chokepoint and verified
    against a pinned SHA-256 — same flow as buffalo_s, YOLO, and
    every other restricted model. The wheel-bundled-file path was
    retired in legal-posture batch A to keep every restricted
    download routed through the registry policy gate."""

    def test_registry_weight_sha_matches_constant(self):
        """The registry entry's ``weight_sha256`` must match the
        constant the loader passes to ``download_file``. A mismatch
        would let one layer accept bytes the other layer would
        refuse, defeating the integrity story."""
        from bpp.registry.model_registry import get_entry
        from bpp.scoring.nudity import NUDENET_MODEL_SHA256

        entry = get_entry("nudenet_320n")
        assert entry is not None
        assert entry.weight_sha256 == NUDENET_MODEL_SHA256, (
            "registry entry SHA must match the SHA the nudity loader "
            "pins in NUDENET_MODEL_SHA256 — they describe the same "
            "bytes."
        )

    def test_create_detector_routes_through_ensure(self, monkeypatch, tmp_path):
        """``_create_nude_detector`` must call ``ensure_nudenet_model``
        before constructing NudeDetector. Without this, a regression
        could revert to the old wheel-bundled flow and bypass the
        registry chokepoint."""
        import pytest

        try:
            import nudenet  # noqa: F401
        except ImportError:
            pytest.skip("nudenet not installed")

        from bpp.scoring import nudity as nud_mod

        ensure_called = []
        fake_model = tmp_path / "320n.onnx"
        fake_model.write_bytes(b"x")  # contents irrelevant — NudeDetector stubbed

        def fake_ensure():
            ensure_called.append(True)
            return str(fake_model)

        monkeypatch.setattr(nud_mod, "ensure_nudenet_model", fake_ensure)

        observed_path = []

        class _Stub:
            def __init__(self, model_path=None):
                observed_path.append(model_path)

        monkeypatch.setattr("nudenet.NudeDetector", _Stub)
        nud_mod._create_nude_detector(None)
        assert ensure_called, "ensure_nudenet_model must be called"
        assert observed_path == [str(fake_model)], (
            "NudeDetector must be constructed against the path returned by ensure_nudenet_model"
        )

    def test_pinned_url_is_commit_pinned_not_branch(self):
        """The NudeNet model URL must reference a commit SHA, not a
        branch or tag. A force-pushed branch could silently swap
        the model bytes; commit-pinned URLs are immutable."""
        import re

        from bpp.scoring.nudity import NUDENET_MODEL_URL

        # Expect '/<40-hex>/nudenet/320n.onnx' inside the raw URL.
        assert re.search(r"/[0-9a-f]{40}/", NUDENET_MODEL_URL), (
            "NUDENET_MODEL_URL must be commit-pinned (40-char hex "
            "SHA in the path); branch / tag refs are mutable and "
            "can be force-pushed under us."
        )


# ─── Integrity-failure propagation ───────────────────────────────


class TestIntegrityFailurePropagates:
    """A tampered NudeNet model must NOT silently degrade into a 0.0
    score. If ModelSingleton.get() catches the SHA-mismatch exception
    broadly and returns None, score_nudity catches the resulting
    None-detector errors and returns 0.0.

    Net effect: every NSFW photo would score "safe" with no diagnostic.
    Defeats the entire point of pinning the SHA. These tests lock the
    new contract — integrity failures propagate.
    """

    @pytest.fixture(autouse=True)
    def _accept_nudenet_first(self):
        """Record the acceptance row for NudeNet so the policy gate
        in ``_get_detector`` passes through. These tests are about
        integrity failures AFTER the user has accepted the
        click-through; without the row, the gate fires first and
        the integrity path is never reached."""
        from bpp.registry import (
            UseContext,
            confirm_acceptance,
            get_entry,
            prepare_acceptance,
            set_use_context,
            utc_now_iso,
        )

        set_use_context(UseContext.PERSONAL, set_via="test")
        entry = get_entry("nudenet_320n")
        if entry is None:
            return  # registry resetter ran; nothing to gate
        draft = prepare_acceptance(entry, use_context=UseContext.PERSONAL)
        confirm_acceptance(
            draft,
            checkbox_responses={cb_id: True for cb_id, _ in draft.required_checkboxes},
            accepted_at=utc_now_iso(),
            separate_rights_asserted=False,
            source_of_rights_note="",
        )

    def _patched_singleton(self, monkeypatch):
        """Reset the module-level _nudenet singleton so each test
        observes a fresh init attempt with the patched verifier.

        All attribute mutations go through ``monkeypatch.setattr`` so
        they auto-revert after the test. Earlier versions of this
        helper assigned directly, which leaked state across tests and
        caused random-order flakes (see Randomized workflow output).

        Also resets ``import_check`` to a no-op so a previous test's
        poisoned ``import_check`` doesn't short-circuit ``.get()``
        before the patched ``create_fn`` can raise.
        """
        from bpp.scoring import nudity as nud_mod
        from bpp.scoring.model_base import ModelIntegrityError

        # Stub create_fn to raise ModelIntegrityError on init
        def _create_fn_raises(_path):
            raise ModelIntegrityError("test: simulated integrity failure")

        # Replace the singleton's create_fn + reset state. Using
        # monkeypatch.setattr so all changes revert at test teardown.
        monkeypatch.setattr(nud_mod._nudenet, "create_fn", _create_fn_raises)
        monkeypatch.setattr(nud_mod._nudenet, "import_check", lambda: None)
        monkeypatch.setattr(nud_mod._nudenet, "_instance", None)
        monkeypatch.setattr(nud_mod._nudenet, "_available", None)
        return nud_mod

    def test_singleton_get_propagates_integrity_error(self, monkeypatch):
        """ModelSingleton.get() must NOT swallow ModelIntegrityError
        as 'unavailable'. The exception is loud by design — caller
        decides whether to abort the operation or surface to user."""
        import pytest

        try:
            import nudenet  # noqa: F401
        except ImportError:
            pytest.skip("nudenet not installed")

        from bpp.scoring.model_base import ModelIntegrityError

        nud_mod = self._patched_singleton(monkeypatch)

        with pytest.raises(ModelIntegrityError, match="simulated integrity"):
            nud_mod._nudenet.get()

    def test_score_nudity_does_not_swallow_integrity_error(self, monkeypatch):
        """The previous bug: score_nudity returned 0.0 because get()
        returned None. Now get() raises, and score_nudity must let
        the error propagate (NOT catch via the broad except)."""
        import pytest

        try:
            import nudenet  # noqa: F401
        except ImportError:
            pytest.skip("nudenet not installed")

        from bpp.scoring.model_base import ModelIntegrityError

        nud_mod = self._patched_singleton(monkeypatch)

        with pytest.raises(ModelIntegrityError):
            nud_mod.score_nudity("/tmp/nonexistent.jpg")

    def test_singleton_does_not_mark_unavailable_on_integrity(self, monkeypatch):
        """A retry shouldn't be possible (the bytes are wrong, no fix
        without operator action) but we also shouldn't mark the
        singleton as cached-unavailable — that would silently
        re-degrade a future call to 'feature off' instead of
        re-raising. Verify _available stays None after the raise."""
        import pytest

        try:
            import nudenet  # noqa: F401
        except ImportError:
            pytest.skip("nudenet not installed")

        from bpp.scoring.model_base import ModelIntegrityError

        nud_mod = self._patched_singleton(monkeypatch)

        with pytest.raises(ModelIntegrityError):
            nud_mod._nudenet.get()
        assert nud_mod._nudenet._available is None, (
            "Integrity failure must not poison the singleton's "
            "_available cache; a retry should re-attempt and re-raise."
        )

    def test_optional_dep_missing_still_returns_none(self, monkeypatch):
        """Inverse: a normal optional-dep missing failure (ImportError
        from import_check) must STILL behave as before — return None
        from get(), score_nudity returns 0.0 quietly. Don't make all
        failure modes loud, only integrity ones."""
        from bpp.scoring import nudity as nud_mod

        # Force import_check to fail with ImportError
        def _fail_import():
            raise ImportError("test: simulated missing dep")

        # monkeypatch.setattr so all mutations revert after the test —
        # leaving import_check=raises and _available=False here used to
        # poison the next integrity test in random order.
        monkeypatch.setattr(nud_mod._nudenet, "import_check", _fail_import)
        monkeypatch.setattr(nud_mod._nudenet, "_instance", None)
        monkeypatch.setattr(nud_mod._nudenet, "_available", None)

        # get() should swallow ImportError and return None
        assert nud_mod._nudenet.get() is None
        # And subsequent score_nudity returns 0.0 cleanly (None
        # detector → early return)
        assert nud_mod.score_nudity("/tmp/whatever.jpg") == 0.0

    def test_process_one_propagates_integrity_error(self, monkeypatch, tmp_path):
        """End-to-end: the analyze worker's per-image entry point
        (process_one) must propagate ModelIntegrityError instead of
        catching it via the broad `except Exception`. Otherwise the
        worker would log "failed to process X" and continue producing
        0.0 nudity scores for every subsequent photo."""
        import pytest

        from bpp.scoring import aggregate
        from bpp.scoring.model_base import ModelIntegrityError

        # Stub the cache lookup to skip DB I/O (we're testing the
        # exception path, not the cache plumbing). Returning None
        # forces the analyze_single_image branch.
        monkeypatch.setattr(aggregate, "_get_cached", lambda db_path, fp, size, mtime: None)

        def _analyze_raises(*args, **kwargs):
            raise ModelIntegrityError("test: simulated mid-pipeline integrity")

        monkeypatch.setattr(aggregate, "analyze_single_image", _analyze_raises)

        # Make a fake image so _cache_key (os.stat) doesn't trip
        from PIL import Image

        img_path = str(tmp_path / "x.jpg")
        Image.new("RGB", (10, 10)).save(img_path, "JPEG")

        with pytest.raises(ModelIntegrityError):
            aggregate.process_one((img_path, 1024, str(tmp_path / "test.db")))
