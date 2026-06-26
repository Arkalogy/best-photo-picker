"""Tests for bpp.constants helpers + source-scan rules around constants."""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from bpp.constants import ACTIVE_PHOTO_SQL, active_photo_sql


class TestActivePhotoSql:
    """active_photo_sql() must produce correct WHERE clause fragments."""

    def test_no_alias_matches_constant(self):
        assert active_photo_sql() == ACTIVE_PHOTO_SQL

    def test_alias_prefixes_all_columns(self):
        result = active_photo_sql("p")
        assert "p.missing" in result
        assert "p.deleted_at" in result
        assert "p.hidden_at" in result
        # No un-prefixed columns
        parts = result.split(" AND ")
        for part in parts:
            col = part.strip().split("=")[0].split()[0]
            assert col.startswith("p."), f"Column {col!r} missing alias prefix"

    def test_empty_alias_same_as_no_alias(self):
        assert active_photo_sql("") == active_photo_sql()


class TestNoHardcodedClusterSentinels:
    """Cluster sentinels MUST use CLUSTER_UNASSIGNED / CLUSTER_DISMISSED
    constants, never raw -1 / -2. Source-scan locks the rule so it
    can't drift back into either production code or tests.

    Allow-list: bpp/constants.py itself defines the constants, and a few
    docstrings legitimately reference the historical sentinel values."""

    REPO_ROOT = Path(__file__).resolve().parent.parent
    # Match `cluster_id = -1`, `cluster_id == -2`, `cluster_id=-1`, etc.
    PATTERN = re.compile(
        r"cluster_id\s*(?:=|==)\s*-[12]\b",
        re.IGNORECASE,
    )

    def _scan_dir(self, root: Path) -> list[tuple[Path, int, str]]:
        """Yield (path, lineno, line) for any hits, skipping comments and
        any line that's part of a docstring or contains triple-quotes
        (where references to the sentinels in prose are legitimate)."""
        hits: list[tuple[Path, int, str]] = []
        for py in root.rglob("*.py"):
            if py.name == "constants.py":
                continue
            in_doc = False
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                # Single-line triple-quote string (`"""x"""`) — skip the line.
                # Multi-line: track open/close state using triple-quote count.
                has_triple = '"""' in line or "'''" in line
                triple_count = line.count('"""') + line.count("'''")
                # Skip the line itself if it contains any triple-quote
                # (covers single-line docstrings and the boundary lines
                # of multi-line docstrings).
                if has_triple:
                    if triple_count % 2 == 1:
                        in_doc = not in_doc
                    continue
                if in_doc:
                    continue
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if self.PATTERN.search(line):
                    hits.append((py, i, line.strip()))
        return hits

    def test_no_hardcoded_cluster_sentinels_in_production(self):
        hits = self._scan_dir(self.REPO_ROOT / "bpp")
        assert not hits, (
            "Production code must use CLUSTER_UNASSIGNED / CLUSTER_DISMISSED, "
            "not raw -1/-2. Hits:\n" + "\n".join(f"  {p}:{ln} — {line}" for p, ln, line in hits)
        )

    def test_no_hardcoded_cluster_sentinels_in_tests(self):
        hits = self._scan_dir(self.REPO_ROOT / "tests")
        # Allow this test file itself (it contains the regex literal)
        hits = [h for h in hits if h[0].name != "test_constants.py"]
        assert not hits, (
            "Tests must use CLUSTER_UNASSIGNED / CLUSTER_DISMISSED, "
            "not raw -1/-2. Hits:\n" + "\n".join(f"  {p}:{ln} — {line}" for p, ln, line in hits)
        )


class TestNoRawSqlite3ConnectInProduction:
    """Production code MUST use `bpp.db.connection.get_db()`, never
    raw `sqlite3.connect()`. The pool is what gives us WAL mode,
    foreign keys, and the 30s busy timeout (centralised in
    `dialect.setup_connection`). Sites that bypass it lose those
    guarantees silently.

    Allow-list:
    - `bpp/db/connection.py` — the implementation file. `get_db` and
      `check_integrity` are exactly where the raw call belongs.
    - `bpp/db/migrate.py` — legacy code that reads a foreign
      `analysis_cache.db` outside the pool for one-shot import.
    - `bpp/web/share.py` — opens the foreign `.backup` / `.backup.prev`
      snapshot files briefly to overwrite the rotated share token
      after `regenerate_share_token`. These files aren't pool-managed
      live DBs; the connection is short-lived and closes inside the
      same function.
    - `bpp/commands.py` — `do_db_restore_backup` reads `user_version`
      from a backup file before restoring. The backup is a foreign DB
      (not pool-managed) and the connection is read-only and short-lived."""

    REPO_ROOT = Path(__file__).resolve().parent.parent
    PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"\bsqlite3\.connect\b")
    ALLOWED: ClassVar[set[str]] = {
        "bpp/db/connection.py",
        "bpp/db/backup.py",
        "bpp/db/integrity.py",
        "bpp/db/migrate.py",
        "bpp/web/share.py",
        "bpp/commands.py",
    }

    def test_no_raw_sqlite3_connect_outside_allowlist(self):
        hits: list[tuple[Path, int, str]] = []
        for py in (self.REPO_ROOT / "bpp").rglob("*.py"):
            rel = str(py.relative_to(self.REPO_ROOT))
            if rel in self.ALLOWED:
                continue
            in_doc = False
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                has_triple = '"""' in line or "'''" in line
                triple_count = line.count('"""') + line.count("'''")
                if has_triple:
                    if triple_count % 2 == 1:
                        in_doc = not in_doc
                    continue
                if in_doc:
                    continue
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if self.PATTERN.search(line):
                    hits.append((py, i, line.strip()))
        assert not hits, (
            "Production code must use bpp.db.connection.get_db(), not "
            "raw sqlite3.connect(). If a new site needs a foreign-DB "
            "connection, add it to the ALLOWED list with a justification.\n"
            "Hits:\n" + "\n".join(f"  {p}:{ln} — {line}" for p, ln, line in hits)
        )
