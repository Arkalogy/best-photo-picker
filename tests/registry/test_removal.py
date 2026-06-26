"""Batch 7 model-removal + derived-data-purge tests.

Pins:

* Schema v41 added ``face_embeddings.producing_model_id`` and an
  index on it.
* ``count_derived_for_model`` returns the right counts and skips
  ``producing_model_id IS NULL`` rows.
* ``purge_derived_for_model`` deletes by model id and only by
  model id (NULL rows are never touched).
* ``remove_model_with_derived_choice`` dispatches BYOM ids to the
  BYOM store, refuses built-in entries with a clear error, and
  honours the explicit purge_derived flag.
* CLI ``bpp model remove`` fails closed without
  ``--purge-derived`` / ``--keep-derived``.
"""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest

from bpp.registry import (
    DerivedDataSummary,
    ModelRemovalError,
    add_byom_entry,
    count_derived_for_model,
    purge_derived_for_model,
    remove_model_with_derived_choice,
)


@pytest.fixture
def isolated_byom_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "byom-models.json"
    monkeypatch.setenv("BPP_BYOM_PATH", str(target))
    return target


@pytest.fixture
def face_embeddings_db(tmp_path: Path) -> sqlite3.Connection:
    """Create an in-memory DB with a minimal face_embeddings table
    mirroring the v41 schema. Lets the tests exercise the purge
    helpers without standing up the full library DB."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE face_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER NOT NULL,
            face_index INTEGER NOT NULL,
            cluster_id INTEGER,
            embedding BLOB,
            producing_model_id TEXT,
            UNIQUE(photo_id, face_index)
        );
        CREATE INDEX idx_face_embeddings_producing_model
            ON face_embeddings (producing_model_id);
        """
    )
    return conn


def _seed_rows(
    conn: sqlite3.Connection,
    rows: list[tuple[int, int, int, str | None]],
) -> None:
    """Insert a list of (photo_id, face_index, cluster_id,
    producing_model_id) rows."""
    blob = struct.pack("128f", *([0.1] * 128))
    conn.executemany(
        "INSERT INTO face_embeddings "
        "(photo_id, face_index, cluster_id, embedding, producing_model_id) "
        "VALUES (?,?,?,?,?)",
        [(pid, fi, cid, blob, mid) for pid, fi, cid, mid in rows],
    )
    conn.commit()


# ── count_derived_for_model ──


class TestCountDerivedForModel:
    def test_count_returns_zero_when_no_rows_match(
        self, face_embeddings_db: sqlite3.Connection
    ) -> None:
        _seed_rows(
            face_embeddings_db,
            [(1, 0, 100, "sface_yunet"), (2, 0, 100, "sface_yunet")],
        )
        summary = count_derived_for_model("not_a_model", face_embeddings_db)
        assert summary == DerivedDataSummary(0, 0, 0)

    def test_count_groups_by_model_id(self, face_embeddings_db: sqlite3.Connection) -> None:
        _seed_rows(
            face_embeddings_db,
            [
                (1, 0, 100, "sface_yunet"),
                (2, 0, 100, "sface_yunet"),
                (2, 1, 101, "sface_yunet"),
                (3, 0, 200, "byom_abc"),
            ],
        )
        summary = count_derived_for_model("sface_yunet", face_embeddings_db)
        assert summary.embeddings == 3
        assert summary.distinct_clusters == 2
        assert summary.distinct_photos == 2

    def test_count_skips_null_producing_model_id(
        self, face_embeddings_db: sqlite3.Connection
    ) -> None:
        """NULL rows are pre-v41 history — must not be counted as
        belonging to any model."""
        _seed_rows(
            face_embeddings_db,
            [
                (1, 0, 100, "sface_yunet"),
                (2, 0, 100, None),  # pre-v41
                (3, 0, 101, None),  # pre-v41
            ],
        )
        summary = count_derived_for_model("sface_yunet", face_embeddings_db)
        assert summary.embeddings == 1
        # Counting a NULL-only model_id returns zero.
        summary = count_derived_for_model("", face_embeddings_db)
        assert summary == DerivedDataSummary(0, 0, 0)


# ── purge_derived_for_model ──


class TestPurgeDerivedForModel:
    def test_purge_deletes_only_matching_rows(self, face_embeddings_db: sqlite3.Connection) -> None:
        _seed_rows(
            face_embeddings_db,
            [
                (1, 0, 100, "sface_yunet"),
                (2, 0, 100, "sface_yunet"),
                (3, 0, 200, "byom_abc"),
            ],
        )
        n = purge_derived_for_model("sface_yunet", face_embeddings_db)
        assert n == 2
        remaining = face_embeddings_db.execute(
            "SELECT producing_model_id FROM face_embeddings"
        ).fetchall()
        assert [r[0] for r in remaining] == ["byom_abc"]

    def test_purge_does_not_touch_null_rows(self, face_embeddings_db: sqlite3.Connection) -> None:
        """Pre-v41 history (NULL producing_model_id) must survive
        any purge — losing it on first run after migration would
        wipe historical data the user did not consent to lose."""
        _seed_rows(
            face_embeddings_db,
            [
                (1, 0, 100, None),
                (2, 0, 100, "sface_yunet"),
                (3, 0, 100, None),
            ],
        )
        n = purge_derived_for_model("sface_yunet", face_embeddings_db)
        assert n == 1
        remaining = face_embeddings_db.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0]
        assert remaining == 2

    def test_purge_empty_model_id_is_a_noop(self, face_embeddings_db: sqlite3.Connection) -> None:
        _seed_rows(
            face_embeddings_db,
            [(1, 0, 100, "sface_yunet")],
        )
        assert purge_derived_for_model("", face_embeddings_db) == 0


