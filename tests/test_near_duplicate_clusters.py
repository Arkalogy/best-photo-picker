"""TDD: near-duplicate cluster assignment.

Tests are written BEFORE implementation and specify the contract for
assign_near_duplicate_clusters() in bpp/db/dedupe.py.

Design decisions documented here:
- dup_cluster_id  -- a stable integer assigned per cluster; all photos in
                     the same near-duplicate cluster share the same id.
                     Singleton photos (no near-duplicate) get cluster_id = 0.
- cluster_size    -- count of photos in the cluster; 1 for singletons.
- hamming_threshold -- default 8 bits out of 64. Photos whose min(dHash,aHash)
                       distance is ≤ this threshold are considered near-duplicates.
                       8 bits handles burst shots (same scene, minor motion).
                       Tighter than the CLI default (10) to avoid false positives
                       in diverse libraries.
- Time window     -- NOT enforced here (unlike CLI deduplicate()). Web-app
                     users see all near-duplicates regardless of time gap; they
                     can make their own judgment. Time-window filtering is a
                     CLI-only concern for batch selection.
- Idempotent      -- running twice produces the same result (cluster ids may
                     differ in value but groups will be the same).
- Sidecar exclusion -- is_live_photo_sidecar=1 photos are never clustered.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def db(tmp_path):
    from bpp.db.connection import init_db

    return init_db(str(tmp_path / "test.db"))


def _insert(conn, id_, phash, ahash=None, score=0.7, date="2024-07-01T10:00:00"):
    conn.execute(
        "INSERT INTO photos (id, filepath, original_filename, file_size, file_mtime, "
        "phash, ahash, aggregate_score, date) "
        "VALUES (?, ?, ?, 1024, 0.0, ?, ?, ?, ?)",
        (id_, f"/lib/img_{id_}.jpg", f"img_{id_}.jpg", phash, ahash, score, date),
    )
    conn.commit()


# ─── contract tests ──────────────────────────────────────────────────────────


class TestAssignNearDuplicateClusters:
    def test_exact_phash_match_forms_cluster(self, db):
        """Two photos with identical phash → same cluster, cluster_size=2."""
        from bpp.db.dedupe import assign_near_duplicate_clusters

        _insert(db, 1, phash=12345678)
        _insert(db, 2, phash=12345678)

        n = assign_near_duplicate_clusters(db)

        assert n == 2
        rows = db.execute(
            "SELECT id, dup_cluster_id, cluster_size FROM photos ORDER BY id"
        ).fetchall()
        assert rows[0][1] == rows[1][1], "same phash → same cluster_id"
        assert rows[0][2] == 2
        assert rows[1][2] == 2

    def test_phash_within_threshold_forms_cluster(self, db):
        """Photos differing by 4 bits (hamming=4 ≤ default 8) → same cluster."""
        from bpp.db.dedupe import assign_near_duplicate_clusters

        base = 0x0000_FFFF_0000_0000  # within SQLite signed int64 range
        close = base ^ 0b1111  # 4 bits different

        _insert(db, 1, phash=base)
        _insert(db, 2, phash=close)

        n = assign_near_duplicate_clusters(db)

        rows = db.execute("SELECT dup_cluster_id, cluster_size FROM photos ORDER BY id").fetchall()
        assert rows[0][0] == rows[1][0], "hamming=4 → same cluster"
        assert n == 2

    def test_phash_beyond_threshold_stays_separate(self, db):
        """Photos differing by 20 bits (> default 8) → different clusters."""
        from bpp.db.dedupe import assign_near_duplicate_clusters

        base = 0
        far = 0x7FFF_FFFF  # 31 bits set — hamming distance = 31 > 8

        _insert(db, 1, phash=base)
        _insert(db, 2, phash=far)

        assign_near_duplicate_clusters(db)

        rows = db.execute("SELECT dup_cluster_id, cluster_size FROM photos ORDER BY id").fetchall()
        # Both are singletons — dup_cluster_id=0 for both, cluster_size=1
        assert rows[0][0] == 0, "singleton → dup_cluster_id=0"
        assert rows[1][0] == 0, "singleton → dup_cluster_id=0"
        assert rows[0][1] == 1
        assert rows[1][1] == 1

    def test_singleton_cluster_size_is_1(self, db):
        """A photo with no near-duplicate gets cluster_size=1."""
        from bpp.db.dedupe import assign_near_duplicate_clusters

        _insert(db, 1, phash=111)
        _insert(db, 2, phash=999_999_999)  # far from 111

        assign_near_duplicate_clusters(db)

        rows = db.execute("SELECT cluster_size FROM photos ORDER BY id").fetchall()
        assert rows[0][0] == 1
        assert rows[1][0] == 1

    def test_three_way_cluster(self, db):
        """Three photos all within threshold of each other → one cluster of 3."""
        from bpp.db.dedupe import assign_near_duplicate_clusters

        base = 0
        _insert(db, 1, phash=base)
        _insert(db, 2, phash=base ^ 0b11)  # 2 bits off
        _insert(db, 3, phash=base ^ 0b1111)  # 4 bits off

        n = assign_near_duplicate_clusters(db)

        rows = db.execute("SELECT dup_cluster_id, cluster_size FROM photos ORDER BY id").fetchall()
        ids = {r[0] for r in rows}
        sizes = {r[1] for r in rows}
        assert len(ids) == 1, "all three should share the same cluster_id"
        assert sizes == {3}
        assert n == 3

    def test_null_phash_excluded_from_clustering(self, db):
        """Photos with NULL phash are not clustered (phash may not be computed yet)."""
        from bpp.db.dedupe import assign_near_duplicate_clusters

        _insert(db, 1, phash=None)
        _insert(db, 2, phash=None)

        n = assign_near_duplicate_clusters(db)

        assert n == 0
        rows = db.execute("SELECT cluster_size FROM photos ORDER BY id").fetchall()
        # NULL phash photos get cluster_size=1 (singleton) not 2
        assert all(r[0] == 1 for r in rows)

    def test_sidecar_photos_excluded(self, db):
        """Live Photo sidecars must never appear in duplicate clusters."""
        from bpp.db.dedupe import assign_near_duplicate_clusters

        _insert(db, 1, phash=99999)
        # Insert a sidecar with the same phash
        db.execute(
            "INSERT INTO photos (id, filepath, original_filename, file_size, file_mtime, "
            "phash, is_live_photo_sidecar) VALUES (?, ?, ?, 1024, 0.0, ?, 1)",
            (2, "/lib/img_2.jpg", "img_2.jpg", 99999),
        )
        db.commit()

        n = assign_near_duplicate_clusters(db)

        assert n == 0  # only one non-sidecar photo with that hash
        row = db.execute("SELECT cluster_size FROM photos WHERE id=1").fetchone()
        assert row[0] == 1

    def test_idempotent(self, db):
        """Running twice gives the same cluster groupings."""
        from bpp.db.dedupe import assign_near_duplicate_clusters

        _insert(db, 1, phash=5555)
        _insert(db, 2, phash=5555)

        assign_near_duplicate_clusters(db)
        first = db.execute("SELECT dup_cluster_id FROM photos ORDER BY id").fetchall()

        assign_near_duplicate_clusters(db)
        second = db.execute("SELECT dup_cluster_id FROM photos ORDER BY id").fetchall()

        # Group membership must be identical (cluster ids may differ but pairing must match)
        assert first[0][0] == first[1][0]  # both in same cluster first run
        assert second[0][0] == second[1][0]  # both in same cluster second run

    def test_returns_count_of_clustered_photos(self, db):
        """Return value is count of photos in non-singleton clusters."""
        from bpp.db.dedupe import assign_near_duplicate_clusters

        _insert(db, 1, phash=1)
        _insert(db, 2, phash=1)  # dup pair
        _insert(db, 3, phash=0x7FFF_0000)  # 30+ bits from 1 → singleton

        n = assign_near_duplicate_clusters(db)
        assert n == 2


class TestDuplicatesSmartAlbumUsesClusterSize:
    def test_cluster_size_gt1_appears_in_duplicates(self, db):
        """After clustering, photos with cluster_size>1 appear in Duplicates album."""
        from bpp.constants import ACTIVE_PHOTO_SQL
        from bpp.db.dedupe import assign_near_duplicate_clusters

        _insert(db, 1, phash=7777)
        _insert(db, 2, phash=7777)
        _insert(db, 3, phash=8888)  # singleton

        assign_near_duplicate_clusters(db)

        dups = db.execute(
            f"SELECT COUNT(*) FROM photos WHERE cluster_size > 1 AND {ACTIVE_PHOTO_SQL}"
        ).fetchone()[0]
        assert dups == 2

    def test_singletons_excluded_from_duplicates(self, db):
        """Singleton photos (cluster_size=1) do not appear in Duplicates album."""
        from bpp.constants import ACTIVE_PHOTO_SQL
        from bpp.db.dedupe import assign_near_duplicate_clusters

        _insert(db, 1, phash=1111)  # unique

        assign_near_duplicate_clusters(db)

        dups = db.execute(
            f"SELECT COUNT(*) FROM photos WHERE cluster_size > 1 AND {ACTIVE_PHOTO_SQL}"
        ).fetchone()[0]
        assert dups == 0
