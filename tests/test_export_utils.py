"""Tests for export, gallery, timing, concurrency, paths, and logging."""

from __future__ import annotations

import csv
import json
import logging
import os
import time
from unittest.mock import patch

import pytest
from PIL import Image

from bpp.output.export import export_selected
from bpp.output.gallery import (
    CARD_TEMPLATE,
    GALLERY_TEMPLATE,
    _make_thumbnail,
    generate_gallery,
)
from bpp.utils.concurrency import get_worker_count, parallel_map
from bpp.utils.logging import get_logger, setup_logging
from bpp.utils.paths import safe_join
from bpp.utils.timing import Timer

# ── helpers ──────────────────────────────────────────────────────────────


def _double(x):
    """Top-level function so ProcessPoolExecutor can pickle it."""
    return x * 2


def _fail_on_three(x):
    """Top-level function that raises on x==3."""
    if x == 3:
        raise RuntimeError("intentional failure")
    return x * 2


def _always_fail(x):
    """Top-level function that always raises."""
    raise RuntimeError("boom")


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
                "blur_score": 0.7,
                "exposure_score": 0.9,
                "face_score": 0.0,
                "composition_score": 0.6,
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

        assert report["version"] == "0.1.0"
        assert report["config"] == {"k": 10}
        assert report["total_analyzed"] == 3
        assert report["total_selected"] == 2
        assert len(report["selected"]) == 2
        assert len(report["skipped"]) == 1
        assert report["selected"][0]["index"] == 1
        assert report["selected"][0]["blur_score"] == 0.7

    def test_report_csv_created(self, tmp_path):
        """R6-L1: by default the CSV `filepath` column carries the
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
        """R6-L1: skipped entries also get the sanitized filepath.
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
    """Export merges into an existing folder (UAT change): the previous
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


class TestGenerateGallery:
    """Tests for generate_gallery."""

    def test_creates_gallery_html(self, tmp_path):
        selected = _make_selected(tmp_path, n=2)
        outdir = str(tmp_path / "gallery_out")
        os.makedirs(outdir)

        generate_gallery(selected, outdir)

        gallery_path = os.path.join(outdir, "gallery.html")
        assert os.path.isfile(gallery_path)
        with open(gallery_path) as f:
            html = f.read()
        assert "Best Photo Picker Gallery" in html
        assert "2 photos selected" in html

    def test_creates_thumbnails_dir(self, tmp_path):
        selected = _make_selected(tmp_path, n=1)
        outdir = str(tmp_path / "gallery_out")
        os.makedirs(outdir)

        generate_gallery(selected, outdir)

        thumb_dir = os.path.join(outdir, "thumbnails")
        assert os.path.isdir(thumb_dir)
        thumbs = os.listdir(thumb_dir)
        assert len(thumbs) == 1
        assert thumbs[0].endswith(".jpg")

    def test_thumbnail_naming(self, tmp_path):
        selected = _make_selected(tmp_path, n=2)
        outdir = str(tmp_path / "gallery_out")
        os.makedirs(outdir)

        generate_gallery(selected, outdir)

        thumb_dir = os.path.join(outdir, "thumbnails")
        files = sorted(os.listdir(thumb_dir))
        assert files[0] == "001_photo_0.jpg"
        assert files[1] == "002_photo_1.jpg"

    def test_cards_contain_metadata(self, tmp_path):
        selected = _make_selected(tmp_path, n=1)
        selected[0]["date_day"] = "2024-06-10"
        selected[0]["selection_reason"] = "diversity"
        outdir = str(tmp_path / "gallery_out")
        os.makedirs(outdir)

        generate_gallery(selected, outdir)

        with open(os.path.join(outdir, "gallery.html")) as f:
            html = f.read()
        assert "photo_0.jpg" in html
        assert "2024-06-10" in html
        assert "diversity" in html

    def test_gallery_with_broken_image_skips_card(self, tmp_path):
        outdir = str(tmp_path / "gallery_out")
        os.makedirs(outdir)
        selected = [
            {"filepath": "/nonexistent/bad.jpg", "date": "2024-01-01"},
        ]

        generate_gallery(selected, outdir)

        with open(os.path.join(outdir, "gallery.html")) as f:
            html = f.read()
        # Card should be skipped since thumbnail failed
        assert "1 photos selected" in html
        assert "bad.jpg" not in html  # no card for failed thumbnail

    def test_empty_selection(self, tmp_path):
        outdir = str(tmp_path / "gallery_out")
        os.makedirs(outdir)

        generate_gallery([], outdir)

        with open(os.path.join(outdir, "gallery.html")) as f:
            html = f.read()
        assert "0 photos selected" in html

    def test_date_fallback_to_date_field(self, tmp_path):
        """When date_day is absent, falls back to date[:10]."""
        selected = _make_selected(tmp_path, n=1)
        # Ensure no date_day key
        selected[0].pop("date_day", None)
        selected[0]["date"] = "2024-06-10 14:30:00"
        outdir = str(tmp_path / "gallery_out")
        os.makedirs(outdir)

        generate_gallery(selected, outdir)

        with open(os.path.join(outdir, "gallery.html")) as f:
            html = f.read()
        assert "2024-06-10" in html


# ══════════════════════════════════════════════════════════════════════════
#  timing.py
# ══════════════════════════════════════════════════════════════════════════


class TestTimer:
    """Tests for the Timer class."""

    def test_init_empty(self):
        t = Timer()
        assert t._sections == []

    def test_single_section(self):
        t = Timer()
        with t.section("test"):
            time.sleep(0.01)

        assert len(t._sections) == 1
        name, elapsed = t._sections[0]
        assert name == "test"
        assert elapsed >= 0.01

    def test_multiple_sections(self):
        t = Timer()
        with t.section("a"):
            time.sleep(0.01)
        with t.section("b"):
            time.sleep(0.01)

        assert len(t._sections) == 2
        assert t._sections[0][0] == "a"
        assert t._sections[1][0] == "b"

    def test_section_records_time_on_exception(self):
        t = Timer()
        with pytest.raises(ValueError, match="boom"), t.section("fail"):
            time.sleep(0.01)
            raise ValueError("boom")

        # Section should still be recorded despite exception
        assert len(t._sections) == 1
        assert t._sections[0][0] == "fail"
        assert t._sections[0][1] >= 0.01

    def test_summary_logs_info(self, caplog):
        t = Timer()
        with t.section("alpha"):
            pass
        with t.section("beta"):
            pass

        with caplog.at_level(logging.INFO, logger="bpp"):
            t.summary()

        assert any("Timing:" in r.message for r in caplog.records)
        assert any("alpha=" in r.message for r in caplog.records)
        assert any("beta=" in r.message for r in caplog.records)

    def test_summary_empty(self, caplog):
        t = Timer()
        with caplog.at_level(logging.INFO, logger="bpp"):
            t.summary()

        assert any("total 0.0s" in r.message for r in caplog.records)

    def test_section_context_manager_yields_none(self):
        t = Timer()
        with t.section("x") as val:
            assert val is None


# ══════════════════════════════════════════════════════════════════════════
#  concurrency.py
# ══════════════════════════════════════════════════════════════════════════


class TestGetWorkerCount:
    """Tests for get_worker_count."""

    def test_explicit_positive(self):
        assert get_worker_count(4) == 4

    def test_explicit_one(self):
        assert get_worker_count(1) == 1

    def test_auto_returns_at_least_1(self):
        with patch("os.cpu_count", return_value=1):
            assert get_worker_count(0) >= 1

    def test_auto_caps_at_8(self):
        with patch("os.cpu_count", return_value=32):
            assert get_worker_count(0) == 8

    def test_auto_halves_cpus(self):
        with patch("os.cpu_count", return_value=8):
            assert get_worker_count(0) == 4

    def test_auto_none_cpu_count(self):
        with patch("os.cpu_count", return_value=None):
            assert get_worker_count(0) == 1  # max(1, min(2//2, 8))


class TestParallelMap:
    """Tests for parallel_map."""

    def test_sequential_fallback_single_worker(self):
        results = parallel_map(lambda x: x * 2, [1, 2, 3, 4, 5], workers=1)
        assert results == [2, 4, 6, 8, 10]

    def test_sequential_fallback_few_items(self):
        """Items <= 2 should use sequential even with workers > 1."""
        results = parallel_map(lambda x: x + 1, [10, 20], workers=4)
        assert results == [11, 21]

    def test_sequential_empty_list(self):
        results = parallel_map(lambda x: x, [], workers=1)
        assert results == []

    def test_sequential_single_item(self):
        results = parallel_map(lambda x: x * 3, [7], workers=1)
        assert results == [21]

    def test_parallel_execution_preserves_order(self):
        """With enough items and workers, uses parallel path."""
        items = list(range(10))
        results = parallel_map(_double, items, workers=2)
        assert results == [i * 2 for i in range(10)]

    def test_parallel_worker_failure_returns_none(self):
        """Failed items should become None, not crash."""
        items = list(range(5))
        results = parallel_map(_fail_on_three, items, workers=2)
        assert results[0] == 0
        assert results[1] == 2
        assert results[2] == 4
        assert results[3] is None  # failed
        assert results[4] == 8

    def test_parallel_all_failures(self):
        """All items failing should return list of Nones."""
        items = [1, 2, 3, 4, 5]
        results = parallel_map(_always_fail, items, workers=2)
        assert all(r is None for r in results)
        assert len(results) == 5


# ══════════════════════════════════════════════════════════════════════════
#  paths.py
# ══════════════════════════════════════════════════════════════════════════


class TestSafeJoin:
    """Tests for safe_join."""

    def test_simple_filename(self, tmp_path):
        result = safe_join(str(tmp_path), "photo.jpg")
        assert result == os.path.join(str(tmp_path), "photo.jpg")

    def test_strips_directory_components(self, tmp_path):
        result = safe_join(str(tmp_path), "subdir/photo.jpg")
        assert os.path.basename(result) == "photo.jpg"
        assert result == os.path.join(str(tmp_path), "photo.jpg")

    def test_traversal_parent_stripped(self, tmp_path):
        """../etc/passwd should be stripped to just 'passwd'."""
        result = safe_join(str(tmp_path), "../../../etc/passwd")
        assert os.path.basename(result) == "passwd"
        # Result should be within base dir
        assert result.startswith(str(tmp_path))

    def test_absolute_path_stripped(self, tmp_path):
        result = safe_join(str(tmp_path), "/etc/passwd")
        assert os.path.basename(result) == "passwd"
        assert result.startswith(str(tmp_path))

    def test_dotdot_only(self, tmp_path):
        """Filename '..' should resolve to empty basename issue."""
        # os.path.basename("..") returns ".."
        # The realpath check should catch it
        base = str(tmp_path / "sub")
        os.makedirs(base)
        # ".." as filename: basename("..") == ".."
        # join(base, "..") resolves to parent, which is outside base
        with pytest.raises(ValueError, match="Path traversal"):
            safe_join(base, "..")

    def test_normal_nested_stripped(self, tmp_path):
        result = safe_join(str(tmp_path), "a/b/c/file.txt")
        assert result == os.path.join(str(tmp_path), "file.txt")

    def test_filename_with_spaces(self, tmp_path):
        result = safe_join(str(tmp_path), "my photo (1).jpg")
        assert result == os.path.join(str(tmp_path), "my photo (1).jpg")

    def test_unicode_filename(self, tmp_path):
        result = safe_join(str(tmp_path), "foto_\u00e9t\u00e9.jpg")
        assert "foto_\u00e9t\u00e9.jpg" in result


class TestIsSafeArchiveMember:
    """Tests for is_safe_archive_member \u2014 the path-traversal guard
    used by zip / tar extraction in analyze_worker. Unlike safe_join,
    this preserves intra-archive subdirectories; the threat model is
    only about escaping the destination tree, not flattening
    legitimate subdirs."""

    def test_simple_filename_allowed(self, tmp_path):
        from bpp.utils.paths import is_safe_archive_member

        assert is_safe_archive_member("photo.jpg", str(tmp_path)) is True

    def test_subdirectory_path_allowed(self, tmp_path):
        """Real archives have nested paths like `photos/2024/img.jpg`."""
        from bpp.utils.paths import is_safe_archive_member

        assert is_safe_archive_member("photos/2024/img.jpg", str(tmp_path)) is True

    def test_parent_traversal_rejected(self, tmp_path):
        from bpp.utils.paths import is_safe_archive_member

        assert is_safe_archive_member("../escape.jpg", str(tmp_path)) is False

    def test_deep_parent_traversal_rejected(self, tmp_path):
        from bpp.utils.paths import is_safe_archive_member

        assert is_safe_archive_member("../../../etc/passwd", str(tmp_path)) is False

    def test_absolute_path_rejected(self, tmp_path):
        """A member name like `/etc/passwd` (absolute) is the classic
        tarbomb shape \u2014 must not extract to /etc/passwd."""
        from bpp.utils.paths import is_safe_archive_member

        assert is_safe_archive_member("/etc/passwd", str(tmp_path)) is False

    def test_traversal_through_subdir_rejected(self, tmp_path):
        """`subdir/../../escape` resolves outside even though it
        starts with a legitimate subdir component."""
        from bpp.utils.paths import is_safe_archive_member

        assert is_safe_archive_member("subdir/../../escape.jpg", str(tmp_path)) is False

    def test_dotdot_only_rejected(self, tmp_path):
        from bpp.utils.paths import is_safe_archive_member

        assert is_safe_archive_member("..", str(tmp_path)) is False

    def test_extract_dir_itself_allowed(self, tmp_path):
        """An empty member name resolves to the extract dir itself \u2014
        zero-byte but inside, so allowed (zip directory entries can
        look like this)."""
        from bpp.utils.paths import is_safe_archive_member

        assert is_safe_archive_member("", str(tmp_path)) is True


class TestIsSafeTarMember:
    """is_safe_tar_member adds tar-type filtering on top of the
    path-containment check. Symlinks / hardlinks / device nodes can
    pass the name check (`safe.jpg` lands inside extract_dir) but
    create dangerous filesystem entries on extraction. The Python
    3.11 fallback path in analyze_worker uses this; on 3.12+ the
    stdlib `filter="data"` does the same job natively.
    """

    def _build_tar_with_member(self, tmp_path, name: str, member_type: bytes):
        """Build a 1-entry tar at tmp_path/test.tar with a single
        member of the given type. Returns the tarfile path."""
        import tarfile

        path = tmp_path / "test.tar"
        with tarfile.open(path, "w") as tf:
            info = tarfile.TarInfo(name=name)
            info.type = member_type
            if member_type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                info.linkname = "/etc/passwd"
            elif member_type == tarfile.DIRTYPE:
                info.mode = 0o755
            else:
                info.size = 0
            tf.addfile(info)
        return path

    def test_regular_file_allowed(self, tmp_path):
        import tarfile

        from bpp.utils.paths import is_safe_tar_member

        tar_path = self._build_tar_with_member(tmp_path, "photo.jpg", tarfile.REGTYPE)
        with tarfile.open(tar_path) as tf:
            members = tf.getmembers()
        assert is_safe_tar_member(members[0], str(tmp_path)) is True

    def test_directory_allowed(self, tmp_path):
        import tarfile

        from bpp.utils.paths import is_safe_tar_member

        tar_path = self._build_tar_with_member(tmp_path, "photos/", tarfile.DIRTYPE)
        with tarfile.open(tar_path) as tf:
            members = tf.getmembers()
        assert is_safe_tar_member(members[0], str(tmp_path)) is True

    def test_symlink_rejected_even_with_safe_name(self, tmp_path):
        """The malicious case: tar entry named `safe.jpg` whose type
        is symlink. The name passes containment, but the type is a
        symlink that would land in extract_dir/safe.jpg pointing at
        /etc/passwd. Reject it."""
        import tarfile

        from bpp.utils.paths import is_safe_tar_member

        tar_path = self._build_tar_with_member(tmp_path, "safe.jpg", tarfile.SYMTYPE)
        with tarfile.open(tar_path) as tf:
            members = tf.getmembers()
        assert is_safe_tar_member(members[0], str(tmp_path)) is False

    def test_hardlink_rejected(self, tmp_path):
        import tarfile

        from bpp.utils.paths import is_safe_tar_member

        tar_path = self._build_tar_with_member(tmp_path, "safe.jpg", tarfile.LNKTYPE)
        with tarfile.open(tar_path) as tf:
            members = tf.getmembers()
        assert is_safe_tar_member(members[0], str(tmp_path)) is False

    def test_fifo_rejected(self, tmp_path):
        import tarfile

        from bpp.utils.paths import is_safe_tar_member

        tar_path = self._build_tar_with_member(tmp_path, "fifo", tarfile.FIFOTYPE)
        with tarfile.open(tar_path) as tf:
            members = tf.getmembers()
        assert is_safe_tar_member(members[0], str(tmp_path)) is False

    def test_traversal_still_rejected(self, tmp_path):
        """Type filter doesn't replace path filter \u2014 both must pass.
        A regular file with `../escape` name must still be rejected."""
        import tarfile

        from bpp.utils.paths import is_safe_tar_member

        tar_path = self._build_tar_with_member(tmp_path, "../escape.jpg", tarfile.REGTYPE)
        with tarfile.open(tar_path) as tf:
            members = tf.getmembers()
        assert is_safe_tar_member(members[0], str(tmp_path)) is False


# ══════════════════════════════════════════════════════════════════════════
#  logging.py
# ══════════════════════════════════════════════════════════════════════════


class TestSetupLogging:
    """Tests for setup_logging and get_logger."""

    def test_get_logger_returns_logger(self):
        logger = get_logger("test.module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.module"

    def test_get_logger_same_name_returns_same(self):
        a = get_logger("test.same")
        b = get_logger("test.same")
        assert a is b

    def test_get_logger_different_names(self):
        a = get_logger("test.one")
        b = get_logger("test.two")
        assert a is not b

    def test_setup_logging_idempotent(self):
        """Calling setup_logging twice should not add duplicate handlers."""
        import bpp.utils.logging as log_mod

        # Reset to ensure clean state
        original = log_mod._CONFIGURED
        try:
            log_mod._CONFIGURED = False
            setup_logging(debug=False)
            root = logging.getLogger("bpp")
            handler_count = len(root.handlers)

            # Call again — should be a no-op
            setup_logging(debug=True)
            assert len(root.handlers) == handler_count
        finally:
            log_mod._CONFIGURED = original

    def test_setup_logging_debug_level(self):
        import bpp.utils.logging as log_mod

        original = log_mod._CONFIGURED
        try:
            log_mod._CONFIGURED = False
            setup_logging(debug=True)
            root = logging.getLogger("bpp")
            assert root.level == logging.DEBUG
        finally:
            log_mod._CONFIGURED = original

    def test_setup_logging_info_level(self):
        import bpp.utils.logging as log_mod

        original = log_mod._CONFIGURED
        root = logging.getLogger("bpp")
        # Remove handlers to get a clean slate
        old_handlers = root.handlers[:]
        root.handlers.clear()
        old_level = root.level
        try:
            log_mod._CONFIGURED = False
            setup_logging(debug=False)
            assert root.level == logging.INFO
        finally:
            log_mod._CONFIGURED = original
            root.handlers = old_handlers
            root.level = old_level

    def test_setup_logging_adds_stream_handler(self):
        """R11-L4: handlers now live on the root logger so third-party
        plugin loggers also pass through RedactingFormatter. This test
        was previously checking the `bpp` namespace logger; updated to
        verify the root logger, which is where setup_logging() now
        attaches the redacting StreamHandler."""
        import bpp.utils.logging as log_mod

        original = log_mod._CONFIGURED
        root_logger = logging.getLogger()  # root, not "bpp"
        old_root_handlers = root_logger.handlers[:]
        bpp_logger = logging.getLogger("bpp")
        old_bpp_handlers = bpp_logger.handlers[:]
        root_logger.handlers.clear()
        try:
            log_mod._CONFIGURED = False
            setup_logging()
            has_stream = any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers)
            assert has_stream, (
                "setup_logging() must attach a StreamHandler to the root "
                "logger (not just 'bpp') so third-party plugin loggers "
                "also pass through RedactingFormatter"
            )
        finally:
            log_mod._CONFIGURED = original
            root_logger.handlers = old_root_handlers
            bpp_logger.handlers = old_bpp_handlers


# ══════════════════════════════════════════════════════════════════════════
#  Additional edge-case tests
# ══════════════════════════════════════════════════════════════════════════


class TestExportReturnValue:
    """Tests for ``export_selected`` returning an ``ExportResult``.

    Prior to the H1 release-audit fix the function returned a plain
    ``(exported, failed)`` tuple. It now returns an ``ExportResult``
    dataclass with the additional ``disk_error`` field so the UI can
    distinguish per-photo failures from a 'whole-export-aborted-due-to-
    disk-full' state. ``ExportResult`` is iterable so legacy callers
    that still tuple-unpack ``(exported, failed)`` keep working.
    """

    def test_returns_export_result_dataclass(self, tmp_path):
        from bpp.output.export import ExportResult

        selected = _make_selected(tmp_path, n=2)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        result = export_selected(selected, analysis, outdir)
        assert isinstance(result, ExportResult)
        # Iterable for back-compat with tuple-unpack callers.
        exported, failed = result
        assert exported == result.exported
        assert failed == result.failed

    def test_all_succeed(self, tmp_path):
        selected = _make_selected(tmp_path, n=3)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        exported, failed = export_selected(selected, analysis, outdir)
        assert exported == 3
        assert failed == 0

    def test_partial_failure(self, tmp_path):
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

        exported, failed = export_selected(selected, analysis, outdir)
        assert exported == 1
        assert failed == 1

    def test_all_fail(self, tmp_path):
        selected = [
            {"filepath": "/nonexistent/a.jpg", "date": "2024-01-01"},
            {"filepath": "/nonexistent/b.jpg", "date": "2024-01-02"},
        ]
        analysis = selected[:]
        outdir = str(tmp_path / "out")

        exported, failed = export_selected(selected, analysis, outdir)
        assert exported == 0
        assert failed == 2

    def test_empty_selection(self, tmp_path):
        outdir = str(tmp_path / "out")

        exported, failed = export_selected([], [], outdir)
        assert exported == 0
        assert failed == 0


class TestExportEdgeCases:
    """Additional edge cases for export_selected."""

    def test_dest_naming_format(self, tmp_path):
        """File numbering uses 3-digit zero-padded index."""
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir)

        files = os.listdir(os.path.join(outdir, "selected"))
        assert files[0].startswith("001_")

    def test_report_json_selected_entries_have_all_fields(self, tmp_path):
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir)

        with open(os.path.join(outdir, "report.json")) as f:
            report = json.load(f)

        entry = report["selected"][0]
        expected_keys = {
            "index",
            "filepath",
            "date",
            "aggregate_score",
            "blur_score",
            "exposure_score",
            "face_score",
            "composition_score",
            "selection_reason",
            "cluster_size",
        }
        assert set(entry.keys()) == expected_keys

    def test_copy_preserves_content(self, tmp_path):
        """Explicit metadata-preserving copy should have identical content."""
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, mode="copy", strip_metadata=False)

        src = selected[0]["filepath"]
        dest = os.path.join(outdir, "selected", "001_photo_0.jpg")
        with open(src, "rb") as f1, open(dest, "rb") as f2:
            assert f1.read() == f2.read()


class TestGalleryTemplates:
    """Tests for gallery template strings."""

    def test_gallery_template_has_count_placeholder(self):
        assert "%(count)d" in GALLERY_TEMPLATE

    def test_gallery_template_has_cards_placeholder(self):
        assert "%(cards)s" in GALLERY_TEMPLATE

    def test_card_template_has_required_placeholders(self):
        for key in ("thumb", "name", "date", "score", "reason"):
            assert f"%({key})" in CARD_TEMPLATE, (
                f"CARD_TEMPLATE missing placeholder %({key})s — gallery render will skip this field"
            )


# ══════════════════════════════════════════════════════════════════════════
#  Export resize/format options
# ══════════════════════════════════════════════════════════════════════════


class TestExportResizeFormat:
    """Tests for export_selected with format conversion and resize."""

    def test_original_format_no_conversion(self, tmp_path):
        """format='original' copies files without conversion."""
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, fmt="original", strip_metadata=False)

        dest = os.path.join(outdir, "selected", "001_photo_0.jpg")
        with open(selected[0]["filepath"], "rb") as f1, open(dest, "rb") as f2:
            assert f1.read() == f2.read()

    def test_convert_to_jpeg(self, tmp_path):
        """format='jpeg' converts PNG sources to JPEG."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        src = src_dir / "photo.png"
        Image.new("RGB", (200, 200), "blue").save(str(src), "PNG")

        selected = [{"filepath": str(src), "date": "2024-01-01"}]
        analysis = selected[:]
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, fmt="jpeg")

        # Output should have .jpg extension
        files = os.listdir(os.path.join(outdir, "selected"))
        assert len(files) == 1
        assert files[0].endswith(".jpg")

        dest = os.path.join(outdir, "selected", files[0])
        with Image.open(dest) as img:
            assert img.format == "JPEG"

    def test_convert_to_png(self, tmp_path):
        """format='png' converts JPEG sources to PNG."""
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, fmt="png")

        files = os.listdir(os.path.join(outdir, "selected"))
        assert len(files) == 1
        assert files[0].endswith(".png")

        dest = os.path.join(outdir, "selected", files[0])
        with Image.open(dest) as img:
            assert img.format == "PNG"

    def test_resize_max_dimension(self, tmp_path):
        """max_size limits the longest dimension."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        src = src_dir / "big.jpg"
        Image.new("RGB", (4000, 3000), "red").save(str(src), "JPEG")

        selected = [{"filepath": str(src), "date": "2024-01-01"}]
        analysis = selected[:]
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, max_size=2048)

        dest = os.path.join(outdir, "selected", "001_big.jpg")
        with Image.open(dest) as img:
            assert max(img.size) <= 2048
            # Aspect ratio preserved
            assert abs(img.size[0] / img.size[1] - 4000 / 3000) < 0.01

    def test_resize_smaller_image_unchanged(self, tmp_path):
        """Images smaller than max_size are not upscaled."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        src = src_dir / "small.jpg"
        Image.new("RGB", (800, 600), "green").save(str(src), "JPEG")

        selected = [{"filepath": str(src), "date": "2024-01-01"}]
        analysis = selected[:]
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, max_size=2048)

        dest = os.path.join(outdir, "selected", "001_small.jpg")
        with Image.open(dest) as img:
            assert img.size == (800, 600)

    def test_jpeg_quality(self, tmp_path):
        """quality param affects JPEG file size."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        src = src_dir / "photo.jpg"
        Image.new("RGB", (500, 500), "red").save(str(src), "JPEG", quality=95)

        selected = [{"filepath": str(src), "date": "2024-01-01"}]
        analysis = selected[:]

        out_low = str(tmp_path / "out_low")
        out_high = str(tmp_path / "out_high")

        export_selected(selected, analysis, out_low, fmt="jpeg", quality=30)
        export_selected(selected, analysis, out_high, fmt="jpeg", quality=95)

        low_file = os.path.join(out_low, "selected", "001_photo.jpg")
        high_file = os.path.join(out_high, "selected", "001_photo.jpg")
        assert os.path.getsize(low_file) < os.path.getsize(high_file)

    def test_resize_and_format_combined(self, tmp_path):
        """Can resize and change format in one export."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        src = src_dir / "big.png"
        Image.new("RGB", (5000, 3000), "blue").save(str(src), "PNG")

        selected = [{"filepath": str(src), "date": "2024-01-01"}]
        analysis = selected[:]
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, fmt="jpeg", max_size=1024, quality=80)

        files = os.listdir(os.path.join(outdir, "selected"))
        assert len(files) == 1
        assert files[0].endswith(".jpg")

        dest = os.path.join(outdir, "selected", files[0])
        with Image.open(dest) as img:
            assert img.format == "JPEG"
            assert max(img.size) <= 1024

    def test_rgba_to_jpeg_drops_alpha(self, tmp_path):
        """RGBA images converted to JPEG should drop alpha channel."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        src = src_dir / "alpha.png"
        Image.new("RGBA", (200, 200), (255, 0, 0, 128)).save(str(src), "PNG")

        selected = [{"filepath": str(src), "date": "2024-01-01"}]
        analysis = selected[:]
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, fmt="jpeg")

        files = os.listdir(os.path.join(outdir, "selected"))
        dest = os.path.join(outdir, "selected", files[0])
        with Image.open(dest) as img:
            assert img.mode == "RGB"

    def test_hardlink_mode_ignores_format(self, tmp_path):
        """Hardlink/symlink modes should ignore format/resize options."""
        selected = _make_selected(tmp_path, n=1)
        analysis = _make_analysis(selected, extra=0)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, mode="hardlink", fmt="png", max_size=100)

        # Should be hardlinked, not converted
        dest = os.path.join(outdir, "selected", "001_photo_0.jpg")
        src_stat = os.stat(selected[0]["filepath"])
        dst_stat = os.stat(dest)
        assert src_stat.st_ino == dst_stat.st_ino

    def test_default_quality_is_85(self, tmp_path):
        """Default JPEG quality should be 85 when not specified."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        src = src_dir / "photo.png"
        Image.new("RGB", (200, 200), "red").save(str(src), "PNG")

        selected = [{"filepath": str(src), "date": "2024-01-01"}]
        analysis = selected[:]
        outdir = str(tmp_path / "out")

        # Just verify it exports without error at default quality
        export_selected(selected, analysis, outdir, fmt="jpeg")
        dest = os.path.join(outdir, "selected")
        assert len(os.listdir(dest)) == 1


