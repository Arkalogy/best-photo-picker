"""TDD tests for H-12: pet crop cache must include bbox in the key."""

from __future__ import annotations

import os

from bpp.web.bp_pets import _generate_pet_crop


class TestPetCropCacheKey:
    def test_different_bbox_produces_different_crop_file(self, tmp_path):
        """Two different bboxes for the same photo+index must NOT share a crop."""
        from PIL import Image

        img_path = str(tmp_path / "photo.jpg")
        Image.new("RGB", (200, 200), "green").save(img_path, "JPEG")
        crop_dir = str(tmp_path / "crops")
        os.makedirs(crop_dir)

        bbox_a = (10, 10, 50, 50)
        bbox_b = (80, 80, 50, 50)

        path_a = _generate_pet_crop(img_path, bbox_a, crop_dir, "abc", 0)
        path_b = _generate_pet_crop(img_path, bbox_b, crop_dir, "abc", 0)

        assert path_a is not None
        assert path_b is not None
        # Must be different files (different bbox = different crop)
        assert path_a != path_b

    def test_same_bbox_reuses_cached_crop(self, tmp_path):
        """Same bbox should return the cached file (no regeneration)."""
        from PIL import Image

        img_path = str(tmp_path / "photo.jpg")
        Image.new("RGB", (200, 200), "blue").save(img_path, "JPEG")
        crop_dir = str(tmp_path / "crops")
        os.makedirs(crop_dir)

        bbox = (10, 10, 50, 50)
        path1 = _generate_pet_crop(img_path, bbox, crop_dir, "xyz", 0)
        mtime1 = os.path.getmtime(path1)
        path2 = _generate_pet_crop(img_path, bbox, crop_dir, "xyz", 0)

        assert path1 == path2
        # File not regenerated
        assert os.path.getmtime(path2) == mtime1
