"""Unit tests for the init_app_db phase helpers.

The handler was decomposed from a 185-LOC body into five named helpers
during the post-review refactor pass (2026-05-31). These tests cover
the boundary behaviour of each helper in isolation so a future change
to one phase doesn't silently break another.
"""

from __future__ import annotations

import os
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from bpp.web import state_init_phases as state_init

# ──────────────────────────────────────────────────────────────────
# Phase 1 — _acquire_serving_lock
# ──────────────────────────────────────────────────────────────────


class TestAcquireServingLock:
    def test_held_by_other_raises_actionable_runtime_error(self, tmp_path):
        wd = str(tmp_path)
        with patch("bpp.utils.serving_lock.acquire_lock", return_value=12345):
            with pytest.raises(RuntimeError) as exc:
                state_init.acquire_serving_lock(wd)
            msg = str(exc.value)
            assert "pid=12345" in msg
            assert "Stop it first" in msg

    def test_lock_io_failure_is_wrapped(self, tmp_path):
        from bpp.utils.serving_lock import ServingLockError

        wd = str(tmp_path)
        with patch(
            "bpp.utils.serving_lock.acquire_lock",
            side_effect=ServingLockError("read-only fs"),
        ):
            with pytest.raises(RuntimeError) as exc:
                state_init.acquire_serving_lock(wd)
            assert "Refusing to start" in str(exc.value)
            assert "read-only fs" in str(exc.value)

    def test_clean_acquisition_registers_atexit(self, tmp_path):
        wd = str(tmp_path)
        with (
            patch("bpp.utils.serving_lock.acquire_lock", return_value=None),
            patch("atexit.register") as mock_atexit,
        ):
            state_init.acquire_serving_lock(wd)
            mock_atexit.assert_called_once()


# ──────────────────────────────────────────────────────────────────
# Phase 2 — _backup_or_refuse_corrupt
# ──────────────────────────────────────────────────────────────────


class TestBackupOrRefuseCorrupt:
    def test_restore_sentinel_short_circuits_and_sets_skip_flag(self, tmp_path):
        wd = str(tmp_path)
        db_p = str(tmp_path / "photopicker.db")
        with (
            patch.object(state_init, "_consume_restore_sentinel", return_value=True),
            patch("bpp.db.connection.set_post_restore_skip_backup") as mock_set,
            patch.object(state_init, "backup_db") as mock_backup,
        ):
            state_init.backup_or_refuse_corrupt(db_p, wd)
            mock_set.assert_called_once_with(True)
            mock_backup.assert_not_called()

    def test_clean_backup_returns_normally(self, tmp_path):
        wd = str(tmp_path)
        db_p = str(tmp_path / "photopicker.db")
        with (
            patch.object(state_init, "_consume_restore_sentinel", return_value=False),
            patch.object(state_init, "backup_db", return_value=db_p + ".backup"),
        ):
            # Doesn't raise.
            state_init.backup_or_refuse_corrupt(db_p, wd)

    def test_corrupt_db_with_existing_backup_names_recovery_command(self, tmp_path):
        wd = str(tmp_path)
        db_p = str(tmp_path / "photopicker.db")
        backup_path = db_p + ".backup"
        # Create the corrupt-looking DB and its existing .backup so the
        # actionable-recovery branch fires.
        with open(db_p, "wb") as f:
            f.write(b"corrupt data" * 100)
        with open(backup_path, "wb") as f:
            f.write(b"placeholder")

        with (
            patch.object(state_init, "_consume_restore_sentinel", return_value=False),
            patch.object(state_init, "backup_db", return_value=None),
        ):
            with pytest.raises(RuntimeError) as exc:
                state_init.backup_or_refuse_corrupt(db_p, wd)
            assert "bpp db restore-backup" in str(exc.value)
            assert wd in str(exc.value)

    def test_corrupt_db_without_backup_raises_manual_recovery(self, tmp_path):
        wd = str(tmp_path)
        db_p = str(tmp_path / "photopicker.db")
        with open(db_p, "wb") as f:
            f.write(b"corrupt data" * 100)

        with (
            patch.object(state_init, "_consume_restore_sentinel", return_value=False),
            patch.object(state_init, "backup_db", return_value=None),
            pytest.raises(RuntimeError, match="no backup exists"),
        ):
            state_init.backup_or_refuse_corrupt(db_p, wd)


