"""Tests for face pipeline improvements: identity labels, dismissed preservation,
ON CONFLICT behavior, reconstruction, decoupling, and parameter threading."""

from __future__ import annotations

import json
import tempfile

import numpy as np
import pytest

from bpp.constants import CLUSTER_DISMISSED, CLUSTER_UNASSIGNED
from bpp.db.connection import init_db
from bpp.db.schema import SCHEMA_VERSION


@pytest.fixture
def db():
    """Fresh DB with schema v26."""
    tmp = tempfile.mktemp(suffix=".db")
    conn = init_db(tmp)
    # Insert test photos
    for i in range(5):
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, face_count, file_size, file_mtime) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"/test/photo_{i}.jpg", f"photo_{i}.jpg", 2, 1000, "2026-01-01"),
        )
    conn.commit()
    yield conn
    conn.close()
    import os

    os.unlink(tmp)


def _insert_face(conn, photo_id, face_index, cluster_id, identity=None, quality=0.8):
    emb = np.random.randn(128).astype(np.float32)
    conn.execute(
        "INSERT INTO face_embeddings "
        "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
        " embedding, cluster_id, quality, identity) "
        "VALUES (?, ?, 10, 10, 50, 50, ?, ?, ?, ?)",
        (photo_id, face_index, emb.tobytes(), cluster_id, quality, identity),
    )
    return emb


class TestSchemaV26:
    """Migration v26 adds identity and user_confirmed columns."""

    def test_identity_column_exists(self, db):
        cols = {r[1] for r in db.execute("PRAGMA table_info(face_embeddings)").fetchall()}
        assert "identity" in cols

    def test_user_confirmed_column_exists(self, db):
        cols = {r[1] for r in db.execute("PRAGMA table_info(face_embeddings)").fetchall()}
        assert "user_confirmed" in cols

    def test_identity_default_null(self, db):
        _insert_face(db, 1, 0, 0)
        db.commit()
        row = db.execute(
            "SELECT identity FROM face_embeddings WHERE photo_id=1 AND face_index=0"
        ).fetchone()
        assert row[0] is None

    def test_user_confirmed_default_zero(self, db):
        _insert_face(db, 1, 0, 0)
        db.commit()
        row = db.execute(
            "SELECT user_confirmed FROM face_embeddings WHERE photo_id=1 AND face_index=0"
        ).fetchone()
        assert row[0] == 0

    def test_schema_version(self, db):
        version = db.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION


class TestOnConflictPreservation:
    """INSERT ... ON CONFLICT DO UPDATE preserves identity and cluster_id."""

    def test_preserves_identity(self, db):
        _insert_face(db, 1, 0, 5, identity="Leo")
        db.commit()

        # Re-extract same slot with ON CONFLICT
        new_emb = np.random.randn(128).astype(np.float32)
        db.execute(
            "INSERT INTO face_embeddings "
            "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, embedding, quality) "
            "VALUES (1, 0, 20, 20, 60, 60, ?, 0.9) "
            "ON CONFLICT(photo_id, face_index) DO UPDATE SET "
            "bbox_x=excluded.bbox_x, bbox_y=excluded.bbox_y, "
            "bbox_w=excluded.bbox_w, bbox_h=excluded.bbox_h, "
            "embedding=excluded.embedding, quality=excluded.quality",
            (new_emb.tobytes(),),
        )
        db.commit()

        row = db.execute(
            "SELECT identity, cluster_id, quality FROM face_embeddings "
            "WHERE photo_id=1 AND face_index=0"
        ).fetchone()
        assert row[0] == "Leo", f"Identity lost: {row[0]}"
        assert row[1] == 5, f"Cluster_id lost: {row[1]}"
        assert row[2] == 0.9, f"Quality not updated: {row[2]}"

    def test_preserves_dismissed(self, db):
        _insert_face(db, 1, 0, CLUSTER_DISMISSED)
        db.commit()

        new_emb = np.random.randn(128).astype(np.float32)
        db.execute(
            "INSERT INTO face_embeddings "
            "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, embedding, quality) "
            "VALUES (1, 0, 20, 20, 60, 60, ?, 0.9) "
            "ON CONFLICT(photo_id, face_index) DO UPDATE SET "
            "bbox_x=excluded.bbox_x, bbox_y=excluded.bbox_y, "
            "bbox_w=excluded.bbox_w, bbox_h=excluded.bbox_h, "
            "embedding=excluded.embedding, quality=excluded.quality",
            (new_emb.tobytes(),),
        )
        db.commit()

        row = db.execute(
            "SELECT cluster_id FROM face_embeddings WHERE photo_id=1 AND face_index=0"
        ).fetchone()
        assert row[0] == CLUSTER_DISMISSED

    def test_new_insert_gets_defaults(self, db):
        new_emb = np.random.randn(128).astype(np.float32)
        db.execute(
            "INSERT INTO face_embeddings "
            "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, embedding, quality) "
            "VALUES (1, 0, 20, 20, 60, 60, ?, 0.9) "
            "ON CONFLICT(photo_id, face_index) DO UPDATE SET "
            "bbox_x=excluded.bbox_x, bbox_y=excluded.bbox_y, "
            "bbox_w=excluded.bbox_w, bbox_h=excluded.bbox_h, "
            "embedding=excluded.embedding, quality=excluded.quality",
            (new_emb.tobytes(),),
        )
        db.commit()

        row = db.execute(
            "SELECT identity, cluster_id FROM face_embeddings WHERE photo_id=1 AND face_index=0"
        ).fetchone()
        assert row[0] is None
        assert row[1] == CLUSTER_UNASSIGNED


