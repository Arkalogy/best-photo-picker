"""Tests for export, gallery, timing, concurrency, paths, and logging — part 1 of 4."""

from __future__ import annotations

import csv
import json
import os

from PIL import Image

from bpp.output.export import export_selected
from bpp.output.gallery import (
    _make_thumbnail,
)
from tests._export_utils_shared import (
    _make_analysis,
    _make_selected,
    _make_test_image,
)

# ══════════════════════════════════════════════════════════════════════════
#  export.py
# ══════════════════════════════════════════════════════════════════════════


class TestExportSelectedCopyMode:
    """Tests for export_selected with default copy mode."""

    def test_basic_copy(self, tmp_path):
        selected = _make_selected(tmp_path)
        analysis = _make_analysis(selected)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir)

        sel_dir = os.path.join(outdir, "selected")
        assert os.path.isdir(sel_dir)
        files = sorted(os.listdir(sel_dir))
        assert len(files) == 3
        assert files[0] == "001_photo_0.jpg"
        assert files[1] == "002_photo_1.jpg"
        assert files[2] == "003_photo_2.jpg"

    def test_report_json_created(self, tmp_path):
        selected = _make_selected(tmp_path, n=2)
        analysis = _make_analysis(selected, extra=1)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, config={"k": 10})

        report_path = os.path.join(outdir, "report.json")
        assert os.path.isfile(report_path)
        with open(report_path) as f:
            report = json.load(f)

        from bpp import __version__

        assert report["version"] == __version__
        assert report["config"] == {"k": 10}
        assert report["total_analyzed"] == 3
        assert report["total_selected"] == 2
        assert len(report["selected"]) == 2
        assert len(report["skipped"]) == 1
        assert report["selected"][0]["index"] == 1
        assert report["selected"][0]["blur_score"] == 0.7

    def test_report_csv_created(self, tmp_path):
        """By default the CSV `filepath` column carries the
        sanitized basename (or library-relative path), not the
        absolute source. Pass `include_source_paths=True` to
        restore the absolute value."""
        selected = _make_selected(tmp_path, n=2)
        analysis = _make_analysis(selected)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir)

        csv_path = os.path.join(outdir, "report.csv")
        assert os.path.isfile(csv_path)
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        # Without library_path / include_source_paths, the value is
        # the basename of the absolute source.
        assert rows[0]["filepath"] == os.path.basename(selected[0]["filepath"])
        assert "aggregate_score" in rows[0]

    def test_report_csv_fieldnames(self, tmp_path):
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir)

        csv_path = os.path.join(outdir, "report.csv")
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            assert set(reader.fieldnames) == {
                "index",
                "filepath",
                "date",
                "aggregate_score",
                "blur_score",
                "exposure_score",
                "face_score",
                "composition_score",
                "selection_reason",
            }

    def test_skipped_items_in_report(self, tmp_path):
        """Skipped entries also get the sanitized filepath.
        The fixture uses /fake/skipped_N.jpg sources, which are
        outside the (empty) library_path so they fall back to the
        basename only."""
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=3)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir)

        with open(os.path.join(outdir, "report.json")) as f:
            report = json.load(f)
        assert len(report["skipped"]) == 3
        assert report["skipped"][0]["filepath"] == "skipped_0.jpg"

    def test_config_defaults_to_empty_dict(self, tmp_path):
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir)

        with open(os.path.join(outdir, "report.json")) as f:
            report = json.load(f)
        assert report["config"] == {}


class TestExportSelectedModes:
    """Tests for hardlink, symlink, and unknown modes."""

    def test_hardlink_mode(self, tmp_path):
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, mode="hardlink")

        sel_dir = os.path.join(outdir, "selected")
        dest = os.path.join(sel_dir, "001_photo_0.jpg")
        assert os.path.isfile(dest)
        # Hardlinked files share the same inode
        src_stat = os.stat(selected[0]["filepath"])
        dst_stat = os.stat(dest)
        assert src_stat.st_ino == dst_stat.st_ino

    def test_symlink_mode(self, tmp_path):
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, mode="symlink")

        sel_dir = os.path.join(outdir, "selected")
        dest = os.path.join(sel_dir, "001_photo_0.jpg")
        assert os.path.islink(dest)
        assert os.path.realpath(dest) == os.path.realpath(selected[0]["filepath"])

    def test_unknown_mode_falls_back_to_copy(self, tmp_path):
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, mode="foobar")

        sel_dir = os.path.join(outdir, "selected")
        dest = os.path.join(sel_dir, "001_photo_0.jpg")
        assert os.path.isfile(dest)
        # Should be a copy, not a hardlink
        src_stat = os.stat(selected[0]["filepath"])
        dst_stat = os.stat(dest)
        assert src_stat.st_ino != dst_stat.st_ino


