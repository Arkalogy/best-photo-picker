"""Tests for XMP sidecar and JSON manifest export."""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET

from PIL import Image

from bpp.output.export import (
    export_selected,
    score_to_label,
    score_to_rating,
    write_json_manifest,
    write_xmp_sidecar,
)

# ── helpers ──────────────────────────────────────────────────────────────


def _make_test_image(path, size=(100, 100), color="red"):
    """Create a minimal JPEG image for testing."""
    img = Image.new("RGB", size, color)
    img.save(str(path), "JPEG")
    return str(path)


def _make_selected(tmp_path, n=3):
    """Build a list of selected items backed by real image files."""
    items = []
    src_dir = tmp_path / "src"
    src_dir.mkdir(exist_ok=True)
    for i in range(n):
        p = src_dir / f"photo_{i}.jpg"
        _make_test_image(p, color=["red", "green", "blue"][i % 3])
        items.append(
            {
                "filepath": str(p),
                "date": f"2024-06-{10 + i:02d}",
                "aggregate_score": 0.8 - i * 0.1,
                "blur_score": 0.90,
                "exposure_score": 0.75,
                "face_score": 0.85,
                "composition_score": 0.78,
                "selection_reason": "top",
                "cluster_size": 1,
            }
        )
    return items


def _make_analysis(selected, extra=2):
    """Build analysis list = selected + some skipped items."""
    skipped = []
    for i in range(extra):
        skipped.append(
            {
                "filepath": f"/fake/skipped_{i}.jpg",
                "aggregate_score": 0.2,
            }
        )
    return selected + skipped


# ══════════════════════════════════════════════════════════════════════════
#  score_to_rating
# ══════════════════════════════════════════════════════════════════════════


class TestScoreToRating:
    """Map aggregate score (0-1) to XMP star rating (1-5)."""

    def test_zero_score(self):
        assert score_to_rating(0.0) == 1

    def test_low_score(self):
        assert score_to_rating(0.15) == 1

    def test_boundary_0_2(self):
        assert score_to_rating(0.2) == 2

    def test_mid_low_score(self):
        assert score_to_rating(0.35) == 2

    def test_boundary_0_4(self):
        assert score_to_rating(0.4) == 3

    def test_mid_score(self):
        assert score_to_rating(0.5) == 3

    def test_boundary_0_6(self):
        assert score_to_rating(0.6) == 4

    def test_high_score(self):
        assert score_to_rating(0.75) == 4

    def test_boundary_0_8(self):
        assert score_to_rating(0.8) == 5

    def test_max_score(self):
        assert score_to_rating(1.0) == 5

    def test_just_below_boundary(self):
        assert score_to_rating(0.199) == 1
        assert score_to_rating(0.399) == 2
        assert score_to_rating(0.599) == 3
        assert score_to_rating(0.799) == 4

    def test_negative_score_clamps_to_1(self):
        assert score_to_rating(-0.5) == 1

    def test_above_one_clamps_to_5(self):
        assert score_to_rating(1.5) == 5


# ══════════════════════════════════════════════════════════════════════════
#  score_to_label
# ══════════════════════════════════════════════════════════════════════════


class TestScoreToLabel:
    """Map aggregate score to XMP color label."""

    def test_low_score_red(self):
        assert score_to_label(0.1) == "Red"

    def test_boundary_red_yellow(self):
        # < 0.3 is Red, >= 0.3 is Yellow
        assert score_to_label(0.29) == "Red"
        assert score_to_label(0.3) == "Yellow"

    def test_mid_score_yellow(self):
        assert score_to_label(0.5) == "Yellow"

    def test_boundary_yellow_green(self):
        assert score_to_label(0.59) == "Yellow"
        assert score_to_label(0.6) == "Green"

    def test_high_score_green(self):
        assert score_to_label(0.7) == "Green"

    def test_boundary_green_blue(self):
        assert score_to_label(0.79) == "Green"
        assert score_to_label(0.8) == "Blue"

    def test_top_score_blue(self):
        assert score_to_label(0.95) == "Blue"

    def test_zero_score(self):
        assert score_to_label(0.0) == "Red"

    def test_max_score(self):
        assert score_to_label(1.0) == "Blue"


# ══════════════════════════════════════════════════════════════════════════
#  write_xmp_sidecar
# ══════════════════════════════════════════════════════════════════════════