class TestIdentityReconstruction:
    """_reconstruct_identities rebuilds named albums from identity labels."""

    def test_reconstructs_after_cluster_change(self, db):
        from bpp.web.face_worker import _reconstruct_identities

        # Set up: cluster 0 = Leo, cluster 1 = Ana
        for pid in range(1, 4):
            _insert_face(db, pid, 0, 0, identity="Leo")
            _insert_face(db, pid, 1, 1, identity="Ana")
        db.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("Leo", json.dumps({"cluster_id": 0})),
        )
        db.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("Ana", json.dumps({"cluster_id": 1})),
        )
        db.commit()

        # Simulate re-clustering: IDs shift
        db.execute("UPDATE face_embeddings SET cluster_id = 10 WHERE cluster_id = 0")
        db.execute("UPDATE face_embeddings SET cluster_id = 20 WHERE cluster_id = 1")
        db.commit()

        _reconstruct_identities(db)

        leo_rule = json.loads(
            db.execute("SELECT rule_json FROM albums WHERE name='Leo'").fetchone()[0]
        )
        ana_rule = json.loads(
            db.execute("SELECT rule_json FROM albums WHERE name='Ana'").fetchone()[0]
        )
        assert leo_rule["cluster_id"] == 10
        assert ana_rule["cluster_id"] == 20

    def test_noop_without_labels(self, db):
        from bpp.web.face_worker import _reconstruct_identities

        for pid in range(1, 4):
            _insert_face(db, pid, 0, 0)  # no identity
        db.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("Leo", json.dumps({"cluster_id": 999})),
        )
        db.commit()

        _reconstruct_identities(db)

        rule = json.loads(db.execute("SELECT rule_json FROM albums WHERE name='Leo'").fetchone()[0])
        assert rule["cluster_id"] == 999, "Should not change without labels"

    def test_majority_identity_wins(self, db):
        from bpp.web.face_worker import _reconstruct_identities

        # 3 faces labeled Leo, 1 labeled Wrong — all in cluster 50
        for pid in range(1, 4):
            _insert_face(db, pid, 0, 50, identity="Leo")
        _insert_face(db, 4, 0, 50, identity="Wrong")
        db.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("Leo", json.dumps({"cluster_id": 0})),
        )
        db.commit()

        _reconstruct_identities(db)

        rule = json.loads(db.execute("SELECT rule_json FROM albums WHERE name='Leo'").fetchone()[0])
        assert rule["cluster_id"] == 50


class TestDismissedPreservation:
    """Dismissed face slots are restored after re-extraction."""

    def test_snapshot_and_restore_flow(self, db):
        """Simulates the dismissed snapshot/restore logic from face_worker."""
        # Insert a dismissed face
        _insert_face(db, 1, 2, CLUSTER_DISMISSED)
        db.commit()

        # Snapshot dismissed slots (mirrors face_worker logic)
        dismissed_slots = set()
        for r in db.execute(
            "SELECT photo_id, face_index FROM face_embeddings WHERE cluster_id = ?",
            (CLUSTER_DISMISSED,),
        ).fetchall():
            dismissed_slots.add((r[0], r[1]))

        assert (1, 2) in dismissed_slots

        # ON CONFLICT overwrites the dismissed face (simulates re-extraction)
        new_emb = np.random.randn(128).astype(np.float32)
        db.execute(
            "INSERT INTO face_embeddings "
            "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, embedding, quality) "
            "VALUES (1, 2, 20, 20, 60, 60, ?, 0.9) "
            "ON CONFLICT(photo_id, face_index) DO UPDATE SET "
            "bbox_x=excluded.bbox_x, bbox_y=excluded.bbox_y, "
            "bbox_w=excluded.bbox_w, bbox_h=excluded.bbox_h, "
            "embedding=excluded.embedding, quality=excluded.quality",
            (new_emb.tobytes(),),
        )
        db.commit()

        # cluster_id preserved by ON CONFLICT (no reset)
        row = db.execute(
            "SELECT cluster_id FROM face_embeddings WHERE photo_id=1 AND face_index=2"
        ).fetchone()
        assert row[0] == CLUSTER_DISMISSED

        # Even if it somehow got reset, the restore logic would fix it:
        db.execute(
            "UPDATE face_embeddings SET cluster_id = ? WHERE photo_id=1 AND face_index=2",
            (CLUSTER_UNASSIGNED,),
        )
        db.commit()

        # Restore dismissed slots (mirrors face_worker logic)
        for pid, fi in dismissed_slots:
            db.execute(
                "UPDATE face_embeddings SET cluster_id = ? WHERE photo_id = ? AND face_index = ?",
                (CLUSTER_DISMISSED, pid, fi),
            )
        db.commit()

        row = db.execute(
            "SELECT cluster_id FROM face_embeddings WHERE photo_id=1 AND face_index=2"
        ).fetchone()
        assert row[0] == CLUSTER_DISMISSED


