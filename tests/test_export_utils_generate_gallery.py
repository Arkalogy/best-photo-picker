"""Tests for export, gallery, timing, concurrency, paths, and logging — part 2 of 4:
TestGenerateGallery, TestTimer, TestGetWorkerCount."""

from __future__ import annotations

import logging
import os
import time
from unittest.mock import patch

import pytest

from bpp.output.gallery import (
    generate_gallery,
)
from bpp.utils.concurrency import get_worker_count, parallel_map
from bpp.utils.paths import safe_join
from bpp.utils.timing import Timer
from tests._export_utils_shared import (
    _always_fail,
    _double,
    _fail_on_three,
    _make_selected,
)


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
