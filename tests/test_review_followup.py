"""Regression tests for the bpp-review (2026-05-31) follow-up fixes.

* M3 — :func:`bpp.web.face_phase_pipeline.register_face_phase` lets
  plugins splice phases between the built-ins by priority.
* M4 — :mod:`bpp.db.event_hooks` exposes post-analyze / post-cluster /
  post-import hook buses, swallow-and-log on plugin failure.
* M5 / L2 — ``docs/plugins.md`` documents the lifecycle hooks and the
  ``register_album_domain`` call so plugin authors don't go spelunking.
* L1 — :class:`SmartAlbumRegistry` supports an ``undeletable`` flag that
  ``bp_albums.py`` consults instead of hardcoding the ``"all"`` check.
* M6 — :class:`SmartAlbumRegistry` supports a ``ui_metadata_fn`` that
  ``bp_albums.py`` dispatches through instead of hardcoding the
  ``"smart_duplicates"`` branch.

Each section is independently runnable; tests do their own cleanup so
they don't leak registrations across the suite.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from bpp.db import event_hooks
from bpp.web import face_phase_pipeline as pipeline

# ──────────────────────────────────────────────────────────────────
# M3 — register_face_phase
# ──────────────────────────────────────────────────────────────────


class _DummyPhase:
    """Minimal FacePhase implementation for ordering tests."""

    journal_bit = pipeline._NO_JOURNAL

    def __init__(self, name: str) -> None:
        self.name = name

    def should_skip(self, _ctx: Any) -> bool:
        return False

    def rehydrate(self, _ctx: Any) -> None: ...

    def run(self, _ctx: Any) -> None: ...


@pytest.fixture(autouse=True)
def _reset_pipeline():
    yield
    pipeline._reset_face_phases_for_tests()


class TestRegisterFacePhase:
    def test_plugin_phase_inserted_at_priority(self):
        pipeline.register_face_phase(_DummyPhase("plugin_validate"), priority=650)
        names = [p.name for p in pipeline.build_pipeline()]
        assert names == [
            "method_reconciler",
            "preload_cached_embeddings",
            "partition_cached_vs_extract",
            "delete_stale_embeddings",
            "capture_dismissed_slots",
            "extract_new_embeddings",
            "cluster_faces",
            "plugin_validate",
            "reconstruct_identities",
        ]

    def test_plugin_phase_at_start(self):
        pipeline.register_face_phase(_DummyPhase("plugin_first"), priority=50)
        names = [p.name for p in pipeline.build_pipeline()]
        assert names[0] == "plugin_first"

    def test_plugin_phase_at_end(self):
        pipeline.register_face_phase(_DummyPhase("plugin_last"), priority=999)
        names = [p.name for p in pipeline.build_pipeline()]
        assert names[-1] == "plugin_last"

    def test_collision_without_replace_raises(self):
        pipeline.register_face_phase(_DummyPhase("dup"), priority=250)
        with pytest.raises(ValueError, match="already registered"):
            pipeline.register_face_phase(_DummyPhase("dup"), priority=260)

    def test_collision_with_replace_overwrites(self):
        first = _DummyPhase("dup")
        second = _DummyPhase("dup")
        pipeline.register_face_phase(first, priority=250)
        pipeline.register_face_phase(second, priority=260, replace=True)
        all_phases = pipeline.build_pipeline()
        dups = [p for p in all_phases if p.name == "dup"]
        assert dups == [second]

    def test_unregister_returns_true_when_present(self):
        pipeline.register_face_phase(_DummyPhase("droppable"), priority=180)
        assert pipeline.unregister_face_phase("droppable") is True
        assert "droppable" not in {p.name for p in pipeline.build_pipeline()}

    def test_unregister_returns_false_when_absent(self):
        assert pipeline.unregister_face_phase("never_existed") is False

    def test_builtin_pipeline_unchanged_when_no_plugins(self):
        builtin = [p.name for p in pipeline.FACE_PIPELINE]
        live = [p.name for p in pipeline.build_pipeline()]
        assert builtin == live


# ──────────────────────────────────────────────────────────────────
# M4 — post-analyze / post-cluster / post-import hooks
# ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_event_hooks():
    yield
    event_hooks._reset_for_tests()


class TestPostAnalyzeHook:
    def test_dispatch_calls_registered_hook(self):
        h = MagicMock()
        event_hooks.register_post_analyze_hook(h)
        conn = MagicMock()
        results = [{"filepath": "/a.jpg"}, {"filepath": "/b.jpg"}]
        event_hooks.dispatch_post_analyze(conn, results)
        h.assert_called_once_with(conn, results)

    def test_dispatch_short_circuits_on_empty_results(self):
        h = MagicMock()
        event_hooks.register_post_analyze_hook(h)
        event_hooks.dispatch_post_analyze(MagicMock(), [])
        h.assert_not_called()

    def test_dispatch_short_circuits_without_subscribers(self):
        # No assertion needed beyond "doesn't crash" — earlier the empty
        # subscriber list was a "for loop over zero items" anyway, but
        # the explicit branch is what we're pinning.
        event_hooks.dispatch_post_analyze(MagicMock(), [{"fp": "x"}])

    def test_failing_hook_does_not_break_others(self, caplog):
        bad = MagicMock(side_effect=RuntimeError("boom"))
        good = MagicMock()
        event_hooks.register_post_analyze_hook(bad)
        event_hooks.register_post_analyze_hook(good)
        with caplog.at_level("WARNING"):
            event_hooks.dispatch_post_analyze(MagicMock(), [{"fp": "x"}])
        good.assert_called_once()
        assert any("Post-analyze hook" in r.message for r in caplog.records)

    def test_unregister(self):
        h = MagicMock()
        event_hooks.register_post_analyze_hook(h)
        assert event_hooks.unregister_post_analyze_hook(h) is True
        event_hooks.dispatch_post_analyze(MagicMock(), [{"fp": "x"}])
        h.assert_not_called()
        # Idempotent: removing again returns False.
        assert event_hooks.unregister_post_analyze_hook(h) is False


class TestPostClusterHook:
    def test_dispatch_calls_hook_with_kind_and_count(self):
        h = MagicMock()
        event_hooks.register_post_cluster_hook(h)
        conn = MagicMock()
        event_hooks.dispatch_post_cluster(conn, "face", 12)
        h.assert_called_once_with(conn, "face", 12)

    def test_zero_clusters_still_fires(self):
        # post_cluster is different from post_analyze: zero clusters
        # is still a meaningful event ("clustering ran but nothing
        # to do"). Plugins may use it for "cluster invariant" checks.
        h = MagicMock()
        event_hooks.register_post_cluster_hook(h)
        event_hooks.dispatch_post_cluster(MagicMock(), "face", 0)
        h.assert_called_once()

    def test_failing_hook_isolated(self, caplog):
        bad = MagicMock(side_effect=ValueError("nope"))
        good = MagicMock()
        event_hooks.register_post_cluster_hook(bad)
        event_hooks.register_post_cluster_hook(good)
        with caplog.at_level("WARNING"):
            event_hooks.dispatch_post_cluster(MagicMock(), "pet", 5)
        good.assert_called_once()
        assert any("Post-cluster hook" in r.message for r in caplog.records)


class TestPostImportHook:
    def test_dispatch_calls_hook_with_ids_and_filepaths(self):
        h = MagicMock()
        event_hooks.register_post_import_hook(h)
        conn = MagicMock()
        ids = [10, 11, 12]
        fps = ["/a.jpg", "/b.jpg", "/c.jpg"]
        event_hooks.dispatch_post_import(conn, ids, fps)
        h.assert_called_once_with(conn, ids, fps)

    def test_empty_import_short_circuits(self):
        h = MagicMock()
        event_hooks.register_post_import_hook(h)
        event_hooks.dispatch_post_import(MagicMock(), [], [])
        h.assert_not_called()

    def test_failing_hook_isolated(self, caplog):
        bad = MagicMock(side_effect=OSError("disk full"))
        good = MagicMock()
        event_hooks.register_post_import_hook(bad)
        event_hooks.register_post_import_hook(good)
        with caplog.at_level("WARNING"):
            event_hooks.dispatch_post_import(MagicMock(), [1], ["/a.jpg"])
        good.assert_called_once()
        assert any("Post-import hook" in r.message for r in caplog.records)


# ──────────────────────────────────────────────────────────────────
# M5 / L2 — docs/plugins.md surface
# ──────────────────────────────────────────────────────────────────


class TestPluginsDocCoversNewSurface:
    def _read_docs(self) -> str:
        from pathlib import Path

        return Path("docs/plugins.md").read_text()

    def test_doc_documents_lifecycle_hooks(self):
        src = self._read_docs()
        # The four lifecycle methods MUST appear in the doc.
        for name in (
            "on_register",
            "on_library_open",
            "on_library_close",
            "on_shutdown",
        ):
            assert name in src, f"docs/plugins.md must document {name}"

    def test_doc_mentions_register_album_domain(self):
        assert "register_album_domain" in self._read_docs(), (
            "L2 — docs/plugins.md must point at register_album_domain "
            "so plugins know to tie a custom album type to a mutation domain"
        )

    def test_doc_mentions_post_event_hooks(self):
        src = self._read_docs()
        assert "register_post_analyze_hook" in src
        assert "register_post_cluster_hook" in src
        assert "register_post_import_hook" in src

    def test_doc_mentions_register_face_phase(self):
        assert "register_face_phase" in self._read_docs()


# ──────────────────────────────────────────────────────────────────
# L1 / M6 — SmartAlbumRegistry undeletable + ui_metadata_fn
# ──────────────────────────────────────────────────────────────────


class TestSmartAlbumRegistryExtras:
    def test_all_album_is_registry_undeletable(self):
        """The bp_albums delete handler used to hardcode the 'all'
        check; the registry now carries it. ``is_undeletable("all")``
        must be True for the built-in."""
        from bpp.db.smart_albums import SmartAlbumRegistry

        assert SmartAlbumRegistry.is_undeletable("all") is True

    def test_arbitrary_type_defaults_undeletable_false(self):
        from bpp.db.smart_albums import SmartAlbumRegistry

        assert SmartAlbumRegistry.is_undeletable("smart_score") is False
        assert SmartAlbumRegistry.is_undeletable("does_not_exist") is False

    def test_smart_duplicates_has_ui_metadata_fn(self):
        from bpp.db.smart_albums import SmartAlbumRegistry

        fn = SmartAlbumRegistry.get_ui_metadata_fn("smart_duplicates")
        assert callable(fn)

    def test_arbitrary_type_has_no_ui_metadata_fn(self):
        from bpp.db.smart_albums import SmartAlbumRegistry

        assert SmartAlbumRegistry.get_ui_metadata_fn("smart_recent") is None
        assert SmartAlbumRegistry.get_ui_metadata_fn("does_not_exist") is None

    def test_plugin_can_register_undeletable_type(self):
        from bpp.db.smart_albums import SmartAlbumRegistry

        try:
            SmartAlbumRegistry.register(
                "smart_plugin_protected",
                lambda _conn: None,
                lambda _conn, _rule: [],
                undeletable=True,
            )
            assert SmartAlbumRegistry.is_undeletable("smart_plugin_protected") is True
        finally:
            # Clean up — the registry is process-global.
            SmartAlbumRegistry._types.pop("smart_plugin_protected", None)
            SmartAlbumRegistry._hooks.pop("smart_plugin_protected", None)

    def test_builtin_user_renameable_types_flagged(self):
        """The user-renameable check used to be a hardcoded set inside
        _ensure_smart_album; it's a registry flag now (review 2026-06-12)."""
        from bpp.db.smart_albums import SmartAlbumRegistry

        for t in ("smart_person", "smart_pet", "smart_group"):
            assert SmartAlbumRegistry.is_user_renameable(t) is True, t
        assert SmartAlbumRegistry.is_user_renameable("smart_score") is False
        assert SmartAlbumRegistry.is_user_renameable("does_not_exist") is False

    def test_plugin_can_register_user_renameable_type(self, tmp_path):
        """A plugin album type with user_renameable=True keeps its
        user-set name across _ensure_smart_album refreshes."""
        from bpp.db.connection import init_db
        from bpp.db.smart_album_refreshers import _ensure_smart_album
        from bpp.db.smart_albums import SmartAlbumRegistry

        try:
            SmartAlbumRegistry.register(
                "smart_plugin_named",
                lambda _conn: None,
                lambda _conn, _rule: [],
                user_renameable=True,
            )
            conn = init_db(str(tmp_path / "t.db"))
            _ensure_smart_album(
                conn, name="Default", album_type="smart_plugin_named", rule={"x": 1}
            )
            conn.execute("UPDATE albums SET name='My Name' WHERE album_type='smart_plugin_named'")
            conn.commit()
            _ensure_smart_album(
                conn, name="Default", album_type="smart_plugin_named", rule={"x": 1}
            )
            row = conn.execute(
                "SELECT name FROM albums WHERE album_type='smart_plugin_named'"
            ).fetchone()
            assert row[0] == "My Name", f"refresh clobbered the user name: {row[0]}"
            conn.close()
        finally:
            SmartAlbumRegistry._types.pop("smart_plugin_named", None)
            SmartAlbumRegistry._hooks.pop("smart_plugin_named", None)

    def test_bp_albums_no_longer_hardcodes_all_check(self):
        """bp_albums.py used to read ``if album["album_type"] == "all":``
        for the delete refusal. The L1 fix moved that to the registry.
        Source-scan to make sure a future refactor doesn't reintroduce
        the hardcoded branch."""
        from pathlib import Path

        src = Path("bpp/web/bp_albums.py").read_text()
        # The new code reads through SmartAlbumRegistry.is_undeletable.
        assert "SmartAlbumRegistry.is_undeletable" in src
        # And the old hardcoded branch is gone.
        assert 'album["album_type"] == "all"' not in src

    def test_bp_albums_no_longer_hardcodes_smart_duplicates_check(self):
        """M6: the smart_duplicates UI-metadata dispatch must go through
        SmartAlbumRegistry.get_ui_metadata_fn, not the hardcoded
        if-equals branch."""
        from pathlib import Path

        src = Path("bpp/web/bp_albums.py").read_text()
        assert "SmartAlbumRegistry.get_ui_metadata_fn" in src
        assert 'album.get("album_type") == "smart_duplicates"' not in src