class TestWriteXmpSidecar:
    """Test XMP sidecar file generation."""

    def test_creates_xmp_file(self, tmp_path):
        photo_path = str(tmp_path / "photo.jpg")
        scores = {
            "aggregate_score": 0.82,
            "blur_score": 0.90,
            "exposure_score": 0.75,
            "face_score": 0.85,
            "composition_score": 0.78,
        }

        xmp_path = write_xmp_sidecar(photo_path, scores)

        assert os.path.isfile(xmp_path)
        assert xmp_path == str(tmp_path / "photo.xmp")

    def test_xmp_content_is_valid_xml(self, tmp_path):
        photo_path = str(tmp_path / "photo.jpg")
        scores = {
            "aggregate_score": 0.82,
            "blur_score": 0.90,
            "exposure_score": 0.75,
            "face_score": 0.85,
            "composition_score": 0.78,
        }

        xmp_path = write_xmp_sidecar(photo_path, scores)

        with open(xmp_path) as f:
            content = f.read()
        # Should be parseable XML
        ET.fromstring(content)

    def test_xmp_contains_rating(self, tmp_path):
        photo_path = str(tmp_path / "photo.jpg")
        scores = {"aggregate_score": 0.82}

        xmp_path = write_xmp_sidecar(photo_path, scores)

        with open(xmp_path) as f:
            content = f.read()
        assert 'xmp:Rating="5"' in content

    def test_xmp_contains_label(self, tmp_path):
        photo_path = str(tmp_path / "photo.jpg")
        scores = {"aggregate_score": 0.82}

        xmp_path = write_xmp_sidecar(photo_path, scores)

        with open(xmp_path) as f:
            content = f.read()
        assert 'xmp:Label="Blue"' in content

    def test_xmp_contains_description_with_scores(self, tmp_path):
        photo_path = str(tmp_path / "photo.jpg")
        scores = {
            "aggregate_score": 0.82,
            "blur_score": 0.90,
            "exposure_score": 0.75,
            "face_score": 0.85,
            "composition_score": 0.78,
        }

        xmp_path = write_xmp_sidecar(photo_path, scores)

        with open(xmp_path) as f:
            content = f.read()
        assert "Score: 0.82" in content
        assert "blur=0.90" in content
        assert "exposure=0.75" in content
        assert "face=0.85" in content
        assert "composition=0.78" in content
        assert "Best Photo Picker" in content

    def test_xmp_has_xml_declaration(self, tmp_path):
        photo_path = str(tmp_path / "photo.jpg")
        scores = {"aggregate_score": 0.5}

        xmp_path = write_xmp_sidecar(photo_path, scores)

        with open(xmp_path) as f:
            content = f.read()
        assert content.startswith('<?xml version="1.0" encoding="UTF-8"?>')

    def test_xmp_has_xmpmeta_wrapper(self, tmp_path):
        photo_path = str(tmp_path / "photo.jpg")
        scores = {"aggregate_score": 0.5}

        xmp_path = write_xmp_sidecar(photo_path, scores)

        with open(xmp_path) as f:
            content = f.read()
        assert 'xmlns:x="adobe:ns:meta/"' in content
        assert "</x:xmpmeta>" in content

    def test_xmp_low_score_rating_and_label(self, tmp_path):
        photo_path = str(tmp_path / "photo.jpg")
        scores = {"aggregate_score": 0.15}

        xmp_path = write_xmp_sidecar(photo_path, scores)

        with open(xmp_path) as f:
            content = f.read()
        assert 'xmp:Rating="1"' in content
        assert 'xmp:Label="Red"' in content

    def test_xmp_missing_sub_scores_default_to_zero(self, tmp_path):
        photo_path = str(tmp_path / "photo.jpg")
        scores = {"aggregate_score": 0.5}

        xmp_path = write_xmp_sidecar(photo_path, scores)

        with open(xmp_path) as f:
            content = f.read()
        assert "blur=0.00" in content
        assert "exposure=0.00" in content
        assert "face=0.00" in content
        assert "composition=0.00" in content

    def test_xmp_replaces_existing_ext(self, tmp_path):
        """XMP file for photo.png should be photo.xmp, not photo.png.xmp."""
        photo_path = str(tmp_path / "image.png")
        scores = {"aggregate_score": 0.5}

        xmp_path = write_xmp_sidecar(photo_path, scores)

        assert xmp_path == str(tmp_path / "image.xmp")


# ══════════════════════════════════════════════════════════════════════════
#  write_json_manifest
# ══════════════════════════════════════════════════════════════════════════


