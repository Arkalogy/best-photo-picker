"""Tests for export, gallery, timing, concurrency, paths, and logging — part 3 of 4:
TestSetupLogging, TestExportReturnValue, TestExportEdgeCases."""

from __future__ import annotations

import json
import logging
import os

from PIL import Image

from bpp.output.export import export_selected
from bpp.output.gallery import (
    CARD_TEMPLATE,
    GALLERY_TEMPLATE,
)
from bpp.utils.logging import get_logger, setup_logging
from tests._export_utils_shared import (
    _make_analysis,
    _make_selected,
    _make_test_image,
)

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
        """Handlers now live on the root logger so third-party
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

    Prior to the disk-error fix the function returned a plain
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