# ──────────────────────────────────────────────────────────────────
# Thread safety on the new plugin-touched globals (review followup)
# ──────────────────────────────────────────────────────────────────


class TestPluginGlobalsThreadSafety:
    """register_face_phase and the event-hook registers / dispatchers
    take internal locks so a worker dispatching while a plugin
    registers can't see a torn list / dict. Hammer them concurrently
    and assert (a) no exception escapes, (b) the final counts add up."""

    def test_register_face_phase_under_thread_storm(self):
        import threading

        # Start clean — the autouse fixture runs after the test, not
        # before this body. _reset is idempotent.
        pipeline._reset_face_phases_for_tests()

        N_THREADS = 16
        PER_THREAD = 50

        def _hammer(start: int) -> None:
            for i in range(PER_THREAD):
                name = f"plugin_phase_{start}_{i}"
                pipeline.register_face_phase(_DummyPhase(name), priority=1000 + start * 1000 + i)

        threads = [threading.Thread(target=_hammer, args=(i,)) for i in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Every (thread, iter) pair registered exactly one phase.
        live = pipeline.build_pipeline()
        plugin_names = {p.name for p in live if p.name.startswith("plugin_phase_")}
        assert len(plugin_names) == N_THREADS * PER_THREAD

    def test_event_hooks_concurrent_register_and_dispatch(self):
        """A long-running dispatch (slow callback) must not block a
        concurrent register, AND the register must not corrupt the
        list the dispatcher is iterating over."""
        import threading

        event_hooks._reset_for_tests()

        dispatched: list[int] = []

        def _slow_hook(_conn, results):
            # Hold the callback long enough to overlap with the
            # registration loop below.
            import time

            time.sleep(0.02)
            dispatched.append(len(results))

        event_hooks.register_post_analyze_hook(_slow_hook)

        # Kick off a dispatch on a background thread.
        dispatch_thread = threading.Thread(
            target=event_hooks.dispatch_post_analyze,
            args=(MagicMock(), [{"fp": "x"}]),
        )
        dispatch_thread.start()

        # Hammer registrations while the dispatch is in flight. None
        # should raise, and the test's own _slow_hook should still
        # have completed.
        def _registrar():
            for _ in range(200):
                event_hooks.register_post_analyze_hook(MagicMock())

        registrars = [threading.Thread(target=_registrar) for _ in range(4)]
        for t in registrars:
            t.start()
        for t in registrars:
            t.join()
        dispatch_thread.join(timeout=2)

        assert not dispatch_thread.is_alive(), "dispatch did not complete in time"
        assert dispatched == [1]


class TestPairVerdictUndo:
    """Undo for review-pairs verdicts (the toast's Undo action)."""

    @pytest.fixture()
    def conn(self, tmp_path):
        from bpp.db.connection import init_db

        return init_db(str(tmp_path / "t.db"))

    def test_same_verdict_undo_removes_latest_feedback_row(self, conn):
        from bpp.db.face_feedback import (
            get_face_feedback,
            store_face_feedback,
            undo_last_pair_feedback,
        )

        store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=0.4)
        store_face_feedback(conn, "merge", cluster_id_a=1, cluster_id_b=2, distance=0.5)
        assert undo_last_pair_feedback(conn, 1, 2) is True
        rows = get_face_feedback(conn)
        assert len(rows) == 1 and rows[0]["distance"] == 0.4, rows
        # Nothing left after undoing the older one too; further undo = False.
        assert undo_last_pair_feedback(conn, 2, 1) is True  # reversed order matches
        assert undo_last_pair_feedback(conn, 1, 2) is False

    def test_different_verdict_undo_decrements_then_deletes(self, conn):
        from bpp.db.face_feedback import (
            get_hard_negatives,
            store_hard_negative,
            undo_hard_negative,
        )

        store_hard_negative(conn, 3, 4)
        store_hard_negative(conn, 3, 4)  # count=2
        assert undo_hard_negative(conn, 4, 3) is True
        negs = get_hard_negatives(conn)
        assert len(negs) == 1 and negs[0]["count"] == 1, negs
        assert undo_hard_negative(conn, 3, 4) is True
        assert get_hard_negatives(conn) == []
        assert undo_hard_negative(conn, 3, 4) is False