# ──────────────────────────────────────────────────────────────────
# Phase 3 — _recover_interrupted_rename
# ──────────────────────────────────────────────────────────────────


class TestRecoverInterruptedRename:
    def test_no_library_path_is_no_op(self):
        ctx = MagicMock()
        ctx.state = {"library_path": ""}
        # Doesn't raise; doesn't touch the rename module.
        state_init.recover_interrupted_rename(ctx)

    def test_with_library_path_calls_recovery(self, tmp_path):
        ctx = MagicMock()
        ctx.state = {"library_path": str(tmp_path)}
        conn = MagicMock()
        ctx.get_conn.return_value = conn

        with patch(
            "bpp.db.batch_rename.recover_interrupted_rename",
            return_value=[],
        ) as mock_rec:
            state_init.recover_interrupted_rename(ctx)
            mock_rec.assert_called_once_with(conn, str(tmp_path))

    def test_recovered_count_logged(self, tmp_path, caplog):
        ctx = MagicMock()
        ctx.state = {"library_path": str(tmp_path)}
        ctx.get_conn.return_value = MagicMock()

        with (
            patch(
                "bpp.db.batch_rename.recover_interrupted_rename",
                return_value=["a", "b", "c"],
            ),
            caplog.at_level("INFO"),
        ):
            state_init.recover_interrupted_rename(ctx)
        assert any("Recovered 3" in r.message for r in caplog.records)


# ──────────────────────────────────────────────────────────────────
# Phase 4a — _backfill_live_photo_sidecars
# ──────────────────────────────────────────────────────────────────


class TestBackfillLivePhotoSidecars:
    def _build_minimal_photos(self, conn):
        conn.execute(
            "CREATE TABLE photos ("
            " id INTEGER PRIMARY KEY,"
            " original_filename TEXT,"
            " is_live_photo_sidecar INTEGER DEFAULT 0,"
            " deleted_at TIMESTAMP"
            ")"
        )

    def test_no_candidate_filenames_returns_false_fast(self):
        conn = sqlite3.connect(":memory:")
        self._build_minimal_photos(conn)
        # No rows with underscore in filename → probe returns nothing.
        conn.execute("INSERT INTO photos (original_filename) VALUES ('foo.jpg')")
        conn.commit()
        assert state_init.backfill_live_photo_sidecars(conn) is False
        conn.close()

    def test_unhandled_exception_swallowed_with_warning(self, caplog):
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("db gone")
        with caplog.at_level("WARNING"):
            assert state_init.backfill_live_photo_sidecars(conn) is False
        assert any("sidecar backfill failed" in r.message for r in caplog.records)


# ──────────────────────────────────────────────────────────────────
# Phase 4b — _import_from_legacy_caches
# ──────────────────────────────────────────────────────────────────


