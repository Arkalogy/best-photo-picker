"""Tests for export, gallery, timing, concurrency, paths, and logging — part 4 of 4:
TestExportMetadataPrivacy, TestExportSelectedZipMode."""

from __future__ import annotations

import os

from PIL import Image

from bpp.output.export import export_selected
from tests._export_utils_shared import (
    _make_analysis,
    _make_selected,
)


class TestExportMetadataPrivacy:
    """Default copy/original exports must not preserve EXIF metadata."""

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