class TestExportSelectedMergeAndErrors:
    """Export merges into an existing folder: the previous
    'rmtree on force, refuse otherwise' behavior was a foot-gun — pointing
    at ~/Downloads silently nuked everything. Unrelated files in the
    destination must survive an export."""

    def test_existing_outdir_is_merged_not_wiped(self, tmp_path):
        outdir = str(tmp_path / "out")
        os.makedirs(outdir)
        # Sentinel: an unrelated file the user put in the destination.
        # The new merge behavior must preserve it.
        (tmp_path / "out" / "old_file.txt").write_text("old")

        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)

        export_selected(selected, analysis, outdir)

        assert os.path.isfile(os.path.join(outdir, "old_file.txt")), (
            "merge mode must preserve files the user already had in outdir"
        )
        assert (tmp_path / "out" / "old_file.txt").read_text() == "old"
        assert os.path.isfile(os.path.join(outdir, "selected", "001_photo_0.jpg"))

    def test_same_named_photo_in_selected_is_overwritten(self, tmp_path):
        """Per-file collision contract: when the export-target filename
        (e.g. ``001_photo_0.jpg``) already exists inside ``outdir/selected/``
        — e.g. from a previous export with the same photos — the new
        write must overwrite it cleanly. Pre-merge behavior was
        rmtree-then-write, so collisions never happened in practice;
        with merge mode this is the load-bearing case.

        Note: the export re-encodes JPEGs via strip_metadata=True
        (PIL.save), so the output bytes legitimately differ from the
        source bytes. The assertion pins (a) the stale bytes are gone
        and (b) the file is a valid JPEG — together that proves a
        clean overwrite, not a no-op or an append."""
        outdir = str(tmp_path / "out")
        sel_dir = os.path.join(outdir, "selected")
        os.makedirs(sel_dir)
        stale = os.path.join(sel_dir, "001_photo_0.jpg")
        with open(stale, "wb") as f:
            f.write(b"STALE_BYTES_NOT_A_JPEG")
        stale_size = os.path.getsize(stale)

        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)

        export_selected(selected, analysis, outdir)

        with open(stale, "rb") as f:
            result_bytes = f.read()
        assert not result_bytes.startswith(b"STALE_BYTES"), (
            "stale bytes must be gone after overwrite"
        )
        assert result_bytes[:3] == b"\xff\xd8\xff", (
            "overwritten file must be a valid JPEG (FF D8 FF magic)"
        )
        assert os.path.getsize(stale) != stale_size, (
            "file size should reflect the new contents, not the stale 22 bytes"
        )

    def test_report_sidecars_overwrite_cleanly(self, tmp_path):
        """report.json / report.csv from a previous export must be
        overwritten when the same destination is re-used. If the merge
        code path ever started APPENDING to these (which json/csv
        writers don't, but the contract should be pinned), the sidecars
        would be corrupt JSON / mixed-schema CSV.

        gallery.html (the only HTML the export writes) is included when
        gallery=True. Unrelated files at unrelated names (e.g. a
        user-authored 'report.html') are preserved by the merge
        contract and tested separately in
        test_existing_outdir_is_merged_not_wiped."""
        outdir = str(tmp_path / "out")
        os.makedirs(outdir)
        for name in ("report.json", "report.csv", "gallery.html"):
            with open(os.path.join(outdir, name), "w") as f:
                f.write("STALE_GARBAGE_NOT_VALID_FORMAT")

        selected = _make_selected(tmp_path, n=2)
        analysis = _make_analysis(selected, extra=0)
        export_selected(selected, analysis, outdir, gallery=True)

        with open(os.path.join(outdir, "report.json")) as f:
            report = json.load(f)
        assert report, "report.json must contain fresh export data after overwrite"
        with open(os.path.join(outdir, "report.csv")) as f:
            csv_content = f.read()
        assert "STALE_GARBAGE" not in csv_content, "report.csv must be overwritten"
        with open(os.path.join(outdir, "gallery.html")) as f:
            html = f.read()
        assert "STALE_GARBAGE" not in html, "gallery.html must be overwritten"

    def test_failed_export_records_error(self, tmp_path):
        """Missing source file should fail gracefully."""
        outdir = str(tmp_path / "out")
        selected = [{"filepath": "/nonexistent/photo.jpg", "date": "2024-01-01"}]
        analysis = selected[:]

        export_selected(selected, analysis, outdir)

        # Export should still create report even with failures
        with open(os.path.join(outdir, "report.json")) as f:
            report = json.load(f)
        assert report["total_selected"] == 1

    def test_partial_failure_exports_good_files(self, tmp_path):
        """Mix of valid and invalid files: valid ones export fine."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        good = str(src_dir / "good.jpg")
        _make_test_image(good)

        selected = [
            {"filepath": good, "date": "2024-01-01"},
            {"filepath": "/nonexistent/bad.jpg", "date": "2024-01-02"},
        ]
        analysis = selected[:]
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir)

        sel_dir = os.path.join(outdir, "selected")
        files = os.listdir(sel_dir)
        assert "001_good.jpg" in files
        assert len(files) == 1  # bad one not exported

    def test_empty_selection(self, tmp_path):
        outdir = str(tmp_path / "out")
        export_selected([], [], outdir)

        with open(os.path.join(outdir, "report.json")) as f:
            report = json.load(f)
        assert report["total_selected"] == 0
        assert report["selected"] == []

    def test_gallery_flag_triggers_generation(self, tmp_path):
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, gallery=True)

        assert os.path.isfile(os.path.join(outdir, "gallery.html"))
        assert os.path.isdir(os.path.join(outdir, "thumbnails"))

    def test_gallery_flag_false_no_gallery(self, tmp_path):
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, gallery=False)

        assert not os.path.exists(os.path.join(outdir, "gallery.html"))

    def test_selected_item_missing_optional_fields(self, tmp_path):
        """Items with minimal fields should still export."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        p = str(src_dir / "minimal.jpg")
        _make_test_image(p)

        selected = [{"filepath": p}]
        analysis = selected[:]
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir)

        with open(os.path.join(outdir, "report.json")) as f:
            report = json.load(f)
        entry = report["selected"][0]
        assert entry["date"] == ""
        assert entry["aggregate_score"] == 0
        assert entry["cluster_size"] == 1