class TestExportMetadataPrivacy:
    """R7-L1: default copy/original exports must not preserve EXIF metadata."""

    def test_default_copy_export_strips_exif_metadata(self, tmp_path):
        src = tmp_path / "private.jpg"
        img = Image.new("RGB", (10, 10), "red")
        exif = Image.Exif()
        exif[0x010F] = "PrivateCameraMake"  # Make
        exif[0x013B] = "Alice Owner"  # Artist
        img.save(src, "JPEG", exif=exif)

        item = {"filepath": str(src), "aggregate_score": 0.9}
        outdir = str(tmp_path / "out")

        export_selected([item], [item], outdir, mode="copy", fmt="original", gallery=False)

        exported = tmp_path / "out" / "selected" / "001_private.jpg"
        exported_exif = Image.open(exported).getexif()
        assert exported_exif.get(0x010F) is None
        assert exported_exif.get(0x013B) is None

    def test_copy_export_can_preserve_metadata_when_explicit(self, tmp_path):
        src = tmp_path / "private.jpg"
        img = Image.new("RGB", (10, 10), "red")
        exif = Image.Exif()
        exif[0x010F] = "PrivateCameraMake"
        img.save(src, "JPEG", exif=exif)

        item = {"filepath": str(src), "aggregate_score": 0.9}
        outdir = str(tmp_path / "out")

        export_selected(
            [item],
            [item],
            outdir,
            mode="copy",
            fmt="original",
            gallery=False,
            strip_metadata=False,
        )

        exported = tmp_path / "out" / "selected" / "001_private.jpg"
        assert Image.open(exported).getexif().get(0x010F) == "PrivateCameraMake"