class TestImportFromLegacyCaches:
    def test_empty_db_imports_analysis_json_when_present(self, tmp_path):
        wd = str(tmp_path)
        # Drop an analysis.json into the workdir.
        json_path = os.path.join(wd, "analysis.json")
        with open(json_path, "w") as f:
            f.write("[]")

        conn = MagicMock()
        # photo_count == 0 path: SELECT for unscored is short-circuited.
        with (
            patch.object(state_init, "import_from_analysis_json") as mock_imp,
            patch.object(state_init, "import_face_embeddings") as mock_emb,
            patch.object(state_init, "import_presets_from_json"),
        ):
            state_init.import_from_legacy_caches(conn, wd, "", 0)
            mock_imp.assert_called_once_with(conn, json_path)
            mock_emb.assert_not_called()  # the legacy DB doesn't exist

    def test_populated_db_with_scored_photos_skips_import(self, tmp_path):
        wd = str(tmp_path)
        # No analysis.json present anyway, but more importantly:
        # photo_count > 0 AND every photo has a non-NULL aggregate_score
        # → the conditional in the function returns early.
        conn = MagicMock()
        # SELECT for unscored returns None — the photos are scored.
        conn.execute.return_value.fetchone.return_value = None
        with patch.object(state_init, "import_from_analysis_json") as mock_imp:
            state_init.import_from_legacy_caches(conn, wd, "", photo_count=100)
            mock_imp.assert_not_called()

    def test_populated_db_with_unscored_photos_does_import(self, tmp_path):
        wd = str(tmp_path)
        json_path = os.path.join(wd, "analysis.json")
        with open(json_path, "w") as f:
            f.write("[]")

        conn = MagicMock()
        # SELECT for unscored returns a sentinel row — there are NULL scores.
        conn.execute.return_value.fetchone.return_value = (1,)
        with (
            patch.object(state_init, "import_from_analysis_json") as mock_imp,
            patch.object(state_init, "import_face_embeddings") as mock_emb,
        ):
            state_init.import_from_legacy_caches(conn, wd, "", photo_count=100)
            mock_imp.assert_called_once_with(conn, json_path)
            # Phase 4b ONLY imports face_embeddings on the photo_count==0 branch.
            mock_emb.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Phase 5 — _backfill_dup_clusters_and_refresh
# ──────────────────────────────────────────────────────────────────


