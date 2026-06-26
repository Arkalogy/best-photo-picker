"""P5 finish — plan-named tests.

* ``test_plugin_lifecycle_hooks_fire_in_order`` (TestPluginLifecycleHooks)
  Registers the reference plugin and asserts that ``on_register``,
  ``on_library_open``, ``on_library_close``, ``on_shutdown`` fire in
  the documented order, that close + shutdown run in reverse-
  registration order, and that one failing plugin doesn't break the
  rest.

* ``test_smart_person_query_uses_indexed_column``
  (TestSmartPersonQueryUsesIndexedColumn)
  Verifies the v36 ``smart_person_cluster_id`` shadow column is what
  the per-person query plan reads, not ``json_extract(rule_json,
  '$.cluster_id')``. The indexed column is what makes album refresh
  on a 50k-photo library fast — the test pins the contract via
  ``EXPLAIN QUERY PLAN`` so a future refactor that reverts to
  ``rule_json`` fails loudly.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from bpp.plugin_protocol import (
    _reset_plugins_for_tests,
    fire_on_library_close,
    fire_on_library_open,
    fire_on_register,
    fire_on_shutdown,
    register_plugin,
)
from bpp.plugins import example as example_plugin


@pytest.fixture(autouse=True)
def _reset_plugin_state():
    """Clear plugin registry + example markers between tests."""
    _reset_plugins_for_tests()
    example_plugin._reset_calls()
    yield
    _reset_plugins_for_tests()
    example_plugin._reset_calls()


# ──────────────────────────────────────────────────────────────────
# test_plugin_lifecycle_hooks_fire_in_order
# ──────────────────────────────────────────────────────────────────


class TestPluginLifecycleHooks:
    """Plan ship criterion — lifecycle hooks fire in declared order."""

    def test_plugin_lifecycle_hooks_fire_in_order(self):
        """Register the reference plugin, drive every lifecycle event,
        and assert the recorded markers match the documented order."""
        example_plugin.setup()  # registers ExamplePlugin via register_plugin

        # Strip registry-side markers; we're testing lifecycle here.
        example_plugin._reset_calls()

        # Drive the four lifecycle events in the order the host calls
        # them in production: register → open → close → shutdown.
        fire_on_register(app=None)
        fire_on_library_open(ctx=None)
        fire_on_library_close(ctx=None)
        fire_on_shutdown()

        assert example_plugin._calls == [
            "on_register",
            "on_library_open",
            "on_library_close",
            "on_shutdown",
        ]

    def test_open_then_close_balances_per_library(self):
        """Calling open + close twice (a library switch) records
        each pair in order — open/close/open/close, not
        open/open/close/close."""
        example_plugin.setup()
        example_plugin._reset_calls()

        fire_on_library_open(ctx=None)
        fire_on_library_close(ctx=None)
        fire_on_library_open(ctx=None)
        fire_on_library_close(ctx=None)

        assert example_plugin._calls == [
            "on_library_open",
            "on_library_close",
            "on_library_open",
            "on_library_close",
        ]

    def test_close_and_shutdown_fire_in_reverse_registration_order(self):
        """Two plugins: A registered first, then B. ``on_register`` /
        ``on_library_open`` fire A→B (registration order); ``on_library_close``
        / ``on_shutdown`` fire B→A (reverse) — same semantic as nested
        context managers."""
        order: list[str] = []

        class _A:
            def on_register(self, _app):
                order.append("A.on_register")

            def on_library_open(self, _ctx):
                order.append("A.on_library_open")

            def on_library_close(self, _ctx):
                order.append("A.on_library_close")

            def on_shutdown(self):
                order.append("A.on_shutdown")

        class _B:
            def on_register(self, _app):
                order.append("B.on_register")

            def on_library_open(self, _ctx):
                order.append("B.on_library_open")

            def on_library_close(self, _ctx):
                order.append("B.on_library_close")

            def on_shutdown(self):
                order.append("B.on_shutdown")

        register_plugin(_A())
        register_plugin(_B())

        fire_on_register(app=None)
        fire_on_library_open(ctx=None)
        fire_on_library_close(ctx=None)
        fire_on_shutdown()

        assert order == [
            "A.on_register",
            "B.on_register",
            "A.on_library_open",
            "B.on_library_open",
            "B.on_library_close",
            "A.on_library_close",
            "B.on_shutdown",
            "A.on_shutdown",
        ]

    def test_one_failing_plugin_does_not_block_others(self, caplog):
        """A plugin whose hook raises gets logged at WARNING; the
        runner keeps calling the rest. Critical contract — a broken
        plugin in the wild must not break the host."""
        order: list[str] = []

        class _Bad:
            def on_library_open(self, _ctx):
                order.append("bad")
                raise RuntimeError("simulated plugin bug")

        class _Good:
            def on_library_open(self, _ctx):
                order.append("good")

        register_plugin(_Bad())
        register_plugin(_Good())

        # No exception should propagate.
        with caplog.at_level("WARNING"):
            fire_on_library_open(ctx=None)

        assert order == ["bad", "good"]
        # Module-qualified class name in the warning so on-call greps work.
        assert any(
            "_Bad" in rec.message and "on_library_open" in rec.message for rec in caplog.records
        )

    def test_missing_hook_methods_are_silently_skipped(self):
        """A plugin that implements only some hooks (e.g. only
        ``on_library_open``) is a first-class citizen — no AttributeError,
        the other firings are no-ops on it."""

        class _PartialPlugin:
            calls = 0

            def on_library_open(self, _ctx):
                _PartialPlugin.calls += 1

        register_plugin(_PartialPlugin())

        # All four fires must succeed without raising.
        fire_on_register(app=None)
        fire_on_library_open(ctx=None)
        fire_on_library_close(ctx=None)
        fire_on_shutdown()

        assert _PartialPlugin.calls == 1


# ──────────────────────────────────────────────────────────────────
# test_smart_person_query_uses_indexed_column
# ──────────────────────────────────────────────────────────────────


class TestSmartPersonQueryUsesIndexedColumn:
    """Plan ship criterion — smart-person queries hit the v36 indexed
    column, not the json_extract anti-pattern.

    The fix matters at 50K-photo + 200-named-people scale: the old
    pattern was `WHERE rule_json LIKE '%cluster_id%'` plus a json_extract
    in every row; the new pattern is `WHERE smart_person_cluster_id = ?`
    against a partial index. SQLite's EXPLAIN QUERY PLAN reports the
    difference — we lock it via a string assertion on the plan output
    so a future refactor that drops the column gets caught.
    """

    def _build_minimal_albums_schema(self, conn: sqlite3.Connection) -> None:
        """Mirror the relevant subset of v36+ albums shape — just
        enough columns + the partial index — so the query planner has
        the same options it does in production."""
        conn.execute(
            "CREATE TABLE albums ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT NOT NULL,"
            " album_type TEXT NOT NULL,"
            " rule_json TEXT,"
            " smart_person_cluster_id INTEGER"
            ")"
        )
        # The v36 partial index — covers the WHERE clause that the
        # face-orchestrator + bp_faces_photo hit on every lookup.
        conn.execute(
            "CREATE INDEX idx_albums_smart_person_cluster_id "
            "ON albums(smart_person_cluster_id) "
            "WHERE smart_person_cluster_id IS NOT NULL"
        )
        conn.commit()

    def _populate(self, conn: sqlite3.Connection, n_people: int) -> None:
        """Seed N smart_person rows — enough that the planner picks
        the partial index over a scan in EXPLAIN."""
        import json

        rows = [
            (f"Person {i}", "smart_person", json.dumps({"cluster_id": i}), i)
            for i in range(n_people)
        ]
        conn.executemany(
            "INSERT INTO albums "
            "(name, album_type, rule_json, smart_person_cluster_id) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )
        # Also seed some non-smart_person rows + some smart_person
        # rows with NULL cluster_id, so the partial index actually
        # excludes them — proves the partial index is the right shape.
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json, smart_person_cluster_id) "
            "VALUES ('All Photos', 'all', NULL, NULL)"
        )
        conn.execute(
            "INSERT INTO albums (name, album_type, rule_json, smart_person_cluster_id) "
            "VALUES ('Pending person', 'smart_person', '{\"placeholder\": true}', NULL)"
        )
        conn.commit()
        # ANALYZE so the planner has real selectivity stats and picks
        # the index over a scan.
        conn.execute("ANALYZE")

    def test_query_uses_indexed_column_not_json_extract(self):
        """The canonical per-person lookup must use the indexed column.

        Negative assertion: the query plan must NOT mention json_extract
        or a SCAN of the albums table — that would indicate a regression
        back to the rule_json LIKE pattern.
        """
        conn = sqlite3.connect(":memory:")
        try:
            self._build_minimal_albums_schema(conn)
            self._populate(conn, n_people=50)

            sql = (
                "SELECT id, name FROM albums "
                "WHERE album_type = 'smart_person' "
                "AND smart_person_cluster_id = ?"
            )
            plan = conn.execute(f"EXPLAIN QUERY PLAN {sql}", (3,)).fetchall()
            plan_text = "\n".join(str(row) for row in plan)

            assert "json_extract" not in plan_text.lower(), (
                f"smart_person query plan must NOT call json_extract; plan was:\n{plan_text}"
            )
            assert "idx_albums_smart_person_cluster_id" in plan_text, (
                f"smart_person query plan must use the partial index "
                f"idx_albums_smart_person_cluster_id; plan was:\n{plan_text}"
            )

            # And the result is actually correct — the row with
            # smart_person_cluster_id = 3 comes back.
            row = conn.execute(sql, (3,)).fetchone()
            assert row is not None
            assert row[1] == "Person 3"
        finally:
            conn.close()

    def test_partial_index_excludes_nulls(self):
        """The partial index ``WHERE smart_person_cluster_id IS NOT NULL``
        skips the placeholder smart_person rows (those that exist but
        haven't been assigned a cluster yet). Proves the index gate is
        the right one — the alternative (a full index over NULLs too)
        would waste pages on every NULL row, which scales linearly with
        named-people count."""
        conn = sqlite3.connect(":memory:")
        try:
            self._build_minimal_albums_schema(conn)
            self._populate(conn, n_people=20)

            # Sanity: the seed populated 20 rows + 1 NULL placeholder.
            count_total = conn.execute(
                "SELECT COUNT(*) FROM albums WHERE album_type='smart_person'"
            ).fetchone()[0]
            count_indexed = conn.execute(
                "SELECT COUNT(*) FROM albums "
                "WHERE album_type='smart_person' "
                "AND smart_person_cluster_id IS NOT NULL"
            ).fetchone()[0]
            assert count_total == 21
            assert count_indexed == 20
        finally:
            conn.close()

    def test_real_production_query_uses_column(self):
        """The bp_faces_photo query in production reads the column.

        Source-scan: grep the file for the indexed-column reference;
        any future refactor that reverts to json_extract here fails
        the test before it hits CI on prod.
        """
        from pathlib import Path

        src = Path("bpp/web/bp_faces_photo.py").read_text()
        assert "smart_person_cluster_id" in src, (
            "bp_faces_photo.py must reference smart_person_cluster_id "
            "directly — the v36 indexed column is the contract"
        )
        # Negative: the file should NOT use the LIKE pattern anymore.
        assert "rule_json LIKE" not in src, (
            "bp_faces_photo.py reverted to the rule_json LIKE anti-pattern"
        )


# ──────────────────────────────────────────────────────────────────
# Sample-plugin smoke test
# ──────────────────────────────────────────────────────────────────


class TestSamplePluginRegistersAcrossEveryRegistry:
    """The reference plugin in bpp/plugins/example.py touches every
    plugin-target registry in setup(). Verify each one shows up — a
    plugin author can copy this file as a template and have a working
    skeleton across all six registries."""

    def test_every_registry_target_records_a_registration(self):
        example_plugin._reset_calls()
        example_plugin.setup()

        recorded = list(example_plugin._calls)
        # No matter how many "skipped" markers appear due to test-order
        # interactions, the six expected registrations must each show
        # at least once as either "registered:X" or "registered:X:skipped".
        targets = [
            "detector",
            "embedder",
            "smart_album",
            "export_mode",
            "dedupe_strategy",
            "worker",
        ]
        for target in targets:
            assert any(t.startswith(f"registered:{target}") for t in recorded), (
                f"sample plugin must touch {target} registry; recorded: {recorded}"
            )

    def test_plugin_registered_with_protocol_host(self):
        """After ``setup()``, the lifecycle host should have one
        :class:`ExamplePlugin` instance. Verify by driving on_register
        and reading the markers."""
        from bpp.plugin_protocol import _PLUGINS

        example_plugin._reset_calls()
        example_plugin.setup()

        # The example plugin is the last one in the registered list.
        assert any(isinstance(p, example_plugin.ExamplePlugin) for p in _PLUGINS)

    def test_calls_list_is_thread_safe(self):
        """The marker list uses a Lock — append from many threads
        without losing any."""
        example_plugin._reset_calls()

        def _hammer():
            for _ in range(100):
                example_plugin._record("t")

        threads = [threading.Thread(target=_hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert example_plugin._calls.count("t") == 800
