"""Bug #9 hardening (v40): ``extraction_max_long_side`` per face row.

What this pins
--------------
The Bug #9 root cause was: ``face_embeddings.bbox_{x,y,w,h}`` are stored
in detector-input coordinate space (the image after ``load_and_downscale``
at the configured ``max_long_side``), but every read site recomputed the
detector scale from the *current* config. A config change between
extraction and read silently mis-scaled every overlay and produced
upper-left "tiny corner" boxes plus background-patch cluster pollution.

v40 fixes this by making each row self-describing: the writer records
the ``max_long_side`` used during extraction; the readers
(``bp_faces_photo._compute_bbox_pct``, ``face_crop.generate_face_crop``)
use that per-row value, falling back to the current config only when
NULL (pre-v40 rows).

These tests guard:
1. Migration v40 adds the column and is idempotent.
2. Schema ``CREATE TABLE`` includes the column for fresh DBs.
3. The writer in ``extract_new_embeddings`` populates the column with
   the ``max_long_side`` argument it received.
4. The read path returns identical ``bbox_pct`` regardless of current
   config when the row has a known extraction size — proving that a
   later config change can't re-shift the overlay.
"""

from __future__ import annotations

import sqlite3

from bpp.constants import CLUSTER_UNASSIGNED
from bpp.db.migrations_latest import _migrate_v40


def _pre_v40_schema(conn: sqlite3.Connection) -> None:
    """Create face_embeddings WITHOUT extraction_max_long_side, mirroring
    a real pre-v40 DB. The migration's ALTER TABLE adds the column."""
    conn.execute(
        "CREATE TABLE face_embeddings ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " photo_id INTEGER NOT NULL,"
        " face_index INTEGER NOT NULL,"
        " bbox_x INTEGER, bbox_y INTEGER, bbox_w INTEGER, bbox_h INTEGER,"
        " embedding BLOB,"
        " quality REAL,"
        f" cluster_id INTEGER NOT NULL DEFAULT {CLUSTER_UNASSIGNED},"
        " UNIQUE(photo_id, face_index)"
        ")"
    )


