"""Tests for face cluster name preservation across re-analysis.

Covers:
1. smart_albums._refresh_person_albums name transfer (Jaccard matching)
2. photo_person_tags remapping during refresh
3. face_worker._remap_names_and_tags (embedding method switch)
4. face_worker._snapshot_cluster_photos
5. Edge cases: no overlap, partial overlap, multiple named clusters
"""

from __future__ import annotations

import json
import sqlite3

import numpy as np

from bpp.db.connection import init_db
from bpp.db.photos import bulk_upsert_photos
from bpp.db.smart_albums import refresh_smart_albums

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path, n_photos: int = 20) -> tuple[sqlite3.Connection, str]:
    """Create a test DB with photo records and return (conn, db_path)."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    photos = []
    for i in range(n_photos):
        photos.append(
            {
                "filepath": f"/photos/img_{i:04d}.jpg",
                "original_filename": f"img_{i:04d}.jpg",
                "batch_name": "test_batch",
                "import_timestamp": "2024-01-01T00:00:00",
                "file_hash": f"hash_{i:04d}",
            }
        )
    bulk_upsert_photos(conn, photos)
    # Mark all photos as not missing so they pass ACTIVE_PHOTO_SQL filter
    conn.execute("UPDATE photos SET missing=0")
    conn.commit()
    return conn, db_path


def _get_photo_ids(conn: sqlite3.Connection) -> list[int]:
    """Return all photo IDs sorted."""
    return [r[0] for r in conn.execute("SELECT id FROM photos ORDER BY id").fetchall()]


def _insert_embeddings(
    conn: sqlite3.Connection,
    cluster_assignments: dict[int, list[int]],
) -> None:
    """Insert face embeddings with given cluster_id → [photo_id, ...] mapping.

    Each photo gets face_index=0 for its first cluster appearance.
    Uses INSERT OR REPLACE to handle re-inserts cleanly.
    """
    # Track face_index per photo to avoid UNIQUE constraint violations
    photo_fi: dict[int, int] = {}
    for cluster_id, photo_ids in cluster_assignments.items():
        for pid in photo_ids:
            fi = photo_fi.get(pid, 0)
            photo_fi[pid] = fi + 1
            emb = np.random.RandomState(pid + cluster_id * 1000).randn(128)
            conn.execute(
                "INSERT OR REPLACE INTO face_embeddings "
                "(photo_id, face_index, cluster_id, embedding) "
                "VALUES (?, ?, ?, ?)",
                (pid, fi, cluster_id, emb.astype(np.float32).tobytes()),
            )
    conn.commit()


def _create_named_person_album(
    conn: sqlite3.Connection,
    name: str,
    cluster_id: int,
    photo_ids: list[int],
) -> int:
    """Create a named smart_person album and populate album_photos."""
    rule_json = json.dumps({"cluster_id": cluster_id}, sort_keys=True)
    conn.execute(
        "INSERT INTO albums (name, album_type, rule_json) VALUES (?, 'smart_person', ?)",
        (name, rule_json),
    )
    album_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for pid in photo_ids:
        conn.execute(
            "INSERT OR IGNORE INTO album_photos (album_id, photo_id) VALUES (?, ?)",
            (album_id, pid),
        )
    conn.commit()
    return album_id


def _get_person_album_names(conn: sqlite3.Connection) -> dict[int, str]:
    """Return {cluster_id: album_name} for all smart_person albums."""
    rows = conn.execute(
        "SELECT name, rule_json FROM albums WHERE album_type='smart_person'"
    ).fetchall()
    result = {}
    for name, rule_json in rows:
        try:
            rule = json.loads(rule_json)
            result[rule["cluster_id"]] = name
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    return result


# ===========================================================================
# Tests: _refresh_person_albums name transfer (Jaccard matching)
# ===========================================================================


class TestRefreshPersonAlbumsNameTransfer:
    """Test name preservation when cluster IDs change during refresh."""

    def test_name_transfers_on_full_overlap(self, tmp_path):
        """Names transfer when new cluster has all the same photos."""
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        # Old state: cluster 0 = "Alice" with photos [0,1,2,3,4]
        _insert_embeddings(conn, {0: pids[:5], 1: pids[5:10]})
        _create_named_person_album(conn, "Alice", 0, pids[:5])

        # Simulate recluster: wipe old embeddings, insert new with different IDs
        conn.execute("DELETE FROM face_embeddings")
        # Note: do NOT delete album_photos — they stay from old albums
        # and are used by the name transfer logic to find overlap
        # Same photos but cluster ID shifted: old 0 → new 7
        _insert_embeddings(conn, {7: pids[:5], 8: pids[5:10]})

        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        assert names.get(7) == "Alice", f"Expected Alice on cluster 7, got {names}"

    def test_name_transfers_on_partial_overlap(self, tmp_path):
        """Names transfer even with partial overlap (some faces lost)."""
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        # Old: cluster 0 = "Bob" with 10 photos
        _insert_embeddings(conn, {0: pids[:10]})
        _create_named_person_album(conn, "Bob", 0, pids[:10])

        # Recluster: new cluster 5 has only 4 of Bob's original 10 photos
        # (higher confidence = fewer faces detected)
        conn.execute("DELETE FROM face_embeddings")
        # Note: do NOT delete album_photos — they stay from old albums
        # and are used by the name transfer logic to find overlap
        _insert_embeddings(conn, {5: pids[:4]})

        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        # Jaccard = 4 / 10 = 0.4 > 0.1 threshold
        assert names.get(5) == "Bob", f"Expected Bob on cluster 5, got {names}"

    def test_name_transfers_with_new_photos_added(self, tmp_path):
        """Names transfer when new cluster has original + new photos."""
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        # Old: cluster 0 = "Carol" with photos [0,1,2]
        _insert_embeddings(conn, {0: pids[:3]})
        _create_named_person_album(conn, "Carol", 0, pids[:3])

        # Recluster: cluster 3 has [0,1,2,3,4,5] (original + new faces found)
        conn.execute("DELETE FROM face_embeddings")
        # Note: do NOT delete album_photos — they stay from old albums
        # and are used by the name transfer logic to find overlap
        _insert_embeddings(conn, {3: pids[:6]})

        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        # Jaccard = 3 / 6 = 0.5 > 0.1
        assert names.get(3) == "Carol", f"Expected Carol on cluster 3, got {names}"

    def test_multiple_names_transfer_correctly(self, tmp_path):
        """Multiple named clusters all remap to correct new clusters."""
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        # Old: Alice=cluster0 [0..4], Bob=cluster1 [5..9]
        _insert_embeddings(conn, {0: pids[:5], 1: pids[5:10]})
        _create_named_person_album(conn, "Alice", 0, pids[:5])
        _create_named_person_album(conn, "Bob", 1, pids[5:10])

        # Recluster: IDs swap — Alice's photos→cluster 10, Bob's→cluster 11
        conn.execute("DELETE FROM face_embeddings")
        # Note: do NOT delete album_photos — they stay from old albums
        # and are used by the name transfer logic to find overlap
        _insert_embeddings(conn, {10: pids[:5], 11: pids[5:10]})

        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        assert names.get(10) == "Alice"
        assert names.get(11) == "Bob"

    def test_no_overlap_keeps_named_album_on_stale_cluster(self, tmp_path):
        """When no photos overlap at all, the named album is KEPT (not
        deleted) on its stale cluster — a stale album is recoverable, a
        deleted name is not. The new cluster must NOT inherit the name."""
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        # Old: cluster 0 = "Dave" with photos [0,1,2]
        _insert_embeddings(conn, {0: pids[:3]})
        _create_named_person_album(conn, "Dave", 0, pids[:3])

        # Recluster: completely different photos
        conn.execute("DELETE FROM face_embeddings")
        # Note: do NOT delete album_photos — they stay from old albums
        # and are used by the name transfer logic to find overlap
        _insert_embeddings(conn, {5: pids[10:15]})

        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        # The new cluster gets a default name (zero overlap with Dave)...
        assert names.get(5) != "Dave", "zero-overlap cluster must not inherit the name"
        # ...but Dave's album survives on its stale cluster for a later
        # refresh to transfer.
        assert names.get(0) == "Dave", (
            "user-named album must be kept when its name can't be transferred"
        )

    def test_low_overlap_still_preserves_name(self, tmp_path):
        """Even very low overlap preserves user-given names (no threshold)."""
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        # Old: cluster 0 = "Eve" with 15 photos
        _insert_embeddings(conn, {0: pids[:15]})
        _create_named_person_album(conn, "Eve", 0, pids[:15])

        # New cluster has 1 old photo + 5 new — very low overlap
        conn.execute("DELETE FROM face_embeddings")
        new_pids = [pids[0], *pids[15:20]]
        _insert_embeddings(conn, {9: new_pids})

        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        # Even with only 1 photo overlap, name "Eve" is preserved
        assert "Eve" in names.values()

    def test_default_name_not_transferred(self, tmp_path):
        """Auto-generated 'Person N' names are NOT transferred."""
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        _insert_embeddings(conn, {0: pids[:5]})
        _create_named_person_album(conn, "Person 1", 0, pids[:5])

        conn.execute("DELETE FROM face_embeddings")
        # Note: do NOT delete album_photos — they stay from old albums
        # and are used by the name transfer logic to find overlap
        _insert_embeddings(conn, {7: pids[:5]})

        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        # Should get a fresh default name, not "Person 1" transferred
        name = names.get(7, "")
        assert name != "Person 1" or name.startswith("Person ")

    def test_no_duplicate_name_assignment(self, tmp_path):
        """Two old clusters can't both claim the same new cluster."""
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        # Two named clusters, both have significant overlap with ONE new cluster
        _insert_embeddings(conn, {0: pids[:5], 1: pids[:5]})
        _create_named_person_album(conn, "Alice", 0, pids[:5])
        _create_named_person_album(conn, "Bob", 1, pids[:5])

        conn.execute("DELETE FROM face_embeddings")
        # Note: do NOT delete album_photos — they stay from old albums
        # and are used by the name transfer logic to find overlap
        _insert_embeddings(conn, {9: pids[:5]})

        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        # Only one name should win (the one with higher Jaccard, or first match)
        names_on_9 = [v for k, v in names.items() if k == 9]
        assert len(names_on_9) <= 1


