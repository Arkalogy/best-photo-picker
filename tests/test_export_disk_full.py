"""Disk-full / permission-denied categorization in export_selected.

Release audit finding H1: the export loop used to catch every OSError
the same way — appended to ``failed`` and kept trying. On a USB stick
that filled mid-export, the user got "36 exported, 14 failed" with no
indication every photo from #37 onward failed for the same reason.

The fix categorises ``e.errno`` against the fatal set
(ENOSPC / EDQUOT / EACCES / EPERM / EROFS), surfaces a ``disk_error``
field in the returned ``ExportResult``, and aborts the loop — the UI
then shows "Disk full — stopped at photo N" instead of a generic count.
"""

from __future__ import annotations

import errno
import os
from typing import Any
from unittest.mock import patch

import pytest

from bpp.output.export import ExportResult, export_selected


def _make_jpg(path: str) -> None:
    """Write a 1x1 JPEG so export_selected has something real to copy."""
    from PIL import Image

    Image.new("RGB", (1, 1), color=(255, 0, 0)).save(path, format="JPEG")


def _selection(tmp_path, n: int) -> list[dict[str, Any]]:
    """Build N test photos and the matching selection list."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    items = []
    for i in range(n):
        p = str(src_dir / f"photo_{i:03d}.jpg")
        _make_jpg(p)
        items.append({"filepath": p, "aggregate_score": 0.5, "date": "2026-05-01"})
    return items


class TestExportDiskFullCategorization:
    """ExportResult.disk_error surfaces when the loop aborts on a fatal OSError."""

    @pytest.mark.parametrize(
        ("err_code", "expected_category"),
        [
            (errno.ENOSPC, "no_space"),
            (errno.EDQUOT, "no_space"),
            (errno.EACCES, "permission"),
            (errno.EPERM, "permission"),
            (errno.EROFS, "read_only_fs"),
        ],
    )
    def test_fatal_oserror_categorises_and_aborts(
        self,
        tmp_path,
        err_code: int,
        expected_category: str,
    ) -> None:
        """Every fatal errno category aborts the loop at the first failure."""
        selection = _selection(tmp_path, 5)
        outdir = str(tmp_path / "out")

        # Let photo #1 copy normally; raise on photo #2 and onwards. The
        # loop should abort after #2 instead of trying #3, #4, #5 too.
        real_copy = __import__("shutil").copy2
        calls = {"n": 0}

        def fake_copy(src, dest, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return real_copy(src, dest, *a, **kw)
            raise OSError(err_code, os.strerror(err_code))

        with patch("shutil.copy2", side_effect=fake_copy):
            result = export_selected(selection, [], outdir, mode="copy", strip_metadata=False)

        assert isinstance(result, ExportResult), (
            f"expected ExportResult, got {type(result).__name__}"
        )
        assert result.disk_error is not None, (
            f"errno={err_code} should have produced a disk_error category"
        )
        assert result.disk_error["category"] == expected_category
        assert result.disk_error["errno"] == err_code
        assert result.disk_error["first_failed_index"] == 2
        # The loop must have aborted at #2 — exactly one photo exported
        # (the success at #1), exactly one logged as failed (#2), and
        # photos #3-5 never attempted. retry_io may retry #2 internally
        # for the transient errnos (EACCES, ETIMEDOUT, …) so we can't
        # pin the raw copy-call count, but the result counters can't lie.
        assert result.exported == 1 and result.failed == 1, (
            f"loop should abort at photo 2; got exported={result.exported}, failed={result.failed}"
        )

    def test_non_fatal_oserror_keeps_going(self, tmp_path) -> None:
        """A generic OSError (corrupt source, ENOENT) is per-photo, not aborting."""
        selection = _selection(tmp_path, 4)
        outdir = str(tmp_path / "out")

        # Photo #2 fails with ENOENT (which is NOT in the fatal set);
        # the loop should keep going and successfully copy #3 and #4.
        real_copy = __import__("shutil").copy2
        calls = {"n": 0}

        def fake_copy(src, dest, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError(errno.ENOENT, "No such file")
            return real_copy(src, dest, *a, **kw)

        with patch("shutil.copy2", side_effect=fake_copy):
            result = export_selected(selection, [], outdir, mode="copy", strip_metadata=False)

        assert result.disk_error is None, "ENOENT is not in the fatal set — loop should not abort"
        assert result.exported == 3, "photos #1, #3, #4 should all succeed"
        assert result.failed == 1, "photo #2 should be counted as failed"

    def test_clean_run_no_disk_error(self, tmp_path) -> None:
        """ExportResult.disk_error is None on a successful export."""
        selection = _selection(tmp_path, 3)
        outdir = str(tmp_path / "out")
        result = export_selected(selection, [], outdir, mode="copy", strip_metadata=False)
        assert result.disk_error is None
        assert result.exported == 3
        assert result.failed == 0

    def test_back_compat_tuple_unpack(self, tmp_path) -> None:
        """Legacy callers tuple-unpacking ``exported, failed`` still work."""
        selection = _selection(tmp_path, 2)
        outdir = str(tmp_path / "out")
        exported, failed = export_selected(selection, [], outdir, mode="copy", strip_metadata=False)
        assert exported == 2 and failed == 0
