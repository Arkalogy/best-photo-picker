"""An import survives being killed: rows are written as the copy goes,
copied files that lost their row are adopted on the next import instead of
being copied again as ``name_1``, cancel stops the copy, and leftover
``.tmp`` files are cleaned up.

Before 0.2.2 the rows were written only after the whole copy loop (the
desktop quit is a SIGKILL after 3 s), the loop ignored cancel, and a
re-import saw a taken slot and copied the file again.
"""

from __future__ import annotations

import errno
import os
import sqlite3

import pytest
from PIL import Image

from bpp.db import library
from bpp.db.connection import init_db
from bpp.db.library import import_folder


@pytest.fixture()
def lib(tmp_path):
    """A library, a source folder with six distinct images, and a DB."""
    root = tmp_path / "library"
    for d in ("photos", "data", "cache", "logs"):
        (root / d).mkdir(parents=True)
    src = tmp_path / "source"
    src.mkdir()
    for i in range(6):
        Image.new("RGB", (10, 10), (i * 30, 10, 10)).save(str(src / f"img_{i}.jpg"), "JPEG")
    db_path = str(root / "data" / "photopicker.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn, str(src), str(root)


def _rows(conn) -> list[str]:
    return sorted(r[0] for r in conn.execute("SELECT filepath FROM photos"))


def _files(batch_dir: str) -> list[str]:
    return sorted(f for f in os.listdir(batch_dir) if not f.startswith("."))


class TestRowsFollowTheCopy:
    def test_rows_are_written_as_the_copy_goes(self, lib, monkeypatch):
        """Killed after the 5th file: the first four rows exist (three from
        the periodic flush, one flushed before the abort)."""
        conn, src, root = lib
        monkeypatch.setattr(library, "IMPORT_ROW_FLUSH_EVERY", 3)
        real_copy = library._copy_atomically
        calls = {"n": 0}

        def copy_then_die(src_path, tmp_path, dest_path):
            calls["n"] += 1
            if calls["n"] == 5:
                raise OSError(errno.ENOSPC, "No space left on device")
            real_copy(src_path, tmp_path, dest_path)

        monkeypatch.setattr(library, "_copy_atomically", copy_then_die)

        with pytest.raises(OSError):
            import_folder(conn, src, root)

        batch_dir = os.path.join(root, "photos", "source")
        assert len(_files(batch_dir)) == 4
        assert len(_rows(conn)) == 4, "every copied file has its row"


class TestRowsFollowTheCopy2:
    def test_any_abort_keeps_the_rows_written_so_far(self, lib, monkeypatch):
        """Ctrl-C (not an OSError) after the 5th copy: the first four rows exist."""
        conn, src, root = lib
        monkeypatch.setattr(library, "IMPORT_ROW_FLUSH_EVERY", 3)
        real_copy = library._copy_atomically
        calls = {"n": 0}

        def copy_then_interrupt(src_path, tmp_path, dest_path):
            calls["n"] += 1
            if calls["n"] == 5:
                raise KeyboardInterrupt
            real_copy(src_path, tmp_path, dest_path)

        monkeypatch.setattr(library, "_copy_atomically", copy_then_interrupt)
        with pytest.raises(KeyboardInterrupt):
            import_folder(conn, src, root)
        assert len(_rows(conn)) == 4


class TestReimportAdopts:
    def test_copied_files_without_a_row_are_adopted_not_copied_again(self, lib):
        conn, src, root = lib
        first = import_folder(conn, src, root)
        assert first.imported == 6
        batch_dir = os.path.join(root, "photos", "source")
        # The kill happened after these were copied but before their rows.
        lost = _rows(conn)[:4]
        conn.executemany("DELETE FROM photos WHERE filepath=?", [(p,) for p in lost])
        conn.commit()
        assert len(_rows(conn)) == 2

        second = import_folder(conn, src, root)

        assert second.adopted == 4
        assert second.imported == 0
        assert second.skipped == 2
        assert _files(batch_dir) == [f"img_{i}.jpg" for i in range(6)], "no name_1 copies"
        assert len(_rows(conn)) == 6
        assert set(_rows(conn)) == {os.path.join(batch_dir, f) for f in _files(batch_dir)}

    def test_a_different_file_at_the_slot_still_gets_a_numbered_name(self, lib):
        conn, src, root = lib
        batch_dir = os.path.join(root, "photos", "source")
        os.makedirs(batch_dir)
        Image.new("RGB", (10, 10), "white").save(os.path.join(batch_dir, "img_0.jpg"), "JPEG")

        result = import_folder(conn, src, root)

        assert result.imported == 6 and result.adopted == 0
        assert "img_0_1.jpg" in _files(batch_dir)

    def test_a_recorded_photo_at_the_slot_is_never_adopted(self, lib):
        """A row exists for the path (someone's scored photo): the source
        gets a numbered name and that row is left alone."""
        conn, src, root = lib
        batch_dir = os.path.join(root, "photos", "source")
        os.makedirs(batch_dir)
        dest = os.path.join(batch_dir, "img_0.jpg")
        import shutil

        shutil.copy2(os.path.join(src, "img_0.jpg"), dest)  # same content as the source
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, sha256, aggregate_score, "
            "file_size, file_mtime) VALUES (?, 'img_0.jpg', 'someothersha', 0.9, 1, 1.0)",
            (dest,),
        )
        conn.commit()

        result = import_folder(conn, src, root)

        assert result.adopted == 0 and result.imported == 6
        assert "img_0_1.jpg" in _files(batch_dir)
        row = conn.execute(
            "SELECT aggregate_score FROM photos WHERE filepath=?", (dest,)
        ).fetchone()
        assert row[0] == 0.9, "the existing row keeps its score"

    def test_an_unreadable_slot_steps_to_the_next_name(self, lib):
        conn, src, root = lib
        batch_dir = os.path.join(root, "photos", "source")
        os.makedirs(os.path.join(batch_dir, "img_0.jpg"))  # a folder in the way

        result = import_folder(conn, src, root)

        assert result.imported == 6 and result.errors == 0
        assert "img_0_1.jpg" in _files(batch_dir)


