"""Shared fixtures and helpers of the test_face_extraction_journal.py suite (split for the 500-LOC
cap)."""

from __future__ import annotations

import sqlite3

import pytest

from bpp.constants import (
    CLUSTER_UNASSIGNED,
)


def _build_schema(c: sqlite3.Connection) -> None:
    """Minimal schema: face_extraction_journal + face_embeddings +
    settings + the feedback tables cluster_faces reads."""
    c.execute(
        "CREATE TABLE face_extraction_journal ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " run_id TEXT NOT NULL UNIQUE,"
        " phases_complete INTEGER NOT NULL DEFAULT 0,"
        " snapshot_json TEXT,"
        " started_at INTEGER NOT NULL,"
        " completed_at INTEGER,"
        # v39 retry_count column.
        " retry_count INTEGER NOT NULL DEFAULT 0"
        ")"
    )
    c.execute(
        "CREATE TABLE face_embeddings ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " photo_id INTEGER NOT NULL,"
        " face_index INTEGER NOT NULL,"
        " bbox_x INTEGER, bbox_y INTEGER, bbox_w INTEGER, bbox_h INTEGER,"
        " embedding BLOB,"
        " quality REAL,"
        f" cluster_id INTEGER NOT NULL DEFAULT {CLUSTER_UNASSIGNED},"
        " extraction_max_long_side INTEGER,"  # v40 bbox-scale hardening
        " producing_model_id TEXT,"  # v41 derived-data purge
        " UNIQUE(photo_id, face_index)"
        ")"
    )
    c.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(
        "CREATE TABLE face_cluster_feedback ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " action TEXT, cluster_id_a INTEGER, cluster_id_b INTEGER,"
        " distance REAL, created_at INTEGER DEFAULT 0"
        ")"
    )
    c.execute(
        "CREATE TABLE face_hard_negatives ("
        " cluster_id_a INTEGER NOT NULL, cluster_id_b INTEGER NOT NULL,"
        " count INTEGER NOT NULL DEFAULT 1,"
        " created_at TEXT, updated_at TEXT,"
        " PRIMARY KEY (cluster_id_a, cluster_id_b)"
        ")"
    )
    # operation_journal is used by ClusterPhase to record clustering
    # as a recoverable operation; the ship-criterion test runs phase 6
    # so the table must exist in the in-memory fixture.
    c.execute(
        "CREATE TABLE IF NOT EXISTS operation_journal ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " kind TEXT NOT NULL,"
        " payload_json TEXT NOT NULL,"
        " started_at INTEGER NOT NULL,"
        " completed_at INTEGER"
        ")"
    )
    c.commit()


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _build_schema(c)
    yield c
    c.close()