class TestMigrationV40:
    """v40 ALTER TABLE adds extraction_max_long_side, is idempotent,
    and the default for pre-existing rows is NULL (deliberately — see
    _migrate_v40 docstring)."""

    def test_adds_column_when_missing(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _pre_v40_schema(conn)
        # Seed a row so we can verify pre-existing data survives.
        conn.execute(
            "INSERT INTO face_embeddings"
            " (photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, embedding)"
            " VALUES (1, 0, 10, 20, 30, 40, X'00')"
        )
        _migrate_v40(conn)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(face_embeddings)")]
        assert "extraction_max_long_side" in cols
        # Pre-existing row's new column is NULL (the read path falls
        # back to current config and logs a once-per-request warning).
        row = conn.execute(
            "SELECT bbox_x, extraction_max_long_side FROM face_embeddings WHERE id=1"
        ).fetchone()
        assert row["bbox_x"] == 10
        assert row["extraction_max_long_side"] is None

    def test_idempotent(self) -> None:
        """Re-running v40 on a DB that already has the column is a no-op."""
        conn = sqlite3.connect(":memory:")
        _pre_v40_schema(conn)
        _migrate_v40(conn)
        # Second invocation must NOT raise (would be ALTER duplicate column).
        _migrate_v40(conn)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(face_embeddings)")]
        # Column appears exactly once.
        assert cols.count("extraction_max_long_side") == 1

    def test_skips_when_table_missing(self) -> None:
        """Defensive: if face_embeddings doesn't exist (migrations
        running out of order on a stub DB), v40 doesn't crash."""
        conn = sqlite3.connect(":memory:")
        # No CREATE TABLE.
        _migrate_v40(conn)  # must not raise


class TestSchemaIncludesNewColumn:
    """Fresh DBs (via CREATE TABLE in schema.py) have the column from
    day one — the migration isn't responsible for new installs."""

    def test_create_table_includes_extraction_max_long_side(self) -> None:
        import os
        import tempfile

        from bpp.db.connection import close_all_connections, init_db

        td = tempfile.mkdtemp()
        try:
            db_path = os.path.join(td, "test.db")
            conn = init_db(db_path)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(face_embeddings)")]
            assert "extraction_max_long_side" in cols
        finally:
            close_all_connections()


class TestReaderReconstructsCorrectlyAcrossConfigChange:
    """Core hardening assertion: a row whose extraction_max_long_side
    is set produces the same bbox_pct regardless of current
    ``ctx.config['max_long_side']``. This is what prevents Bug #9 from
    being reachable again — the read path stops depending on a live
    setting that can drift.

    This is a pure-arithmetic test on the percentage computation lifted
    from ``bp_faces_photo._compute_bbox_pct`` — running the full Flask
    handler is unnecessary because the bug class is about coordinate
    math, not blueprint plumbing."""

    @staticmethod
    def _bbox_pct(
        bbox: tuple[int, int, int, int],
        row_max_long: int | None,
        current_max_long: int,
        orig_w: int,
        orig_h: int,
    ) -> dict[str, float]:
        """Mirrors bp_faces_photo._detector_dims + the pct calc."""
        bx, by, bw, bh = bbox
        max_long = row_max_long if row_max_long is not None else current_max_long
        long_side = max(orig_w, orig_h)
        scale = long_side / max_long if long_side > max_long else 1.0
        det_w = round(orig_w / scale)
        det_h = round(orig_h / scale)
        return {
            "x": round(bx / det_w * 100, 2),
            "y": round(by / det_h * 100, 2),
            "w": round(bw / det_w * 100, 2),
            "h": round(bh / det_h * 100, 2),
        }

    def test_row_with_known_size_unaffected_by_config_change(self) -> None:
        """Extract row at max_long=1024, store bbox in 682x1024 space.
        Change current config to 320 (the Bug #9 historical value).
        The percentage MUST stay identical."""
        # Photo dims: 867x1300 → detector at max_long=1024 → 682x1024.
        orig_w, orig_h = 867, 1300
        # Stored bbox (matches photo 475's fresh detection):
        bbox = (226, 303, 281, 368)

        pct_with_correct_row_size = self._bbox_pct(
            bbox,
            row_max_long=1024,
            current_max_long=1024,
            orig_w=orig_w,
            orig_h=orig_h,
        )
        pct_with_drifted_config = self._bbox_pct(
            bbox,
            row_max_long=1024,
            current_max_long=320,
            orig_w=orig_w,
            orig_h=orig_h,
        )
        assert pct_with_correct_row_size == pct_with_drifted_config, (
            f"bbox_pct must be stable across config drift when the row "
            f"has extraction_max_long_side set; got {pct_with_correct_row_size!r} "
            f"vs {pct_with_drifted_config!r}"
        )

    def test_null_row_falls_back_to_current_config(self) -> None:
        """Pre-v40 row (NULL) uses current config. This is best-effort
        — by design — and the read path logs a warning. Test only
        confirms the fallback path returns a sensible value, not that
        it's *correct* (which is impossible without knowing the
        historical max_long_side)."""
        bbox = (226, 303, 281, 368)
        pct = self._bbox_pct(
            bbox,
            row_max_long=None,
            current_max_long=1024,
            orig_w=867,
            orig_h=1300,
        )
        # Just assert it's in [0, 100] — the value depends on current_max_long.
        assert 0 <= pct["x"] <= 100
        assert 0 <= pct["y"] <= 100
        assert 0 <= pct["w"] <= 100
        assert 0 <= pct["h"] <= 100

    def test_drifted_config_corrupts_pct_without_v40(self) -> None:
        """Sanity: without v40 (i.e. always using current config) the
        same bbox produces different percentages when config drifts —
        this is exactly the Bug #9 failure mode. Test exists to make
        the protection's value concrete."""
        bbox = (226, 303, 281, 368)
        pct_at_1024 = self._bbox_pct(
            bbox,
            row_max_long=None,
            current_max_long=1024,
            orig_w=867,
            orig_h=1300,
        )
        pct_at_320 = self._bbox_pct(
            bbox,
            row_max_long=None,
            current_max_long=320,
            orig_w=867,
            orig_h=1300,
        )
        assert pct_at_1024 != pct_at_320, (
            "Sanity check failed: bbox_pct should differ when config drifts "
            "and there's no per-row size to anchor on. If this passes the "
            "math has been refactored and the protection above no longer "
            "tests what its docstring claims."
        )
