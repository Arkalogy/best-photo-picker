"""Tests for the M15 scan-progress callback in scan_images."""

from __future__ import annotations

import os
from itertools import pairwise

from PIL import Image


def _make_image(path: str) -> None:
    Image.new("RGB", (4, 4), "blue").save(path, "JPEG")


class TestScanImagesOnProgress:
    def test_callback_invoked_on_recursive_scan(self, tmp_path):
        """Progress callback fires periodically (every ~500 files) during
        a recursive walk so the worker can emit scan_progress events
        instead of presenting a black box during the scan."""
        from bpp.io_scan import _SCAN_PROGRESS_EVERY, scan_images

        # Build enough files to trigger at least one mid-scan tick.
        # _SCAN_PROGRESS_EVERY is the cadence; we make 2x that so we
        # get one mid-scan callback + the trailing "final" callback.
        target = _SCAN_PROGRESS_EVERY * 2 + 5
        for i in range(target):
            _make_image(str(tmp_path / f"img_{i:05d}.jpg"))

        calls: list[tuple[int, int]] = []
        result = scan_images(
            str(tmp_path),
            extensions=["jpg"],
            recursive=True,
            on_progress=lambda s, m: calls.append((s, m)),
        )
        assert len(result) == target
        # At least 2 calls: one mid-scan tick + the trailing final tick
        assert len(calls) >= 2
        # The arguments are monotonic and the last one matches reality
        for prev, cur in pairwise(calls):
            assert cur[0] >= prev[0], f"scanned counter regressed: {prev[0]} → {cur[0]}"
            assert cur[1] >= prev[1], f"matched counter regressed: {prev[1]} → {cur[1]}"
        assert calls[-1][0] >= target, f"final scanned ({calls[-1][0]}) < files written ({target})"
        assert calls[-1][1] == target, (
            f"final matched ({calls[-1][1]}) != target ({target}) — jpg filter dropped some"
        )

    def test_callback_optional(self, tmp_path):
        """Omitting on_progress preserves the original signature."""
        from bpp.io_scan import scan_images

        for i in range(3):
            _make_image(str(tmp_path / f"img_{i}.jpg"))
        result = scan_images(str(tmp_path), extensions=["jpg"], recursive=True)
        assert len(result) == 3

    def test_non_recursive_does_not_invoke(self, tmp_path):
        """Non-recursive scans are fast — progress reporting would just
        be noise, so the callback is never invoked in that mode."""
        from bpp.io_scan import scan_images

        for i in range(50):
            _make_image(str(tmp_path / f"img_{i}.jpg"))
        calls: list[tuple[int, int]] = []
        scan_images(
            str(tmp_path),
            extensions=["jpg"],
            recursive=False,
            on_progress=lambda s, m: calls.append((s, m)),
        )
        assert calls == []

    def test_callback_exception_does_not_break_scan(self, tmp_path):
        """A buggy on_progress hook must not abort the scan — log + continue."""
        from bpp.io_scan import _SCAN_PROGRESS_EVERY, scan_images

        for i in range(_SCAN_PROGRESS_EVERY + 5):
            _make_image(str(tmp_path / f"img_{i:05d}.jpg"))

        def bad(scanned: int, matched: int) -> None:
            raise RuntimeError("boom")

        result = scan_images(
            str(tmp_path),
            extensions=["jpg"],
            recursive=True,
            on_progress=bad,
        )
        # Scan still completed; full result returned.
        assert len(result) == _SCAN_PROGRESS_EVERY + 5

    def test_skips_internal_dirs(self, tmp_path):
        """Skip dirs (web_thumbs, face_crops, etc.) still excluded."""
        from bpp.io_scan import scan_images

        os.makedirs(str(tmp_path / "web_thumbs"))
        _make_image(str(tmp_path / "web_thumbs" / "skip.jpg"))
        _make_image(str(tmp_path / "keep.jpg"))
        result = scan_images(str(tmp_path), extensions=["jpg"], recursive=True)
        assert len(result) == 1
        assert result[0].endswith("keep.jpg")