class TestWriteJsonManifest:
    """Test JSON manifest generation."""

    def test_creates_manifest_file(self, tmp_path):
        outdir = str(tmp_path)
        photos_data = []

        manifest_path = write_json_manifest(outdir, photos_data, "/path/to/library")

        assert os.path.isfile(manifest_path)
        assert manifest_path == os.path.join(str(tmp_path), "manifest.json")

    def test_manifest_structure(self, tmp_path):
        """R6-L1: default manifest does NOT include the absolute
        `library` field — it's sanitized away. Pass
        `include_source_paths=True` to get it back."""
        outdir = str(tmp_path)
        photos_data = [
            {
                "dest_name": "001_IMG_1234.jpg",
                "original_path": "/path/to/library/IMG_1234.jpg",
                "scores": {
                    "aggregate_score": 0.82,
                    "blur_score": 0.90,
                    "exposure_score": 0.75,
                    "face_score": 0.85,
                    "composition_score": 0.78,
                },
                "rank": 1,
            },
        ]

        manifest_path = write_json_manifest(outdir, photos_data, "/path/to/library")

        with open(manifest_path) as f:
            manifest = json.load(f)

        assert "exported_at" in manifest
        assert "library" not in manifest, "absolute library path leaked by default"
        assert manifest["count"] == 1
        assert len(manifest["photos"]) == 1

    def test_manifest_photo_entry(self, tmp_path):
        """R6-L1: default per-photo entry uses `original_filename`
        (relative or basename) and omits the absolute
        `original_path`."""
        outdir = str(tmp_path)
        photos_data = [
            {
                "dest_name": "001_IMG_1234.jpg",
                "original_path": "/path/to/library/sub/IMG_1234.jpg",
                "scores": {
                    "aggregate_score": 0.82,
                    "blur_score": 0.90,
                    "exposure_score": 0.75,
                    "face_score": 0.85,
                    "composition_score": 0.78,
                },
                "rank": 1,
            },
        ]

        manifest_path = write_json_manifest(outdir, photos_data, "/path/to/library")

        with open(manifest_path) as f:
            manifest = json.load(f)

        photo = manifest["photos"][0]
        assert photo["filename"] == "001_IMG_1234.jpg"
        assert "original_path" not in photo, "absolute source path leaked by default"
        # Source is inside library_path → relative path
        assert photo["original_filename"] == os.path.join("sub", "IMG_1234.jpg")
        assert photo["rank"] == 1
        assert photo["scores"]["aggregate"] == 0.82
        assert photo["scores"]["blur"] == 0.90
        assert photo["scores"]["exposure"] == 0.75
        assert photo["scores"]["face"] == 0.85
        assert photo["scores"]["composition"] == 0.78

    def test_manifest_include_source_paths_opt_in(self, tmp_path):
        """R6-L1: explicit opt-in restores the absolute fields for
        diagnostic / archival workflows that need them."""
        outdir = str(tmp_path)
        photos_data = [
            {
                "dest_name": "001_IMG_1234.jpg",
                "original_path": "/path/to/library/IMG_1234.jpg",
                "scores": {"aggregate_score": 0.5},
                "rank": 1,
            }
        ]

        manifest_path = write_json_manifest(
            outdir, photos_data, "/path/to/library", include_source_paths=True
        )

        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["library"] == "/path/to/library"
        assert manifest["photos"][0]["original_path"] == "/path/to/library/IMG_1234.jpg"
        # The sanitized field stays present — opt-in is additive.
        assert manifest["photos"][0]["original_filename"] == "IMG_1234.jpg"

    def test_manifest_source_outside_library_falls_back_to_basename(self, tmp_path):
        """R6-L1: when the source isn't inside library_path, refuse
        to emit a `..`-relative path that would still leak parent
        directory names (e.g. `Users/alice`) — fall back to the
        basename only."""
        outdir = str(tmp_path)
        photos_data = [
            {
                "dest_name": "001_outside.jpg",
                "original_path": "/Users/alice/Desktop/outside.jpg",
                "scores": {"aggregate_score": 0.5},
                "rank": 1,
            }
        ]

        manifest_path = write_json_manifest(outdir, photos_data, "/path/to/library")

        with open(manifest_path) as f:
            manifest = json.load(f)

        photo = manifest["photos"][0]
        assert photo["original_filename"] == "outside.jpg"
        assert "/Users/alice" not in json.dumps(manifest)
        assert "Desktop" not in json.dumps(manifest)

    def test_manifest_multiple_photos(self, tmp_path):
        outdir = str(tmp_path)
        photos_data = [
            {
                "dest_name": f"00{i}_photo.jpg",
                "original_path": f"/path/photo_{i}.jpg",
                "scores": {"aggregate_score": 0.5 + i * 0.1},
                "rank": i,
            }
            for i in range(1, 4)
        ]

        manifest_path = write_json_manifest(outdir, photos_data, "/lib")

        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["count"] == 3
        assert len(manifest["photos"]) == 3

    def test_manifest_empty_photos(self, tmp_path):
        outdir = str(tmp_path)

        manifest_path = write_json_manifest(outdir, [], "/lib")

        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["count"] == 0
        assert manifest["photos"] == []

    def test_manifest_exported_at_is_iso_format(self, tmp_path):
        outdir = str(tmp_path)

        manifest_path = write_json_manifest(outdir, [], "/lib")

        with open(manifest_path) as f:
            manifest = json.load(f)

        # Should be a valid ISO 8601 timestamp ending with Z
        ts = manifest["exported_at"]
        assert ts.endswith("Z")
        assert "T" in ts


