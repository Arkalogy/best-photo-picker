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
        mock_detector.label_scores.return_value = {"FEMALE_GENITALIA_EXPOSED": 0.95}

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            assert score_nudity("/fake/image.jpg") == 0.95

    def test_secondary_only_reduced_weight(self):
        mock_detector = MagicMock()
        mock_detector.label_scores.return_value = {"BUTTOCKS_EXPOSED": 0.8}

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            score = score_nudity("/fake/image.jpg")
            assert abs(score - 0.24) < 0.01  # 0.3 * 0.80

    def test_combined_primary_and_secondary(self):
        mock_detector = MagicMock()
        mock_detector.label_scores.return_value = {
            "MALE_GENITALIA_EXPOSED": 0.7,
            "BUTTOCKS_EXPOSED": 0.9,
        }

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            score = score_nudity("/fake/image.jpg")
            assert abs(score - 0.97) < 0.01  # 0.70 + 0.3 * 0.90

    def test_score_capped_at_one(self):
        mock_detector = MagicMock()
        mock_detector.label_scores.return_value = {
            "FEMALE_GENITALIA_EXPOSED": 0.99,
            "BUTTOCKS_EXPOSED": 0.95,
        }

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            assert score_nudity("/fake/image.jpg") <= 1.0

    def test_exception_returns_zero(self):
        mock_detector = MagicMock()
        mock_detector.label_scores.side_effect = RuntimeError("model error")

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            assert score_nudity("/fake/image.jpg") == 0.0

    def test_anus_is_primary_label(self):
        mock_detector = MagicMock()
        mock_detector.label_scores.return_value = {"ANUS_EXPOSED": 0.85}

        with patch("bpp.scoring.nudity._get_detector", return_value=mock_detector):
            from bpp.scoring.nudity import score_nudity

            assert score_nudity("/fake/image.jpg") == 0.85

    def test_irrelevant_labels_ignored(self):
        mock_detector = MagicMock()
        mock_detector.label_scores.return_value = {
            "FACE_FEMALE": 0.99,
            "FEMALE_BREAST_COVERED": 0.95,
        }

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


# ─── Label-set sync with the 320n model's own class names ────────

#: Class names embedded in the pinned 320n.onnx metadata (``names``).
_MODEL_320N_LABELS = (
    "FEMALE_GENITALIA_COVERED",
    "FACE_FEMALE",
    "BUTTOCKS_EXPOSED",
    "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_BREAST_EXPOSED",
    "ANUS_EXPOSED",
    "FEET_EXPOSED",
    "BELLY_COVERED",
    "FEET_COVERED",
    "ARMPITS_COVERED",
    "ARMPITS_EXPOSED",
    "FACE_MALE",
    "BELLY_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "ANUS_COVERED",
    "FEMALE_BREAST_COVERED",
    "BUTTOCKS_COVERED",
)


class TestLabelsMatchModel:
    """Scoring labels MUST be class names the model actually emits — the
    original v2-era names (``EXPOSED_GENITALIA_F``, …) never matched v3
    output, which silently zeroed every photo's nudity score."""

    def test_scoring_labels_are_model_classes(self):
        from bpp.scoring.nudity import _GENITAL_LABELS, _SECONDARY_LABELS

        unknown = (_GENITAL_LABELS | _SECONDARY_LABELS) - set(_MODEL_320N_LABELS)
        assert not unknown, f"scoring labels {sorted(unknown)} are not 320n classes"

    def test_cached_model_metadata_matches(self):
        """When the pinned model is on disk, its embedded class list must
        match the list above (catches a model swap)."""
        from bpp.scoring.nudity import _NudeNetSession, model_file

        path = model_file()
        if not path.exists():
            pytest.skip("320n.onnx not downloaded")
        assert tuple(_NudeNetSession(str(path)).labels) == _MODEL_320N_LABELS


class TestDecode:
    """``_best_score_per_label`` on synthetic YOLOv8 output."""

    @staticmethod
    def _output(rows):
        import numpy as np

        n_cls = len(_MODEL_320N_LABELS)
        out = np.zeros((1, 4 + n_cls, len(rows)), dtype=np.float32)
        for j, (box, cls, score) in enumerate(rows):
            out[0, :4, j] = box
            out[0, 4 + cls, j] = score
        return out

    def test_overlapping_boxes_keep_best_and_disjoint_boxes_both_count(self):
        from bpp.scoring.nudity import _best_score_per_label

        g = _MODEL_320N_LABELS.index("FEMALE_GENITALIA_EXPOSED")
        b = _MODEL_320N_LABELS.index("BUTTOCKS_EXPOSED")
        out = self._output(
            [
                ((100, 100, 40, 40), g, 0.9),
                ((101, 101, 40, 40), g, 0.6),  # same object, suppressed
                ((250, 250, 30, 30), b, 0.5),  # separate object
                ((10, 10, 5, 5), b, 0.1),  # below threshold
            ]
        )
        got = _best_score_per_label(out, list(_MODEL_320N_LABELS))
        assert got == pytest.approx({"FEMALE_GENITALIA_EXPOSED": 0.9, "BUTTOCKS_EXPOSED": 0.5})

    def test_each_anchor_counts_only_its_best_class(self):
        import numpy as np

        from bpp.scoring.nudity import _best_score_per_label

        out = self._output([((100, 100, 40, 40), 1, 0.8)])
        out[0, 4 + _MODEL_320N_LABELS.index("ANUS_EXPOSED"), 0] = np.float32(0.5)
        got = _best_score_per_label(out, list(_MODEL_320N_LABELS))
        assert "ANUS_EXPOSED" not in got
        assert got == pytest.approx({"FACE_FEMALE": 0.8})

    def test_nothing_above_threshold(self):
        from bpp.scoring.nudity import _best_score_per_label

        out = self._output([((100, 100, 40, 40), 0, 0.05)])
        assert _best_score_per_label(out, list(_MODEL_320N_LABELS)) == {}


# ─── SHA-256 verification of bundled NudeNet model ────────────────


class TestNudeNetModelIntegrity:
    """The NudeNet 320n model is fetched via the canonical
    ``bpp.utils.download.download_file`` chokepoint and verified
    against a pinned SHA-256 — same flow as buffalo_s, YOLO, and
    every other restricted model. The wheel-bundled-file path was
    retired to keep every restricted
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

    def test_loads_through_the_shared_model_loader(self):
        """NudeNet is a standard ModelSingleton: its file, URL and SHA are
        the pinned ones, so every load and download goes through the
        shared loader (license gate, retries, verification)."""
        from bpp.scoring import nudity as nud_mod

        single = nud_mod._nudenet
        assert single.model_url == nud_mod.NUDENET_MODEL_URL
        assert single.model_sha256 == nud_mod.NUDENET_MODEL_SHA256
        assert single.registry_id == nud_mod.REGISTRY_ID
        assert str(single.model_path).endswith("320n.onnx")

    def test_no_nudenet_package_import(self):
        """BPP runs the weights itself; the AGPL ``nudenet`` package must
        not be imported anywhere in bpp/."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[1] / "bpp"
        offenders = [
            str(p)
            for p in root.rglob("*.py")
            if "import nudenet" in p.read_text() or "from nudenet" in p.read_text()
        ]
        assert not offenders, offenders

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