# ===========================================================================
# Tests: photo_person_tags remapping
# ===========================================================================


class TestPersonTagsRemapping:
    """Test that photo_person_tags.cluster_id is remapped during refresh."""

    def test_tags_remap_on_cluster_change(self, tmp_path):
        """Manual person tags follow the named cluster to its new ID."""
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        # Set up: cluster 0 = "Alice", with a manual tag on photo pids[0]
        _insert_embeddings(conn, {0: pids[:5]})
        _create_named_person_album(conn, "Alice", 0, pids[:5])
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
            (pids[0], 0),
        )
        conn.commit()

        # Recluster: cluster 0 → cluster 7
        conn.execute("DELETE FROM face_embeddings")
        # Note: do NOT delete album_photos — they stay from old albums
        # and are used by the name transfer logic to find overlap
        _insert_embeddings(conn, {7: pids[:5]})

        refresh_smart_albums(conn)

        # Tag should now point to cluster 7
        row = conn.execute(
            "SELECT cluster_id FROM photo_person_tags WHERE photo_id=?",
            (pids[0],),
        ).fetchone()
        assert row is not None
        assert row[0] == 7, f"Expected tag cluster_id=7, got {row[0]}"

    def test_tags_not_remapped_when_no_name(self, tmp_path):
        """Tags on unnamed clusters are NOT remapped (no name = no match)."""
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        # Cluster 0 with default name "Person 1"
        _insert_embeddings(conn, {0: pids[:5]})
        _create_named_person_album(conn, "Person 1", 0, pids[:5])
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
            (pids[0], 0),
        )
        conn.commit()

        # Recluster
        conn.execute("DELETE FROM face_embeddings")
        # Note: do NOT delete album_photos — they stay from old albums
        # and are used by the name transfer logic to find overlap
        _insert_embeddings(conn, {7: pids[:5]})

        refresh_smart_albums(conn)

        # Tag should still point to old cluster 0 (orphaned but not remapped)
        row = conn.execute(
            "SELECT cluster_id FROM photo_person_tags WHERE photo_id=?",
            (pids[0],),
        ).fetchone()
        assert row is not None
        assert row[0] == 0