class TestFaceCountDecoupling:
    """Face worker processes photos with existing embeddings even if face_count=0."""

    def test_includes_photos_with_embeddings(self, db):
        """Photos with face_count=0 but existing embeddings should be in with_faces."""
        # Photo 1: face_count=0 but has an embedding in DB
        db.execute("UPDATE photos SET face_count=0 WHERE id=1")
        _insert_face(db, 1, 0, 5)
        db.commit()

        photos_with_embeddings = set()
        for r in db.execute("SELECT DISTINCT photo_id FROM face_embeddings").fetchall():
            photos_with_embeddings.add(r[0])

        # Simulate the worker filter
        analysis = [
            {"filepath": f"/test/photo_{i}.jpg", "face_count": 0 if i == 0 else 2} for i in range(5)
        ]
        photo_map = {f"/test/photo_{i}.jpg": i + 1 for i in range(5)}

        with_faces = [
            a
            for a in analysis
            if a.get("face_count", 0) > 0 or photo_map.get(a["filepath"]) in photos_with_embeddings
        ]

        # Photo 0 has face_count=0 but embedding exists — should be included
        paths = [a["filepath"] for a in with_faces]
        assert "/test/photo_0.jpg" in paths


class TestParameterThreading:
    """Settings flow through to detection and embedding functions."""

    def test_min_face_area_frac_parameter(self):
        from bpp.scoring.face import _filter_small_faces

        faces = [(10, 10, 30, 30), (10, 10, 50, 50)]  # areas: 900, 2500
        image_area = 1024 * 768  # 786432

        # Default 0.002: min_area = 1573 → only 50x50 passes
        result = _filter_small_faces(faces, image_area, min_area_frac=0.002)
        assert len(result) == 1
        assert result[0][2] == 50

        # Looser 0.001: min_area = 786 → both pass
        result = _filter_small_faces(faces, image_area, min_area_frac=0.001)
        assert len(result) == 2

        # Tighter 0.005: min_area = 3932 → neither passes
        result = _filter_small_faces(faces, image_area, min_area_frac=0.005)
        assert len(result) == 0

    def test_min_face_area_conf_variant(self):
        from bpp.scoring.face import _filter_small_faces_conf

        faces = [(10, 10, 30, 30, 0.9), (10, 10, 50, 50, 0.8)]
        image_area = 1024 * 768

        result = _filter_small_faces_conf(faces, image_area, min_area_frac=0.002)
        assert len(result) == 1
        assert result[0][4] == 0.8  # confidence preserved


class TestMigrationBackfill:
    """v26 migration backfills identity from named smart_person albums."""

    def test_backfill_named_albums(self, db):
        # Set up: cluster 5 with faces, named album "Leo"
        for pid in range(1, 4):
            _insert_face(db, pid, 0, 5)
        db.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("Leo", json.dumps({"cluster_id": 5})),
        )
        db.commit()

        # Manually run backfill logic (same as migration)
        rows = db.execute(
            "SELECT name, rule_json FROM albums WHERE album_type='smart_person'"
        ).fetchall()
        for name, rule_json in rows:
            rule = json.loads(rule_json)
            cid = rule.get("cluster_id")
            if cid is not None and not (name.startswith("Person ") and name[7:].isdigit()):
                db.execute(
                    "UPDATE face_embeddings SET identity = ? "
                    "WHERE cluster_id = ? AND identity IS NULL",
                    (name, cid),
                )
        db.commit()

        labeled = db.execute(
            "SELECT COUNT(*) FROM face_embeddings WHERE identity='Leo'"
        ).fetchone()[0]
        assert labeled == 3

    def test_skips_default_person_names(self, db):
        _insert_face(db, 1, 0, 7)
        db.execute(
            "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
            ("Person 42", json.dumps({"cluster_id": 7})),
        )
        db.commit()

        # Backfill should skip "Person N" names
        rows = db.execute(
            "SELECT name, rule_json FROM albums WHERE album_type='smart_person'"
        ).fetchall()
        for name, rule_json in rows:
            rule = json.loads(rule_json)
            cid = rule.get("cluster_id")
            if cid is not None and not (name.startswith("Person ") and name[7:].isdigit()):
                db.execute(
                    "UPDATE face_embeddings SET identity = ? "
                    "WHERE cluster_id = ? AND identity IS NULL",
                    (name, cid),
                )
        db.commit()

        labeled = db.execute(
            "SELECT COUNT(*) FROM face_embeddings WHERE identity IS NOT NULL"
        ).fetchone()[0]
        assert labeled == 0