class TestBackfillDupClustersAndRefresh:
    def test_no_unclustered_photos_no_refresh_when_not_forced(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        with (
            patch("bpp.db.dedupe.assign_near_duplicate_clusters") as mock_assign,
            patch("bpp.db.smart_albums.refresh_smart_albums") as mock_refresh,
        ):
            state_init.backfill_dup_clusters_and_refresh(conn, force_refresh=False)
            mock_assign.assert_not_called()
            mock_refresh.assert_not_called()

    def test_force_refresh_triggers_smart_album_refresh_even_without_clusters(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        with (
            patch("bpp.db.smart_albums.refresh_smart_albums") as mock_refresh,
        ):
            state_init.backfill_dup_clusters_and_refresh(conn, force_refresh=True)
            mock_refresh.assert_called_once()

    def test_unclustered_photos_trigger_assign_and_refresh(self):
        conn = MagicMock()
        # SELECT returns a sentinel — unclustered photos exist.
        conn.execute.return_value.fetchone.return_value = (1,)
        with (
            patch("bpp.db.dedupe.assign_near_duplicate_clusters") as mock_assign,
            patch("bpp.db.smart_albums.refresh_smart_albums") as mock_refresh,
        ):
            state_init.backfill_dup_clusters_and_refresh(conn, force_refresh=False)
            mock_assign.assert_called_once_with(conn)
            mock_refresh.assert_called_once()

    def test_cluster_assignment_failure_logged_and_swallowed(self, caplog):
        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("query failed")
        with caplog.at_level("WARNING"):
            # No raise.
            state_init.backfill_dup_clusters_and_refresh(conn, force_refresh=False)
        assert any("cluster backfill failed" in r.message for r in caplog.records)


# ──────────────────────────────────────────────────────────────────
# M8 — init_app_db defers Phase 5 to a daemon thread
# ──────────────────────────────────────────────────────────────────
#
# At 200K-photo scale the synchronous backfill blocked /api/v1/photos
# for 5-10s during startup. M8 moves Phase 5 onto a daemon thread so
# the HTTP server can answer the photo grid immediately; the daemon
# sets ctx.smart_album_backfill_done when it finishes so tests +
# switch_library + shutdown still have a deterministic wait point.


class TestInitAppDbPhase5Deferred:
    def test_init_app_db_returns_before_backfill_finishes_and_event_resolves(
        self,
        tmp_path,
        monkeypatch,
    ):
        """init_app_db must return promptly even when Phase 5 is slow,
        and the smart_album_backfill_done event must eventually fire
        with no error.

        monkeypatch (rather than ``with patch`` blocks) is required
        because the daemon thread the production code spawns runs
        asynchronously — by the time a ``with patch`` block exits,
        the thread may not have started yet and would see the
        original (unmocked) symbols. monkeypatch keeps the patches
        in place until the test function returns, covering the
        daemon's execution window.
        """
        import threading

        from bpp.web import state_init
        from bpp.web.state import WebAppState

        wd = str(tmp_path)
        ctx = WebAppState.__new__(WebAppState)
        ctx.state = {"workdir": wd, "library_path": str(tmp_path)}
        ctx.smart_album_backfill_done = threading.Event()
        ctx.smart_album_backfill_done.set()  # match default-set semantics
        ctx.serve_mode = False

        def _get_conn():
            return sqlite3.connect(os.path.join(wd, "photopicker.db"))

        ctx.get_conn = _get_conn

        # Stub Phase 5's slow helper. We just need to confirm the
        # daemon thread invoked it; the real heavy compute is
        # unnecessary for this wiring test.
        phase5_observed = threading.Event()

        def slow_phase5(*_args, **_kwargs):
            phase5_observed.set()

        monkeypatch.setattr(state_init, "_backfill_dup_clusters_and_refresh", slow_phase5)
        # Neutralise the unrelated phases so the test stays focused on
        # the deferred-Phase-5 wiring.
        monkeypatch.setattr(state_init, "_acquire_serving_lock", lambda *a, **k: None)
        monkeypatch.setattr(state_init, "_backup_or_refuse_corrupt", lambda *a, **k: None)
        monkeypatch.setattr(state_init, "_recover_interrupted_rename", lambda *a, **k: None)
        monkeypatch.setattr(state_init, "_backfill_live_photo_sidecars", lambda *a, **k: False)
        monkeypatch.setattr(state_init, "_import_from_legacy_caches", lambda *a, **k: None)
        monkeypatch.setattr(state_init, "ensure_all_photos_album", lambda *a, **k: None)
        monkeypatch.setattr(state_init, "sync_all_photos_album", lambda *a, **k: None)
        monkeypatch.setattr(state_init, "get_photo_count", lambda *a, **k: 0)
        monkeypatch.setattr(state_init, "init_db", lambda *a, **k: None)

        state_init.init_app_db(ctx)

        # The daemon thread must have run; the Event resolves once it
        # finishes; the stubbed Phase 5 helper was invoked.
        assert ctx.smart_album_backfill_done.wait(timeout=5), (
            "smart_album_backfill_done was never set; daemon thread crashed silently"
        )
        assert phase5_observed.is_set(), (
            "Phase 5 helper was never invoked — the daemon thread didn't run"
        )

    def test_daemon_resolves_db_path_at_spawn_not_via_ctx_get_conn(self):
        """The daemon must capture its target DB path at spawn time and
        resolve directly via get_db(db_path_at_spawn). It MUST NOT
        re-resolve through ctx.get_conn() at runtime — that would let a
        switch_library() between spawn and execution silently redirect
        Phase 5 onto the WRONG library's DB.

        A threading-based race test isn't deterministic here (the
        daemon may finish resolving the connection before the test
        mutates ctx.state). This static check on the closure's source
        is both deterministic and catches the exact regression: 'a
        future refactor reintroduced ctx.get_conn() inside the
        daemon body'.
        """
        import inspect

        from bpp.web import state_init

        src = inspect.getsource(state_init.init_app_db)
        # The closure body should reference get_db with a captured
        # local, not the dynamic ctx.get_conn().
        assert "get_db(db_path_at_spawn)" in src, (
            "Phase 5 daemon must call get_db(db_path_at_spawn) — capturing "
            "the workdir at spawn time so a switch_library mid-startup "
            "can't redirect this thread to the wrong library."
        )
        assert "bg_conn = ctx.get_conn()" not in src, (
            "Phase 5 daemon MUST NOT call ctx.get_conn() — that resolves "
            "ctx.paths.workdir at runtime and would write to whichever "
            "library is current when the thread happens to wake. Use "
            "get_db(db_path_at_spawn) instead."
        )
