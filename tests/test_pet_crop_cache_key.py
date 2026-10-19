"""TDD tests: pet crop cache must include bbox in the key."""

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

    def test_a_database_row_keys_the_same_crop_as_a_tuple(self, tmp_path):
        """The endpoint reads the box out of a sqlite3.Row; the key must
        come from the four numbers, not the row object (whose str() is
        its address, so no request ever hit the cache)."""
        import sqlite3

        from PIL import Image

        img_path = str(tmp_path / "photo.jpg")
        Image.new("RGB", (200, 200), "red").save(img_path, "JPEG")
        crop_dir = str(tmp_path / "crops")
        os.makedirs(crop_dir)
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT 10 AS bbox_x, 10 AS bbox_y, 50 AS bbox_w, 50 AS bbox_h"
        ).fetchone()

        from_row = _generate_pet_crop(img_path, row, crop_dir, "abc", 0)
        from_tuple = _generate_pet_crop(img_path, (10, 10, 50, 50), crop_dir, "abc", 0)
        assert from_row == from_tuple
        assert len(os.listdir(crop_dir)) == 1
