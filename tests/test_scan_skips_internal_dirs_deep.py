"""Scanning a library root must not descend into bpp's own folders:
cache/pet_crops and data/extracted hold JPEGs that are not photos."""

from __future__ import annotations

from bpp.io_scan import scan_images


def test_children_of_internal_dirs_are_not_photos(tmp_path):
    (tmp_path / "photos" / "batch").mkdir(parents=True)
    (tmp_path / "photos" / "batch" / "a.jpg").write_bytes(b"x")
    (tmp_path / "cache" / "pet_crops").mkdir(parents=True)
    (tmp_path / "cache" / "pet_crops" / "pet_abc_0_1.jpg").write_bytes(b"x")
    (tmp_path / "data" / "extracted").mkdir(parents=True)
    (tmp_path / "data" / "extracted" / "y.jpg").write_bytes(b"x")
    (tmp_path / "face_crops" / "deep").mkdir(parents=True)
    (tmp_path / "face_crops" / "deep" / "z.jpg").write_bytes(b"x")
    found = scan_images(str(tmp_path), recursive=True, extensions=["jpg"])
    assert found == [str(tmp_path / "photos" / "batch" / "a.jpg")]