# ══════════════════════════════════════════════════════════════════════════
#  gallery.py
# ══════════════════════════════════════════════════════════════════════════


class TestMakeThumbnail:
    """Tests for _make_thumbnail."""

    def test_success(self, tmp_path):
        src = tmp_path / "src.jpg"
        _make_test_image(src, size=(800, 600))
        dest = str(tmp_path / "thumb.jpg")

        result = _make_thumbnail(str(src), dest)

        assert result is True
        assert os.path.isfile(dest)
        with Image.open(dest) as img:
            assert img.format == "JPEG"
            assert max(img.size) <= 400

    def test_custom_size(self, tmp_path):
        src = tmp_path / "src.jpg"
        _make_test_image(src, size=(1000, 1000))
        dest = str(tmp_path / "thumb.jpg")

        _make_thumbnail(str(src), dest, size=200)

        with Image.open(dest) as img:
            assert max(img.size) <= 200

    def test_nonexistent_source_returns_false(self, tmp_path):
        result = _make_thumbnail("/nonexistent/img.jpg", str(tmp_path / "thumb.jpg"))
        assert result is False

    def test_corrupt_file_returns_false(self, tmp_path):
        src = tmp_path / "corrupt.jpg"
        src.write_text("not an image")
        dest = str(tmp_path / "thumb.jpg")

        result = _make_thumbnail(str(src), dest)

        assert result is False

    def test_png_source(self, tmp_path):
        src = tmp_path / "src.png"
        Image.new("RGBA", (200, 200), (0, 0, 255, 128)).save(str(src), "PNG")
        dest = str(tmp_path / "thumb.jpg")

        result = _make_thumbnail(str(src), dest)

        assert result is True
        with Image.open(dest) as img:
            assert img.mode == "RGB"  # converted from RGBA