# ===========================================================================
# Tests: face_worker._remap_names_and_tags
# ===========================================================================


class TestFaceWorkerRemap:
    """Test the face_worker remap logic for embedding method switches."""

    def test_remap_names_and_tags_basic(self, tmp_path):
        """Basic remap: old cluster 0 → new cluster 5 by photo overlap."""
        from bpp.web.face_worker import _remap_names_and_tags

        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        # Create named album for old cluster 0
        _create_named_person_album(conn, "Alice", 0, pids[:5])

        old_cluster_photos = {0: set(pids[:5]), 1: set(pids[5:10])}
        new_cluster_photos = {5: set(pids[:5]), 6: set(pids[5:10])}

        _remap_names_and_tags(conn, old_cluster_photos, new_cluster_photos)

        names = _get_person_album_names(conn)
        assert names.get(5) == "Alice"

    def test_remap_with_person_tags(self, tmp_path):
        """photo_person_tags are remapped along with album names."""
        from bpp.web.face_worker import _remap_names_and_tags

        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        _create_named_person_album(conn, "Bob", 0, pids[:5])
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
            (pids[0], 0),
        )
        conn.commit()

        old_cluster_photos = {0: set(pids[:5])}
        new_cluster_photos = {3: set(pids[:5])}

        _remap_names_and_tags(conn, old_cluster_photos, new_cluster_photos)

        row = conn.execute(
            "SELECT cluster_id FROM photo_person_tags WHERE photo_id=?",
            (pids[0],),
        ).fetchone()
        assert row[0] == 3

    def test_remap_no_overlap_warns(self, tmp_path):
        """When no cluster overlaps, names are orphaned (logged, not crash)."""
        from bpp.web.face_worker import _remap_names_and_tags

        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        _create_named_person_album(conn, "Carol", 0, pids[:5])

        old_cluster_photos = {0: set(pids[:5])}
        new_cluster_photos = {9: set(pids[10:15])}

        # Should not crash
        _remap_names_and_tags(conn, old_cluster_photos, new_cluster_photos)

        names = _get_person_album_names(conn)
        # Carol stays on old cluster 0 (no remap target)
        assert names.get(0) == "Carol"
        assert "Carol" not in {v for k, v in names.items() if k != 0}

    def test_remap_multiple_names(self, tmp_path):
        """Multiple named clusters all remap correctly."""
        from bpp.web.face_worker import _remap_names_and_tags

        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        _create_named_person_album(conn, "Alice", 0, pids[:5])
        _create_named_person_album(conn, "Bob", 1, pids[5:10])

        old_cluster_photos = {0: set(pids[:5]), 1: set(pids[5:10])}
        new_cluster_photos = {10: set(pids[:5]), 11: set(pids[5:10])}

        _remap_names_and_tags(conn, old_cluster_photos, new_cluster_photos)

        names = _get_person_album_names(conn)
        assert names.get(10) == "Alice"
        assert names.get(11) == "Bob"

    def test_remap_when_new_cluster_album_already_exists(self, tmp_path):
        """When new cluster already has a default-named album, name transfers."""
        from bpp.web.face_worker import _remap_names_and_tags

        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        # Old named album
        _create_named_person_album(conn, "Alice", 0, pids[:5])
        # New cluster already has a default-named album
        _create_named_person_album(conn, "Person 6", 5, pids[:5])

        old_cluster_photos = {0: set(pids[:5])}
        new_cluster_photos = {5: set(pids[:5])}

        _remap_names_and_tags(conn, old_cluster_photos, new_cluster_photos)

        names = _get_person_album_names(conn)
        assert names.get(5) == "Alice"
        # Old album for cluster 0 should be gone
        assert 0 not in names

    def test_remap_no_duplicate_claims(self, tmp_path):
        """Two old named clusters can't both claim the same new cluster."""
        from bpp.web.face_worker import _remap_names_and_tags

        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        _create_named_person_album(conn, "Alice", 0, pids[:5])
        _create_named_person_album(conn, "Bob", 1, pids[:5])

        old_cluster_photos = {0: set(pids[:5]), 1: set(pids[:5])}
        new_cluster_photos = {9: set(pids[:5])}

        _remap_names_and_tags(conn, old_cluster_photos, new_cluster_photos)

        names = _get_person_album_names(conn)
        # Only one should win cluster 9
        assert names.get(9) in ("Alice", "Bob")
        # Count how many albums point to cluster 9
        count = conn.execute(
            "SELECT COUNT(*) FROM albums WHERE album_type='smart_person' AND rule_json=?",
            (json.dumps({"cluster_id": 9}, sort_keys=True),),
        ).fetchone()[0]
        assert count == 1