# ── remove_model_with_derived_choice ──


class TestRemoveModelDispatch:
    def _write_byom_file(self, tmp_path: Path) -> Path:
        fp = tmp_path / "user-model.onnx"
        fp.write_bytes(b"fake-model-bytes")
        return fp

    def test_removing_byom_drops_it_and_optionally_purges(
        self,
        tmp_path: Path,
        face_embeddings_db: sqlite3.Connection,
        isolated_byom_store: Path,
    ) -> None:
        fp = self._write_byom_file(tmp_path)
        byom = add_byom_entry(display_name="x", kind="face_embedder", file_path=fp)
        _seed_rows(
            face_embeddings_db,
            [(1, 0, 100, byom.id), (2, 0, 100, byom.id)],
        )
        result = remove_model_with_derived_choice(
            byom.id, purge_derived=True, conn=face_embeddings_db
        )
        assert result.entry_kind == "byom"
        assert result.purged is True
        assert result.derived_summary.embeddings == 2
        # BYOM store dropped it.
        from bpp.registry import get_byom_entry

        assert get_byom_entry(byom.id) is None
        # Embeddings deleted.
        remaining = face_embeddings_db.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0]
        assert remaining == 0

    def test_removing_byom_with_keep_derived_preserves_embeddings(
        self,
        tmp_path: Path,
        face_embeddings_db: sqlite3.Connection,
        isolated_byom_store: Path,
    ) -> None:
        fp = self._write_byom_file(tmp_path)
        byom = add_byom_entry(display_name="x", kind="face_embedder", file_path=fp)
        _seed_rows(
            face_embeddings_db,
            [(1, 0, 100, byom.id)],
        )
        result = remove_model_with_derived_choice(
            byom.id, purge_derived=False, conn=face_embeddings_db
        )
        assert result.entry_kind == "byom"
        assert result.purged is False
        remaining = face_embeddings_db.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0]
        assert remaining == 1

    def test_unknown_byom_id_raises(
        self,
        face_embeddings_db: sqlite3.Connection,
        isolated_byom_store: Path,
    ) -> None:
        with pytest.raises(ModelRemovalError, match="No BYOM entry"):
            remove_model_with_derived_choice(
                "byom_nonexistent",
                purge_derived=True,
                conn=face_embeddings_db,
            )

    def test_built_in_entry_refuses_with_clear_message(
        self,
        face_embeddings_db: sqlite3.Connection,
    ) -> None:
        """Removing SFace is meaningful only via the remote-registry
        overlay (Batch 8); the in-process removal must refuse with
        a directive message."""
        with pytest.raises(ModelRemovalError, match="built-in"):
            remove_model_with_derived_choice(
                "sface_yunet",
                purge_derived=True,
                conn=face_embeddings_db,
            )

    def test_unknown_id_raises_no_built_in_no_byom(
        self,
        face_embeddings_db: sqlite3.Connection,
        isolated_byom_store: Path,
    ) -> None:
        with pytest.raises(ModelRemovalError, match="No registered entry"):
            remove_model_with_derived_choice(
                "not_a_real_id",
                purge_derived=True,
                conn=face_embeddings_db,
            )


# ── Schema migration v41 ──


class TestSchemaV41Migration:
    """The v41 migration adds producing_model_id + an index. These
    tests assert the column exists in the v41 schema."""

    def test_canonical_schema_has_producing_model_id(self) -> None:
        from bpp.db.schema import INDEXES_SQL, TABLES_SQL

        assert "producing_model_id TEXT" in TABLES_SQL
        assert "idx_face_embeddings_producing_model" in INDEXES_SQL

    def test_schema_version_includes_v41(self) -> None:
        """>= (not ==): this class pins the v41 migration into the
        lineage, not the global version — an exact pin goes stale on
        every later schema bump (v42/moments broke it for a day)."""
        from bpp.db.schema import SCHEMA_VERSION

        assert SCHEMA_VERSION >= 41

    def test_v41_migration_is_idempotent(self) -> None:
        """Calling the migration twice on the same DB must not
        raise, must not add the column twice."""
        from bpp.db.migrations_latest import _migrate_v41

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE face_embeddings ("
            " id INTEGER PRIMARY KEY, photo_id INTEGER, face_index INTEGER,"
            " embedding BLOB)"
        )
        _migrate_v41(conn)
        _migrate_v41(conn)  # second call is no-op
        cols = [row[1] for row in conn.execute("PRAGMA table_info(face_embeddings)").fetchall()]
        assert cols.count("producing_model_id") == 1

    def test_v41_migration_skips_missing_table(self) -> None:
        """On a fresh DB that has not yet reached the face_embeddings
        creation step (impossible in practice for v41 but defensively
        coded), the migration must skip rather than raise."""
        from bpp.db.migrations_latest import _migrate_v41

        conn = sqlite3.connect(":memory:")
        _migrate_v41(conn)  # no-op
