"""Tests for XMP sidecar and JSON manifest export — part 2 of 2: TestExportSelectedManifest,
TestExportSelectedXmp, TestExportSelectedRegistryWalkCount."""

from __future__ import annotations

import json
import os

from bpp.output.export import (
    export_selected,
    write_xmp_sidecar,
)
from tests._export_sidecar_shared import _make_analysis, _make_selected, _make_test_image

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
        """By default the absolute library path is sanitized
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
        """Regression — none of report.json, report.csv, or
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
    """`_iter_weighted_score_pairs()` used to be invoked
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

        photo = tmp_path / "x.jpg"
        photo.touch()
        result = write_xmp_sidecar(
            str(photo),
            {"aggregate_score": 0.5, "blur_score": 0.6},
        )
        assert os.path.isfile(result)