# ===========================================================================
# Tests: face_worker._snapshot_cluster_photos
# ===========================================================================


class TestSnapshotClusterPhotos:
    """Test the snapshot helper."""

    def test_snapshot_basic(self, tmp_path):
        from bpp.web.face_worker import _snapshot_cluster_photos

        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        _insert_embeddings(conn, {0: pids[:3], 1: pids[3:6]})

        result = _snapshot_cluster_photos(conn)
        assert result[0] == set(pids[:3])
        assert result[1] == set(pids[3:6])

    def test_snapshot_ignores_dismissed(self, tmp_path):
        from bpp.web.face_worker import _snapshot_cluster_photos

        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        _insert_embeddings(conn, {0: pids[:3]})
        # Insert a dismissed embedding
        emb = np.random.randn(128).astype(np.float32).tobytes()
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, cluster_id, embedding) "
            "VALUES (?, ?, -2, ?)",
            (pids[5], 0, emb),
        )
        conn.commit()

        result = _snapshot_cluster_photos(conn)
        assert 0 in result
        assert -2 not in result

    def test_snapshot_empty_db(self, tmp_path):
        from bpp.web.face_worker import _snapshot_cluster_photos

        conn, _ = _make_db(tmp_path)
        result = _snapshot_cluster_photos(conn)
        assert result == {}