class TestExportSelectedZipMode:
    """mode='zip' bundles the (processed) selection into a single
    best-photos.zip and removes the loose selected/ folder, so the user
    gets ONE file to hand off. Honors the same format/resize/strip
    options as copy mode (Option A)."""

    def test_zip_creates_single_archive(self, tmp_path):
        import zipfile

        selected = _make_selected(tmp_path, n=3)
        analysis = _make_analysis(selected)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, mode="zip", gallery=False)

        archive = os.path.join(outdir, "best-photos.zip")
        assert os.path.isfile(archive), "zip bundle should be written to outdir"
        # The loose folder is gone — the whole point is a single file.
        assert not os.path.isdir(os.path.join(outdir, "selected"))
        with zipfile.ZipFile(archive) as z:
            names = sorted(z.namelist())
        assert names == ["001_photo_0.jpg", "002_photo_1.jpg", "003_photo_2.jpg"]

    def test_zip_honors_format_conversion(self, tmp_path):
        """Processed bytes go INTO the archive — fmt='png' yields .png
        entries (proves the copy-style processing path runs in bundle
        mode, not a raw zip of originals)."""
        import zipfile

        selected = _make_selected(tmp_path, n=2)  # JPEG sources
        analysis = _make_analysis(selected)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, mode="zip", fmt="png", gallery=False)

        with zipfile.ZipFile(os.path.join(outdir, "best-photos.zip")) as z:
            names = sorted(z.namelist())
        assert all(n.endswith(".png") for n in names), names

    def test_zip_still_writes_reports(self, tmp_path):
        """report.json/csv stay loose in outdir (diagnostics, not part of
        the handoff file)."""
        selected = _make_selected(tmp_path, n=2)
        analysis = _make_analysis(selected)
        outdir = str(tmp_path / "out")

        result = export_selected(selected, analysis, outdir, mode="zip", gallery=False)

        assert os.path.isfile(os.path.join(outdir, "report.json"))
        assert result.exported == 2
        assert result.failed == 0

    def test_zip_skips_gallery(self, tmp_path):
        """A folder-relative gallery can't ride inside a single zip, so
        bundle mode skips it even when gallery=True."""
        selected = _make_selected(tmp_path, n=2)
        analysis = _make_analysis(selected)
        outdir = str(tmp_path / "out")

        export_selected(selected, analysis, outdir, mode="zip", gallery=True)

        assert os.path.isfile(os.path.join(outdir, "best-photos.zip"))
        assert not os.path.isfile(os.path.join(outdir, "gallery.html"))
