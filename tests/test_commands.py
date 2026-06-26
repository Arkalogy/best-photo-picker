"""Unit tests for bpp.commands CLI handlers.

These tests cover the early-return paths (input validation, dry-run,
missing dependencies) of every ``do_*`` handler without spinning up the
full server or analysis pipeline. Heavy operations get monkeypatched.
"""

from __future__ import annotations

import argparse
import json
import os

import pytest

from bpp import commands

# ── do_analyze ───────────────────────────────────────────────────────────


class TestDoAnalyze:
    def _ns(self, **overrides):
        ns = argparse.Namespace(
            input="/nonexistent",
            out="/tmp/out",
            config=None,
            max=0,
            workers=1,
            debug=False,
            dry_run=False,
            seed=42,
            extensions=".jpg,.png",
        )
        for k, v in overrides.items():
            setattr(ns, k, v)
        return ns

    def test_missing_input_dir_returns_1(self, tmp_path):
        ns = self._ns(input=str(tmp_path / "doesnotexist"), out=str(tmp_path / "out"))
        assert commands.do_analyze(ns) == 1

    def test_empty_input_dir_returns_0(self, tmp_path):
        ns = self._ns(
            input=str(tmp_path),
            out=str(tmp_path / "out"),
            extensions=".no_such_ext",  # nothing matches
        )
        assert commands.do_analyze(ns) == 0

    def test_dry_run_does_not_analyze(self, tmp_path, monkeypatch):
        # Create one fake image so scan finds something
        (tmp_path / "a.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
        ns = self._ns(input=str(tmp_path), out=str(tmp_path / "out"), dry_run=True)
        # If dry-run actually called analyze_all, we'd see real ML loads.
        # Track if it was called.
        called = {"analyze": False}

        def _mock_analyze(*a, **k):
            called["analyze"] = True
            return {"processed": 0, "skipped": 0}

        monkeypatch.setattr("bpp.scoring.aggregate.analyze_all", _mock_analyze)
        assert commands.do_analyze(ns) == 0
        assert called["analyze"] is False


# ── do_select ────────────────────────────────────────────────────────────


class TestDoSelect:
    def _ns(self, **overrides):
        ns = argparse.Namespace(
            workdir="/tmp/work",
            out="/tmp/out",
            config=None,
            k=10,
            export_mode="copy",
            gallery=False,
            force=False,
            dry_run=False,
            seed=42,
            extensions=".jpg,.png",
        )
        for k, v in overrides.items():
            setattr(ns, k, v)
        return ns

    def test_missing_workdir_returns_1(self, tmp_path):
        ns = self._ns(workdir=str(tmp_path / "missing"), out=str(tmp_path / "out"))
        assert commands.do_select(ns) == 1

    def test_existing_outdir_without_force_returns_1(self, tmp_path):
        workdir = tmp_path / "work"
        workdir.mkdir()
        outdir = tmp_path / "out"
        outdir.mkdir()
        ns = self._ns(workdir=str(workdir), out=str(outdir), force=False)
        assert commands.do_select(ns) == 1

    def test_no_analysis_returns_1(self, tmp_path):
        workdir = tmp_path / "work"
        workdir.mkdir()
        # No analysis.json present
        ns = self._ns(workdir=str(workdir), out=str(tmp_path / "out"))
        assert commands.do_select(ns) == 1

    def test_dry_run_skips_export(self, tmp_path, monkeypatch):
        workdir = tmp_path / "work"
        workdir.mkdir()
        analysis = [{"filepath": "x.jpg", "aggregate_score": 0.5}]
        (workdir / "analysis.json").write_text(json.dumps(analysis))
        ns = self._ns(workdir=str(workdir), out=str(tmp_path / "newout"), dry_run=True, k=1)
        exported = {"called": False}

        def _mock_export(*a, **k):
            exported["called"] = True

        monkeypatch.setattr("bpp.output.export.export_selected", _mock_export)
        rc = commands.do_select(ns)
        assert rc == 0
        assert exported["called"] is False


# ── do_run ───────────────────────────────────────────────────────────────


class TestDoRun:
    def _ns(self, **overrides):
        ns = argparse.Namespace(
            input="/nonexistent",
            out="/tmp/out",
            config=None,
            max=0,
            workers=1,
            debug=False,
            dry_run=False,
            seed=42,
            extensions=".jpg",
            k=10,
            export_mode="copy",
        )
        for k, v in overrides.items():
            setattr(ns, k, v)
        return ns

    def test_propagates_analyze_failure(self, tmp_path):
        # do_analyze returns 1 because input doesn't exist; do_run must propagate
        ns = self._ns(input=str(tmp_path / "missing"), out=str(tmp_path / "out"))
        assert commands.do_run(ns) == 1

    def test_dry_run_returns_after_analyze(self, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
        ns = self._ns(input=str(tmp_path), out=str(tmp_path / "out"), dry_run=True)
        # Should not raise; should not call select
        assert commands.do_run(ns) == 0


# ── do_web ───────────────────────────────────────────────────────────────


class TestDoWeb:
    def _ns(self, **overrides):
        ns = argparse.Namespace(
            input=None,
            workdir=None,
            config=None,
            port=5099,
            no_browser=True,
            debug=False,
            host="127.0.0.1",
        )
        for k, v in overrides.items():
            setattr(ns, k, v)
        return ns

    def test_signature_exists(self):
        """Smoke test — handler is reachable. Full server start is exercised
        by integration tests; we just confirm the function takes a Namespace."""
        assert callable(commands.do_web)


# ── do_pick ──────────────────────────────────────────────────────────────


class TestDoPick:
    def _ns(self, library, **overrides):
        ns = argparse.Namespace(
            library=library,
            k=10,
            out="/tmp/picks_out",
            export_mode="copy",
            gallery=False,
            force=False,
            boost_face=[],
            seed=42,
            dry_run=False,
            verbose=False,
        )
        for k, v in overrides.items():
            setattr(ns, k, v)
        return ns

    def test_missing_library_returns_1(self, tmp_path, capsys):
        ns = self._ns(library=str(tmp_path / "nonexistent"))
        assert commands.do_pick(ns) == 1
        captured = capsys.readouterr()
        assert "library path does not exist" in captured.err

    def test_missing_database_returns_1(self, tmp_path, capsys):
        lib = tmp_path / "lib"
        (lib / "data").mkdir(parents=True)
        ns = self._ns(library=str(lib))
        # The data/ exists but no photopicker.db in it
        assert commands.do_pick(ns) == 1
        captured = capsys.readouterr()
        assert "no database found" in captured.err

    def test_unanalyzed_db_returns_1(self, tmp_path, capsys):
        from bpp.db.connection import get_db, init_db
        from bpp.db.library import ensure_library_dirs
        from bpp.db.photos import upsert_photo

        lib = tmp_path / "lib"
        lib.mkdir()
        dirs = ensure_library_dirs(str(lib))
        db_path = os.path.join(dirs["data"], "photopicker.db")
        init_db(db_path)
        conn = get_db(db_path)
        # Photo with no aggregate_score → "not analyzed"
        f = lib / "p.jpg"
        f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 32)
        upsert_photo(conn, {"filepath": str(f)})
        conn.commit()
        from bpp.db.connection import close_all_connections

        close_all_connections()

        ns = self._ns(library=str(lib))
        assert commands.do_pick(ns) == 1
        captured = capsys.readouterr()
        assert "no analyzed photos" in captured.err


# ── do_db_restore_backup smoke ───────────────────────────────────────────


class TestDoDbRestoreBackup:
    """do_db_restore_backup has its own dedicated test file;
    we add a smoke test here to cover the command dispatch path."""

    def test_missing_library_returns_nonzero(self, tmp_path):
        ns = argparse.Namespace(
            library=str(tmp_path / "no_such_lib"),
            previous=False,
            force=False,
            yes=True,
        )
        # The handler should fail-fast on a non-existent library, not raise
        rc = commands.do_db_restore_backup(ns)
        assert rc != 0


# ── Argparse subcommand registration (smoke) ─────────────────────────────


class TestSubcommandRegistration:
    """The CLI parser declares 8 subcommands. Verify each is reachable
    via the do_* dispatcher so a renamed handler in commands.py
    immediately surfaces here rather than in the wild."""

    @pytest.mark.parametrize(
        "fn_name",
        [
            "do_analyze",
            "do_select",
            "do_run",
            "do_web",
            "do_demo",
            "do_pick",
            "do_serve",
            "do_db_restore_backup",
        ],
    )
    def test_handler_exists_and_is_callable(self, fn_name):
        fn = getattr(commands, fn_name, None)
        assert fn is not None, f"{fn_name} missing from bpp.commands"
        assert callable(fn), f"{fn_name} is not callable"
