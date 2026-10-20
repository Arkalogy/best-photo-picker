"""Tests for face cluster name preservation across re-analysis — part 2 of 2:
TestEmptyClusterGuard."""

from __future__ import annotations

from bpp.db.smart_albums import refresh_smart_albums
from tests._face_name_preservation_shared import (
    _create_named_person_album,
    _get_person_album_names,
    _get_photo_ids,
    _insert_embeddings,
    _make_db,
)

# ===========================================================================
# Tests: mid-extraction wipe guard (2026-06-11 incident)
# ===========================================================================


class TestEmptyClusterGuard:
    """A refresh that runs while face_embeddings is empty (faces/retry
    wiped the table; extraction still in flight) must NOT treat every
    person album as orphaned. The 2026-06-11 incident: a hash-computation
    refresh fired 9 minutes into a retry wipe and deleted all the named
    people with nothing to transfer the names onto."""

    def test_empty_embeddings_with_person_albums_skips_refresh(self, tmp_path):
        conn, _ = _make_db(tmp_path)
        pids = _get_photo_ids(conn)

        _insert_embeddings(conn, {0: pids[:5], 1: pids[5:8]})
        _create_named_person_album(conn, "Kai", 0, pids[:5])
        _create_named_person_album(conn, "Person 2", 1, pids[5:8])

        # faces/retry wipe: embeddings gone, albums still there.
        conn.execute("DELETE FROM face_embeddings")
        conn.commit()

        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        assert names.get(0) == "Kai", (
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
        _create_named_person_album(conn, "Kai", 0, pids[:5])

        conn.execute("DELETE FROM face_embeddings")
        conn.commit()
        refresh_smart_albums(conn)  # mid-flight refresh — must be a no-op

        # Extraction done: same person, renumbered cluster, partial overlap.
        _insert_embeddings(conn, {3: pids[:4] + pids[10:12]})
        refresh_smart_albums(conn)

        names = _get_person_album_names(conn)
        assert names.get(3) == "Kai", "name must transfer to the renumbered cluster"