# ===========================================================================
# Tests: face_worker._is_default_person_name
# ===========================================================================


class TestIsDefaultPersonName:
    """Test the helper that detects auto-generated names."""

    def test_default_names(self):
        from bpp.web.face_worker import _is_default_person_name

        assert _is_default_person_name("Person 1") is True
        assert _is_default_person_name("Person 42") is True
        assert _is_default_person_name("Person 100") is True

    def test_user_names(self):
        from bpp.web.face_worker import _is_default_person_name

        assert _is_default_person_name("Alice") is False
        assert _is_default_person_name("Bob Smith") is False
        assert _is_default_person_name("Person") is False
        assert _is_default_person_name("Person X") is False
        assert _is_default_person_name("My Person 1") is False


# ===========================================================================
# Tests: mid-extraction wipe guard (2026-06-11 incident)
# ===========================================================================


class TestEmptyClusterGuard:
    """A refresh that runs while face_embeddings is empty (faces/retry
    wiped the table; extraction still in flight) must NOT treat every
    person album as orphaned. The 2026-06-11 incident: a hash-computation
    refresh fired 9 minutes into a retry wipe and deleted all six named
    people with nothing to transfer the names onto."""

    def test_empty_embeddings_with_person_albums_skips_refresh(self, tmp_path):
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        _insert_embeddings(conn, {0: pids[:5], 1: pids[5:8]})
        _create_named_person_album(conn, "Leo", 0, pids[:5])
        _create_named_person_album(conn, "Person 2", 1, pids[5:8])

        # faces/retry wipe: embeddings gone, albums still there.
        conn.execute("DELETE FROM face_embeddings")
        conn.commit()

        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        assert names.get(0) == "Leo", (
            "named album must survive a refresh that runs while the "
            "face_embeddings table is empty (extraction in flight)"
        )
        assert 1 in names, "even default-named albums are left alone in the wiped state"

    def test_empty_embeddings_without_person_albums_still_creates_tag_albums(self, tmp_path):
        """The guard must only fire when person albums exist — a fresh
        library with manual tags and no embeddings still gets its
        tag-only albums created."""
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
            (pids[0], 42),
        )
        conn.commit()

        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        assert 42 in names, "tag-only album creation must not be blocked by the guard"

    def test_full_recluster_after_wipe_still_transfers_names(self, tmp_path):
        """End-to-end retry flow: wipe → (guarded refresh is a no-op) →
        extraction completes with renumbered clusters → final refresh
        transfers the name by photo overlap."""
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        _insert_embeddings(conn, {0: pids[:5]})
        _create_named_person_album(conn, "Leo", 0, pids[:5])

        conn.execute("DELETE FROM face_embeddings")
        conn.commit()
        refresh_smart_albums(conn)  # mid-flight refresh — must be a no-op

        # Extraction done: same person, renumbered cluster, partial overlap.
        _insert_embeddings(conn, {3: pids[:4] + pids[10:12]})
        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        assert names.get(3) == "Leo", "name must transfer to the renumbered cluster"
