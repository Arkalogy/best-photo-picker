"""Tests for demo mode."""

from __future__ import annotations

import os

from bpp.demo.generate import generate_sample_photos


class TestGenerateSamplePhotos:
    def test_generates_expected_count(self, tmp_path):
        outdir = str(tmp_path / "photos")
        paths = generate_sample_photos(outdir, count=12)
        # 12 photos + 1 near-duplicate = 13
        assert len(paths) == 13
        for p in paths:
            assert os.path.isfile(p)

    def test_images_are_valid_jpegs(self, tmp_path):
        from PIL import Image

        outdir = str(tmp_path / "photos")
        paths = generate_sample_photos(outdir, count=6)
        for p in paths:
            img = Image.open(p)
            assert img.format == "JPEG"
            assert img.size[0] > 0 and img.size[1] > 0

    def test_deterministic_with_same_seed(self, tmp_path):
        dir1 = str(tmp_path / "a")
        dir2 = str(tmp_path / "b")
        paths1 = generate_sample_photos(dir1, count=4, seed=99)
        paths2 = generate_sample_photos(dir2, count=4, seed=99)
        for p1, p2 in zip(paths1, paths2, strict=True):
            assert os.path.getsize(p1) == os.path.getsize(p2)

    def test_creates_output_directory(self, tmp_path):
        outdir = str(tmp_path / "nested" / "dir")
        assert not os.path.exists(outdir)
        generate_sample_photos(outdir, count=2)
        assert os.path.isdir(outdir)

    def test_near_duplicate_exists(self, tmp_path):
        outdir = str(tmp_path / "photos")
        paths = generate_sample_photos(outdir, count=4)
        names = [os.path.basename(p) for p in paths]
        assert "IMG_0000_dup.jpg" in names


class TestDemoCommand:
    def test_cli_demo_subcommand_exists(self):
        from bpp.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["demo", "--no-browser", "--keep"])
        assert args.command == "demo"
        assert args.no_browser is True
        assert args.keep is True

    def test_cli_demo_defaults(self):
        from bpp.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["demo"])
        assert args.command == "demo"
        assert args.port == 5001
        assert args.no_browser is False
        assert args.keep is False
