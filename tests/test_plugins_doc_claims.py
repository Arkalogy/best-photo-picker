"""``docs/plugins.md`` describes the plugin surface as it is."""

from __future__ import annotations

import pathlib

DOC = (pathlib.Path(__file__).resolve().parents[1] / "docs" / "plugins.md").read_text()


def test_the_doc_does_not_claim_a_plugin_scorer_ranking_survives_a_restart():
    # aggregate_score is recomputed from the stored per-scorer columns on
    # every recompute, and a plugin scorer's score is not stored.
    assert "survives restart" not in DOC
    assert "does not survive" in DOC or "is recomputed" in DOC


def test_the_doc_names_the_restore_hook_and_the_cli_lifecycle():
    assert "on_db_restore" in DOC
    assert "load_plugins_for_cli" in DOC or "bpp select" in DOC


def test_the_doc_knows_the_export_dropdown_is_registry_driven():
    assert "hardcoded HTML today" not in DOC
    assert "/api/v1/export/modes" in DOC


def test_the_doc_tells_plugin_toggle_keys_are_switchable():
    assert "all_toggle_keys" in DOC or "/api/v1/models/toggle" in DOC
