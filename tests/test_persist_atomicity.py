"""Atomicity gate for the recompute + analyze persist paths.

Release-audit H3 flagged these write paths as potentially non-atomic:
the abstract concern was 'multiple row updates outside an explicit
transaction'. Reading the code showed the bug doesn't exist —
``set_album_selection`` is a single ``UPDATE ... CASE`` and
``bulk_upsert_photos`` is ``executemany`` + single commit, both of
which SQLite guarantees as atomic against a worker crash.

These tests pin those guarantees so a future refactor that splits
either into per-row execute+commit trips the gate instead of silently
introducing the bug the audit was worried about.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock

import pytest

from bpp.db.albums import ensure_all_photos_album, set_album_selection
from bpp.db.connection import get_db, init_db
from bpp.db.photos import bulk_upsert_photos


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "atomicity.db")
    init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    return get_db(db_path)


def _photo(filepath: str) -> dict:
    """Minimal photo dict accepted by bulk_upsert_photos."""
    return {
        "filepath": filepath,
        "sha256": "deadbeef" + filepath.split("/")[-1],
        "date": "2026-05-01T12:00:00",
        "date_day": "2026-05-01",
        "date_month": "2026-05",
        "aggregate_score": 0.5,
    }


class TestSetAlbumSelectionAtomicity:
    """``set_album_selection`` must remain a single SQL statement so two
    concurrent recompute runs can't interleave the clear vs. set phases.

    The original two-statement form (``UPDATE … SET selected=0`` then
    ``UPDATE … SET selected=1 WHERE id IN (...)``) let tab B's clear+set
    sneak between tab A's clear and set; tab B's selection silently
    disappeared. The fix collapsed both into a single ``UPDATE ... CASE``.
    These tests pin that shape.
    """

    def test_with_selection_runs_exactly_one_execute(self) -> None:
        """A non-empty selection writes via exactly one execute call."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        set_album_selection(mock_conn, album_id=1, selected_photo_ids={10, 20, 30})
        # Exactly one execute (the CASE UPDATE) + one commit. If anyone
        # ever splits this back into clear-then-set, this assertion
        # catches it before merge.
        assert mock_conn.execute.call_count == 1, (
            f"set_album_selection must use a single UPDATE statement; "
            f"got {mock_conn.execute.call_count} execute calls"
        )
        assert mock_conn.commit.call_count == 1
        # The SQL must be the CASE shape — defensive in case someone
        # changes the body to a single non-atomic statement.
        sql = mock_conn.execute.call_args[0][0]
        assert "CASE" in sql.upper(), f"expected a CASE-form UPDATE; got: {sql!r}"

    def test_empty_selection_also_one_execute(self) -> None:
        """Clearing all selections is also one statement."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        set_album_selection(mock_conn, album_id=1, selected_photo_ids=set())
        assert mock_conn.execute.call_count == 1
        assert mock_conn.commit.call_count == 1

    def test_end_to_end_atomicity_against_real_db(self, conn, tmp_path) -> None:
        """Round-trip through a real SQLite to confirm the statement
        does what the unit-level shape claims."""
        # Seed two photos in the All album
        bulk_upsert_photos(conn, [_photo(str(tmp_path / "a.jpg")), _photo(str(tmp_path / "b.jpg"))])
        album_id = ensure_all_photos_album(conn)
        # Need the photos linked to the album for selected to mean anything.
        photo_ids = [r["id"] for r in conn.execute("SELECT id FROM photos ORDER BY id").fetchall()]
        for pid in photo_ids:
            conn.execute(
                "INSERT INTO album_photos (album_id, photo_id, selected) VALUES (?, ?, 0)",
                (album_id, pid),
            )
        conn.commit()

        # Mark only the first as selected; expect (1, 0).
        set_album_selection(conn, album_id, {photo_ids[0]})
        rows = conn.execute(
            "SELECT photo_id, selected FROM album_photos WHERE album_id=? ORDER BY photo_id",
            (album_id,),
        ).fetchall()
        assert [r["selected"] for r in rows] == [1, 0], (
            f"expected first selected, second cleared; got {[dict(r) for r in rows]}"
        )

        # Flip — only second selected. The CASE form must clear the
        # first in the same statement that sets the second.
        set_album_selection(conn, album_id, {photo_ids[1]})
        rows = conn.execute(
            "SELECT photo_id, selected FROM album_photos WHERE album_id=? ORDER BY photo_id",
            (album_id,),
        ).fetchall()
        assert [r["selected"] for r in rows] == [0, 1]


class TestBulkUpsertPhotosAtomicity:
    """``bulk_upsert_photos`` writes the whole batch in a single
    ``executemany`` + one ``commit``. If a worker crashes between
    ``executemany`` and ``commit``, SQLite leaves zero rows persisted
    rather than a partial subset — and a future refactor to per-row
    execute+commit would silently break that. Pin the shape.
    """

    def test_uses_executemany_and_single_commit(self, tmp_path) -> None:
        """The function calls executemany once, commit once, and per-row
        execute zero times."""
        mock_conn = MagicMock(spec=sqlite3.Connection)
        photos = [_photo(str(tmp_path / f"p{i}.jpg")) for i in range(5)]
        # Patch os.stat so the function doesn't error on the fake paths.
        from unittest.mock import patch

        with patch("os.stat", side_effect=OSError("test fixture has no file")):
            bulk_upsert_photos(mock_conn, photos)
        assert mock_conn.executemany.call_count == 1, (
            "must use a single executemany so the batch is one transaction"
        )
        # No per-row execute — that would defeat the atomicity claim.
        assert mock_conn.execute.call_count == 0, (
            f"unexpected per-row execute calls: {mock_conn.execute.call_count}"
        )
        assert mock_conn.commit.call_count == 1, "exactly one commit at the boundary, not per row"

    def test_no_partial_persistence_when_commit_fails(self, conn, tmp_path) -> None:
        """If commit raises after executemany, no rows must be visible.

        This is the actual crash semantic from the original audit
        concern: a worker that dies between ``executemany`` and
        ``commit`` should leave the table untouched. SQLite gives us
        this for free because ``executemany`` is an implicit
        transaction that's only made durable by the commit.
        """
        # Stage 1: confirm the photos table starts empty.
        before = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        assert before == 0

        # Stage 2: simulate a mid-write crash by wrapping the real
        # connection so commit() raises. executemany still runs, but
        # the transaction never commits.
        class _CrashingConn:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                if name == "commit":
                    raise OSError("simulated mid-write worker crash")
                return getattr(self._real, name)

        wrapped = _CrashingConn(conn)
        photos = [_photo(str(tmp_path / f"q{i}.jpg")) for i in range(3)]
        with pytest.raises(OSError, match="simulated mid-write"):
            bulk_upsert_photos(wrapped, photos)

        # Stage 3: a fresh read on the real connection should see zero
        # rows. SQLite's implicit transaction around executemany rolls
        # back when the connection is closed without a commit.
        conn.rollback()  # discard the uncommitted in-flight tx
        after = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        assert after == 0, (
            f"expected zero rows after a crashed bulk_upsert; got {after} — "
            "executemany atomicity invariant is broken"
        )

    def test_clean_run_persists_all_rows(self, conn, tmp_path) -> None:
        """Sanity: a non-crashing run does persist every row."""
        photos = [_photo(str(tmp_path / f"ok{i}.jpg")) for i in range(4)]
        bulk_upsert_photos(conn, photos)
        n = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        assert n == 4

    def test_transient_io_error_is_retried_and_succeeds(self, conn, tmp_path) -> None:
        """M9: a transient OSError (NAS jitter) on the first executemany
        attempt must be retried — the second clean attempt should land
        the rows. Local-disk users see no overhead because is_transient
        returns False for everything they'd hit; this test simulates
        the NAS path.

        sqlite3.Connection attributes are C-level and not directly
        monkey-patchable, so we wrap the real connection in a proxy
        whose ``executemany`` injects one transient OSError before
        passing through.
        """
        import errno

        photos = [_photo(str(tmp_path / f"nas{i}.jpg")) for i in range(3)]
        calls = {"n": 0}

        class _FlakyConn:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                return getattr(self._real, name)

            def executemany(self, *args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError(errno.EIO, "synthetic NAS jitter")
                return self._real.executemany(*args, **kwargs)

            def commit(self):
                return self._real.commit()

        flaky = _FlakyConn(conn)
        # The retry_io wrapper backs off (0.5s + 1s + 2s on the default
        # 3-retry budget) so this test takes ~0.5s on the path that
        # hits one transient.
        bulk_upsert_photos(flaky, photos)

        # First attempt raised; second attempt persisted all 3 rows.
        n = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        assert n == 3, f"transient I/O error should have been retried; expected 3 rows, got {n}"
        assert calls["n"] >= 2, (
            f"retry_io should have made a second executemany attempt; saw {calls['n']}"
        )