# ══════════════════════════════════════════════════════════════════════════
#  export_selected integration with write_manifest and write_xmp
# ══════════════════════════════════════════════════════════════════════════


class TestExportSelectedManifest:
    """Test that export_selected writes manifest.json when flag is set."""

    def test_manifest_not_written_by_default(self, tmp_path):
        selected = _make_selected(tmp_path, n=2)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir)

        assert not os.path.exists(os.path.join(outdir, "manifest.json"))

    def test_manifest_written_when_flag_set(self, tmp_path):
        selected = _make_selected(tmp_path, n=2)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, write_manifest=True)

        manifest_path = os.path.join(outdir, "manifest.json")
        assert os.path.isfile(manifest_path)

    def test_manifest_contains_exported_photos(self, tmp_path):
        """R6-L1: by default the absolute library path is sanitized
        away. The opt-in (`include_source_paths=True`) restores it."""
        selected = _make_selected(tmp_path, n=2)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(
            selected,
            analysis,
            outdir,
            write_manifest=True,
            library_path="/test/lib",
            include_source_paths=True,
        )

        with open(os.path.join(outdir, "manifest.json")) as f:
            manifest = json.load(f)

        assert manifest["count"] == 2
        assert manifest["library"] == "/test/lib"
        assert len(manifest["photos"]) == 2
        # Check first photo has scores
        p = manifest["photos"][0]
        assert "scores" in p
        assert p["rank"] == 1

    def test_manifest_scores_match_input(self, tmp_path):
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, write_manifest=True)

        with open(os.path.join(outdir, "manifest.json")) as f:
            manifest = json.load(f)

        scores = manifest["photos"][0]["scores"]
        assert scores["aggregate"] == 0.8
        assert scores["blur"] == 0.90
        assert scores["exposure"] == 0.75
        assert scores["face"] == 0.85
        assert scores["composition"] == 0.78

    def test_manifest_skips_failed_exports(self, tmp_path):
        """Photos that failed to export should not appear in the manifest."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        good = str(src_dir / "good.jpg")
        _make_test_image(good)

        selected = [
            {
                "filepath": good,
                "date": "2024-01-01",
                "aggregate_score": 0.9,
                "blur_score": 0.8,
                "exposure_score": 0.7,
                "face_score": 0.6,
                "composition_score": 0.5,
            },
            {"filepath": "/nonexistent/bad.jpg", "date": "2024-01-02"},
        ]
        analysis = selected[:]
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, write_manifest=True)

        with open(os.path.join(outdir, "manifest.json")) as f:
            manifest = json.load(f)

        assert manifest["count"] == 1  # only the successful one

    def test_export_reports_do_not_leak_absolute_source_paths(self, tmp_path):
        """R6-L1: regression — none of report.json, report.csv, or
        manifest.json may contain the owner's absolute source path
        when the user shares the export folder. Use a path that
        looks like a private library (Users/alice/...). The user
        opts in via `include_source_paths=True` for diagnostics; the
        default must be safe."""
        lib = tmp_path / "Users" / "alice" / "Pictures" / "PrivateLibrary"
        lib.mkdir(parents=True)
        src = lib / "KidBirthday.jpg"
        _make_test_image(str(src))

        item = {
            "filepath": str(src),
            "date": "2024-01-01",
            "aggregate_score": 0.9,
            "blur_score": 0.8,
            "exposure_score": 0.7,
            "face_score": 0.6,
            "composition_score": 0.5,
        }
        outdir = tmp_path / "export"

        export_selected(
            selected=[item],
            analysis=[item],
            outdir=str(outdir),
            mode="copy",
            gallery=False,
            write_manifest=True,
            library_path=str(lib),
        )

        for rel in ("report.json", "report.csv", "manifest.json"):
            text = (outdir / rel).read_text()
            assert str(lib) not in text, f"{rel} leaked the library path"
            assert str(src) not in text, f"{rel} leaked the absolute source path"
            assert "Users/alice" not in text, f"{rel} leaked private parent dirs"


class TestExportSelectedXmp:
    """Test that export_selected writes XMP sidecars when flag is set."""

    def test_xmp_not_written_by_default(self, tmp_path):
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir)

        sel_dir = os.path.join(outdir, "selected")
        xmp_files = [f for f in os.listdir(sel_dir) if f.endswith(".xmp")]
        assert len(xmp_files) == 0

    def test_xmp_written_when_flag_set(self, tmp_path):
        selected = _make_selected(tmp_path, n=2)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, write_xmp=True)

        sel_dir = os.path.join(outdir, "selected")
        xmp_files = sorted(f for f in os.listdir(sel_dir) if f.endswith(".xmp"))
        assert len(xmp_files) == 2
        assert xmp_files[0] == "001_photo_0.xmp"
        assert xmp_files[1] == "002_photo_1.xmp"

    def test_xmp_sidecar_has_correct_rating(self, tmp_path):
        selected = _make_selected(tmp_path, n=1)
        # aggregate_score is 0.8 -> rating 5
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, write_xmp=True)

        xmp_path = os.path.join(outdir, "selected", "001_photo_0.xmp")
        with open(xmp_path) as f:
            content = f.read()
        assert 'xmp:Rating="5"' in content

    def test_xmp_not_written_for_failed_exports(self, tmp_path):
        """Failed exports should not produce XMP files."""
        selected = [{"filepath": "/nonexistent/photo.jpg", "date": "2024-01-01"}]
        analysis = selected[:]
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, write_xmp=True)

        sel_dir = os.path.join(outdir, "selected")
        xmp_files = [f for f in os.listdir(sel_dir) if f.endswith(".xmp")]
        assert len(xmp_files) == 0

    def test_both_manifest_and_xmp(self, tmp_path):
        """Both flags can be used together."""
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, write_manifest=True, write_xmp=True)

        assert os.path.isfile(os.path.join(outdir, "manifest.json"))
        sel_dir = os.path.join(outdir, "selected")
        xmp_files = [f for f in os.listdir(sel_dir) if f.endswith(".xmp")]
        assert len(xmp_files) == 1


class TestExportSelectedRegistryWalkCount:
    """R9-perf-H1: `_iter_weighted_score_pairs()` used to be invoked
    once per photo (XMP sidecar) plus once for the manifest plus once
    for the report — N+3 walks of the scorer registry per export run.
    The fix snapshots the registry once at the top of
    `export_selected` and threads the snapshot through write_xmp and
    write_json_manifest. With N photos and write_xmp=True, the new
    shape is exactly 1 walk."""

    def test_snapshot_once_per_export(self, tmp_path):
        from unittest.mock import patch

        from bpp.output import export as export_mod

        selected = _make_selected(tmp_path, n=5)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        original = export_mod._iter_weighted_score_pairs
        call_count = 0

        def _counting():
            nonlocal call_count
            call_count += 1
            return original()

        with patch.object(export_mod, "_iter_weighted_score_pairs", side_effect=_counting):
            export_selected(
                selected,
                analysis,
                outdir,
                write_manifest=True,
                write_xmp=True,
            )

        assert call_count == 1, (
            f"_iter_weighted_score_pairs should be walked once per export "
            f"(snapshot pattern); got {call_count} walks. Pre-fix shape "
            f"was N+3 = 5+3 = 8 walks."
        )

    def test_standalone_callers_still_default_to_walking(self, tmp_path):
        """Tests / ad-hoc callers of `write_xmp_sidecar` that don't
        supply `score_pairs` keep working — the per-call walk is the
        documented fallback."""
        from bpp.output.export import write_xmp_sidecar

        photo = tmp_path / "x.jpg"
        photo.touch()
        result = write_xmp_sidecar(
            str(photo),
            {"aggregate_score": 0.5, "blur_score": 0.6},
        )
        assert os.path.isfile(result)
