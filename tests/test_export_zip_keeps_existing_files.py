"""Bundling an export into a zip removes only what this export staged,
never files an earlier export left in the same folder."""

from __future__ import annotations

import os
import zipfile

from PIL import Image

from bpp.output.export import ZIP_BUNDLE_NAME, export_selected


def test_zip_export_leaves_earlier_exports_alone(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = lib / "new.jpg"
    Image.new("RGB", (8, 8)).save(src, "JPEG")
    outdir = tmp_path / "out"
    earlier = outdir / "selected" / "old.jpg"
    earlier.parent.mkdir(parents=True)
    earlier.write_bytes(b"from an earlier export")

    item = {"filepath": str(src), "aggregate_score": 0.5}
    result = export_selected([item], [item], str(outdir), mode="zip", library_path=str(lib))

    assert result.failures == []
    assert earlier.exists(), "the earlier export was deleted"
    with zipfile.ZipFile(os.path.join(str(outdir), ZIP_BUNDLE_NAME)) as z:
        assert any(n.endswith("new.jpg") for n in z.namelist())
