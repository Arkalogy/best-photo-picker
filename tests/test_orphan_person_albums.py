"""Regression tests: orphaned smart_person albums after face re-extraction.

When face extraction is fully re-run (`/api/v1/faces/retry`), the
cluster IDs assigned by clustering renumber. Two paths produce
orphan albums in the sidebar without these guards:

  1. ``photo_person_tags`` rows referencing the old (now-deleted)
     cluster IDs survive the wipe of ``face_embeddings``. The
     `_create_tag_only_clusters` step recreates a "Person N" album
     for each stale cluster_id that has any tags. Fixed by wiping
     ``photo_person_tags`` in the retry endpoint and by raising
     the minimum-tags threshold for tag-only album creation.

  2. The same single-tag-from-prior-cluster pattern can land via
     other paths (legacy DBs migrated from pre-fix versions). The
     ``_TAG_ONLY_MIN_PHOTOS`` filter is the belt-and-suspenders
     guard.
"""

from __future__ import annotations

import sqlite3

import pytest

from bpp.db.connection import init_db


@pytest.fixture()
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    yield c
    c.close()


def _insert_photo(conn, filepath: str) -> int:
    cur = conn.execute(
        "INSERT INTO photos (filepath, original_filename, file_size, file_mtime) "
        "VALUES (?, ?, ?, ?)",
        (filepath, filepath.rsplit("/", 1)[-1], 100, 1000.0),
    )
    conn.commit()
    return cur.lastrowid


class TestRetryWipesPersonTags:
    """The retry endpoint must wipe photo_person_tags alongside
    face_embeddings — otherwise stale tags create ghost albums in
    the next refresh pass."""

    def test_retry_handler_wipes_both_tables(self):
        # Source-scan: api_faces_retry must DELETE FROM both tables.
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        source = (repo / "bpp" / "web" / "bp_faces_extract.py").read_text()

        # Locate the api_faces_retry function body.
        start = source.find("def api_faces_retry")
        # Body ends at the next top-level def or @bp decorator after
        # the function start.
        end_marker = source.find("\n@bp.", start + 1)
        if end_marker == -1:
            end_marker = source.find("\ndef ", start + 50)
        body = source[start:end_marker] if end_marker > 0 else source[start:]

        assert "DELETE FROM face_embeddings" in body, (
            "api_faces_retry must wipe face_embeddings — that's the whole point."
        )
        assert "DELETE FROM photo_person_tags" in body, (
            "api_faces_retry must wipe photo_person_tags. Without this, manual "
            "person-tags pointing at the about-to-be-renumbered clusters get "
            "left behind, and the next refresh creates ghost 'Person N' albums "
            "for stale cluster_ids that no longer have any face data."
        )


class TestRetryJournalRecovery:
    """`api_faces_retry` opens a `face_extraction_retry` journal entry
    BEFORE wiping `face_embeddings` + `photo_person_tags`. The entry
    is cleared by the FaceWorker's success path. If the server SIGKILLs
    between the wipe and the worker completing, the recovery handler
    on next startup re-fires the worker — without it, a crash mid-flight
    strands the user with empty face data and no automatic recovery.
    """

    def test_retry_handler_opens_journal(self):
        """Source-scan: api_faces_retry must call journal_start with
        the 'face_extraction_retry' kind before any DELETE."""
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        source = (repo / "bpp" / "web" / "bp_faces_extract.py").read_text()
        # Find api_faces_retry body.
        start = source.find("def api_faces_retry")
        end_marker = source.find("\n@bp.", start + 1)
        body = source[start:end_marker] if end_marker > 0 else source[start:]

        assert "journal_start" in body, (
            "api_faces_retry must open a journal entry before wiping. "
            "Without it, a SIGKILL between wipe and extract leaves no "
            "breadcrumb for the next-startup recovery handler."
        )
        assert "face_extraction_retry" in body, (
            "Journal kind must be 'face_extraction_retry' to match the "
            "recovery handler in face_worker.register_face_extraction_retry_recovery."
        )
        # Order matters: journal MUST come before DELETE.
        journal_idx = body.find("journal_start")
        delete_idx = body.find("DELETE FROM face_embeddings")
        assert journal_idx > 0 and delete_idx > 0
        assert journal_idx < delete_idx, (
            "journal_start must run BEFORE DELETE. Otherwise a crash "
            "between the journal write and the DELETE leaves no breadcrumb."
        )

    def test_face_worker_success_clears_retry_journal(self):
        """Source-scan: FaceWorker._run must clear pending
        `face_extraction_retry` entries after the subprocess returns
        successfully. Otherwise the recovery handler re-fires the
        worker on every startup, even after a successful run."""
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        source = (repo / "bpp" / "web" / "face_worker.py").read_text()
        # The worker's _run method ends near the bottom of the class.
        # Look for the journal-cleanup pattern explicitly.
        assert "face_extraction_retry" in source
        assert "journal_complete" in source
        assert "pending_journals" in source, (
            "FaceWorker must enumerate pending face_extraction_retry "
            "entries via pending_journals(kind=...) and complete them "
            "after the subprocess returns. Without this, recovery fires "
            "on every startup."
        )

    def test_recovery_handler_registered(self):
        """Source-scan: register_face_extraction_retry_recovery must
        be called from WebAppState's startup recovery-binding path,
        next to register_face_clustering_recovery.

        Registration moved from state.py to state_lifecycle.py during
        the v0.1 cleanup — that's where the journal-recovery wiring
        lives now (called from WebAppState.startup → state_lifecycle.startup).
        """
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        lifecycle_src = (repo / "bpp" / "web" / "state_lifecycle.py").read_text()
        assert "register_face_extraction_retry_recovery" in lifecycle_src, (
            "register_face_extraction_retry_recovery must be wired into "
            "WebAppState's startup (via state_lifecycle._register_journal_recovery_handlers). "
            "Without the registration, the recovery handler is never invoked "
            "and pending entries pile up forever."
        )


class TestRetryWipeIsAuthoritative:
    """Stale-tag cleanup is the retry endpoint's responsibility, not
    the smart-album refresh path. Single tags are an intentional
    user-facing feature ("tag a photo as Mom before face detection
    runs") so the refresh path can't filter them out — it has no way
    to distinguish a stale-from-prior-extraction single tag from an
    intentional pre-extraction single tag.

    The retry endpoint runs only on explicit user intent ("re-extract
    everything from scratch"); wiping `photo_person_tags` there is the
    right scope for the destructive operation.
    """

    def test_orphan_tags_create_albums_when_refresh_runs_alone(self, conn):
        """Without a retry-wipe, refresh DOES recreate stale-single-tag
        person albums. This test pins the existing behaviour so a future
        change that filters tag-only clusters defensively doesn't break
        the intentional single-tag feature.
        """
        from bpp.db.smart_albums import _refresh_person_albums

        pid = _insert_photo(conn, "/tmp/p1.jpg")
        conn.execute(
            "INSERT INTO photo_person_tags (photo_id, cluster_id) VALUES (?, ?)",
            (pid, 99),
        )
        conn.commit()

        _refresh_person_albums(conn)

        rows = conn.execute(
            "SELECT id, name, rule_json FROM albums WHERE album_type='smart_person'"
        ).fetchall()
        assert len(rows) == 1, (
            "single-tag clusters must still create person albums — that's "
            "the intentional 'tag-only person' feature. Stale-tag cleanup "
            "belongs in the retry endpoint, not here."
        )
