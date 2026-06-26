"""End-to-end tests for journal-based crash recovery.

Each test simulates a crash mid-operation by leaving a journal entry
in 'started' state, then runs `recover_pending` and asserts the
operation's effects were completed correctly.

Permanent delete is the highest-risk operation today — DB rows are
deleted, then files are removed, then cache is pruned. A crash
between any two of those steps leaves observable partial state that
the journal must recover.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_journal_handlers():
    from bpp.db.journal import _reset_handlers_for_tests

    _reset_handlers_for_tests()
    yield
    _reset_handlers_for_tests()


@pytest.fixture
def app(tmp_path):
    """A WebAppState rigged with a real library directory + photos."""
    from bpp.web.app import create_app

    lib = tmp_path / "library"
    photos_dir = lib / "photos"
    photos_dir.mkdir(parents=True)
    app = create_app(workdir=str(lib / "data"), library_path=str(lib))
    app.config["TESTING"] = True
    return app


def _add_photo_file(library_path: str, name: str) -> str:
    """Create a real file inside the library and return its path."""
    photos_dir = Path(library_path) / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    p = photos_dir / name
    p.write_bytes(b"fake photo bytes")
    return str(p)


# ─── permanent_delete recovery ──────────────────────────────────────


class TestPermanentDeleteRecovery:
    def test_pending_journal_replays_disk_cleanup(self, app, tmp_path):
        """Simulate a crash AFTER the DB delete, BEFORE the disk loop:
        a journal entry still in 'started' state with the filepaths.
        recover_pending must invoke the registered handler, the handler
        re-runs the disk cleanup, and the orphaned files disappear."""
        from bpp.db.journal import journal_start, pending_journals
        from bpp.web.bp_photos_manage import register_permanent_delete_recovery

        ctx = app.extensions["bpp"]
        lib = ctx.state["library_path"]

        # Two real files in the library — DB rows would have been deleted
        # by a (now-crashed) permanent_delete request. Files orphaned.
        f1 = _add_photo_file(lib, "orphan_a.jpg")
        f2 = _add_photo_file(lib, "orphan_b.jpg")
        assert os.path.exists(f1)
        assert os.path.exists(f2)

        # Build the same journal entry the live request would have written.
        with app.app_context():
            allowed = [os.path.realpath(lib)]
            wd = ctx.state.get("workdir")
            if wd:
                allowed.append(os.path.realpath(wd))
            journal_start(
                ctx.get_conn(),
                "permanent_delete",
                {
                    "filepaths": [f1, f2],
                    "sha256_map": {},
                    "allowed_dirs": allowed,
                },
            )

        # Bind the recovery handler with this ctx, then recover.
        register_permanent_delete_recovery(ctx)
        from bpp.db.journal import recover_pending

        with app.app_context():
            result = recover_pending(ctx.get_conn())

        assert result.get("permanent_delete") == 1
        assert not os.path.exists(f1)
        assert not os.path.exists(f2)
        # Journal entry consumed
        with app.app_context():
            assert pending_journals(ctx.get_conn()) == []

    def test_recovery_skips_files_outside_library(self, app, tmp_path):
        """A malformed/tampered journal entry pointing at a path outside
        the allowed dirs must NOT be acted on. The handler logs and
        skips, but still completes the journal (we trust the path-check
        upstream — if a future bug ever wrote one, recovery shouldn't
        amplify it)."""
        from bpp.db.journal import journal_start, recover_pending
        from bpp.web.bp_photos_manage import register_permanent_delete_recovery

        ctx = app.extensions["bpp"]
        lib = ctx.state["library_path"]

        # Sentinel file OUTSIDE the library
        outside = tmp_path / "outside.txt"
        outside.write_text("must not be deleted")

        with app.app_context():
            journal_start(
                ctx.get_conn(),
                "permanent_delete",
                {
                    "filepaths": [str(outside)],
                    "sha256_map": {},
                    "allowed_dirs": [os.path.realpath(lib)],  # outside not allowed
                },
            )

        register_permanent_delete_recovery(ctx)
        with app.app_context():
            recover_pending(ctx.get_conn())

        # Sentinel survived
        assert outside.exists()
        assert outside.read_text() == "must not be deleted"

    def test_recovery_idempotent_on_already_deleted_files(self, app, tmp_path):
        """Recovery must tolerate filepaths that are already gone — they
        were deleted by the original request before the crash, so the
        recovery's os.path.isfile check skips them. No exceptions."""
        from bpp.db.journal import journal_start, recover_pending
        from bpp.web.bp_photos_manage import register_permanent_delete_recovery

        ctx = app.extensions["bpp"]
        lib = ctx.state["library_path"]

        # File that "was" deleted before the journal recovery runs
        ghost = os.path.join(lib, "photos", "ghost.jpg")

        with app.app_context():
            journal_start(
                ctx.get_conn(),
                "permanent_delete",
                {
                    "filepaths": [ghost],
                    "sha256_map": {},
                    "allowed_dirs": [os.path.realpath(lib)],
                },
            )

        register_permanent_delete_recovery(ctx)
        with app.app_context():
            result = recover_pending(ctx.get_conn())
        # Recovery still completes — entry consumed, no crash
        assert result.get("permanent_delete") == 1