class TestCancelAndCleanup:
    def test_cancel_stops_the_copy_and_keeps_the_rows(self, lib):
        conn, src, root = lib
        done: list[str] = []

        def on_progress(_cur, _total, filename, status):
            done.append(status)

        result = import_folder(
            conn, src, root, on_progress=on_progress, cancel_check=lambda: len(done) >= 2
        )

        assert result.cancelled is True
        assert result.imported == 2
        assert len(_rows(conn)) == 2
        assert len(_files(os.path.join(root, "photos", "source"))) == 2

    def test_stale_tmp_files_are_removed_at_import_start(self, lib):
        conn, src, root = lib
        batch_dir = os.path.join(root, "photos", "source")
        os.makedirs(batch_dir)
        stale = os.path.join(batch_dir, "half.jpg.tmp")
        with open(stale, "wb") as f:
            f.write(b"partial")

        import_folder(conn, src, root)

        assert not os.path.exists(stale)
        assert "half.jpg.tmp" not in _files(batch_dir)

    def test_the_library_open_sweep_covers_every_batch(self, lib):
        from bpp.db.library_copy import remove_stale_import_tmp

        _conn, _src, root = lib
        photos_dir = os.path.join(root, "photos")
        for batch in ("trip", "trip-2"):
            os.makedirs(os.path.join(photos_dir, batch))
            with open(os.path.join(photos_dir, batch, "x.jpg.tmp"), "wb") as f:
                f.write(b"half")
        with open(os.path.join(photos_dir, "trip", "keep.jpg"), "wb") as f:
            f.write(b"ok")

        assert remove_stale_import_tmp(photos_dir, older_than_s=0) == 2
        assert _files(os.path.join(photos_dir, "trip")) == ["keep.jpg"]
        assert _files(os.path.join(photos_dir, "trip-2")) == []


class TestWorkerHonoursCancel:
    def test_the_worker_hands_its_cancel_to_the_copy_and_skips_analysis(self, lib, monkeypatch):
        from bpp.web import import_worker as iw
        from bpp.web.import_worker import ImportWorker

        conn, src, root = lib
        seen: dict[str, object] = {}

        def fake_import_folder(**kwargs):
            seen.update(kwargs)
            return library.ImportResult(batch_name="source", imported=2, cancelled=True)

        monkeypatch.setattr(iw, "import_folder", fake_import_folder)
        monkeypatch.setattr(
            iw,
            "process_one_settings",
            lambda *_a, **_k: pytest.fail("analysis must not start after a cancelled copy"),
        )
        from bpp.web import analyze_finalize

        finalized: list[object] = []
        monkeypatch.setattr(
            analyze_finalize, "finalize_import_in_worker", lambda ctx: finalized.append(ctx)
        )
        worker = ImportWorker()
        worker._cancelled.set()
        worker._do_import(conn, src, root, root, {}, [".jpg"], None)

        assert callable(seen.get("cancel_check"))
        assert seen["cancel_check"]() is True, "the worker's own cancel flag"
        assert len(finalized) == 1, "the kept rows must show: finalize runs on cancel too"