# ─── face_clustering recovery ───────────────────────────────────────


class TestFaceClusteringRecovery:
    def test_pending_journal_re_runs_smart_album_refresh(self, app):
        """Crash between cluster_id commit and smart-album refresh: a
        pending face_clustering journal entry. recover_pending must
        invoke the handler, which re-runs refresh_smart_albums (the
        user-visible "person albums reflect new clusters" guarantee)."""
        from unittest.mock import patch

        from bpp.db.journal import journal_start, pending_journals, recover_pending
        from bpp.web.face_worker import register_face_clustering_recovery

        ctx = app.extensions["bpp"]
        with app.app_context():
            journal_start(
                ctx.get_conn(),
                "face_clustering",
                {"unassigned_count": 12},
            )

        register_face_clustering_recovery()

        # Spy on refresh_smart_albums — recovery must call it
        with (
            patch("bpp.db.smart_albums.refresh_smart_albums") as refresh_spy,
            app.app_context(),
        ):
            result = recover_pending(ctx.get_conn())

        assert result.get("face_clustering") == 1
        assert refresh_spy.called, "face_clustering recovery must call refresh_smart_albums"
        # Journal entry consumed
        with app.app_context():
            assert pending_journals(ctx.get_conn(), kind="face_clustering") == []

    def test_clip_extraction_recovery_clears_breadcrumb(self, app):
        """CLIP extraction recovery is a no-op apart from clearing the
        journal entry — partial state is already idempotent because the
        next ClipWorker run picks up missing embeddings via WHERE NOT IN.
        Verify the entry consumes cleanly and doesn't pile up on
        successive startups."""
        from bpp.db.journal import journal_start, pending_journals, recover_pending
        from bpp.web.clip_worker import register_clip_extraction_recovery

        ctx = app.extensions["bpp"]
        with app.app_context():
            journal_start(
                ctx.get_conn(),
                "clip_extraction",
                {"total": 200, "model": "ViT-B-32"},
            )

        register_clip_extraction_recovery()
        with app.app_context():
            result = recover_pending(ctx.get_conn())

        assert result.get("clip_extraction") == 1
        with app.app_context():
            assert pending_journals(ctx.get_conn(), kind="clip_extraction") == []

    def test_recovery_survives_refresh_failure(self, app):
        """If refresh_smart_albums raises during recovery, the handler
        still consumes the journal entry (logged warning, not stuck)."""
        from unittest.mock import patch

        from bpp.db.journal import journal_start, pending_journals, recover_pending
        from bpp.web.face_worker import register_face_clustering_recovery

        ctx = app.extensions["bpp"]
        with app.app_context():
            journal_start(
                ctx.get_conn(),
                "face_clustering",
                {"unassigned_count": 1},
            )

        register_face_clustering_recovery()

        with (
            patch(
                "bpp.db.smart_albums.refresh_smart_albums",
                side_effect=RuntimeError("simulated DB lock"),
            ),
            app.app_context(),
        ):
            result = recover_pending(ctx.get_conn())

        # Handler returned True (best-effort) so entry got cleaned up.
        # If we left it stuck, every subsequent startup would re-attempt
        # and re-fail — worse UX than just logging once and moving on.
        assert result.get("face_clustering") == 1
        with app.app_context():
            assert pending_journals(ctx.get_conn(), kind="face_clustering") == []
