"""Second-pass patterns review (v0.1.3): IN batching goes through
in_batches everywhere (and the two library-sized lists are split), the
DB layer no longer reaches into bpp.web, the layering gate sees every
import form, the phase-5 backfill thread has an owner, and the sidebar
takes icon and tooltip from the server only."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BPP = REPO_ROOT / "bpp"

_HAND_ROLLED = re.compile(r"range\(0, len\([^)]*\), (SQL_BATCH_SIZE|_BATCH_SIZE|batch_size|\d+)\)")


def test_in_batching_is_not_hand_rolled():
    offenders = []
    for path in BPP.rglob("*.py"):
        if path.name == "sql_batches.py":
            continue
        for m in _HAND_ROLLED.finditer(path.read_text()):
            offenders.append(f"{path.relative_to(REPO_ROOT)}: {m.group(0)}")
    assert offenders == [], "use bpp.db.sql_batches.in_batches:\n" + "\n".join(offenders)


def test_marking_missing_photos_is_batched(tmp_path, monkeypatch):
    from bpp.db import sql_batches
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo
    from bpp.db.photos_missing import scan_missing

    monkeypatch.setattr(sql_batches, "SQL_BATCH_SIZE", 3)
    photos = tmp_path / "photos"
    photos.mkdir()
    conn = init_db(str(tmp_path / "t.db"))
    paths = []
    for i in range(40):
        p = photos / f"p{i}.jpg"
        p.write_bytes(b"x")
        upsert_photo(conn, {"filepath": str(p)})
        paths.append(p)
    conn.commit()
    for p in paths[:10]:
        p.unlink()
    captured: list[str] = []
    conn.set_trace_callback(captured.append)
    try:
        result = scan_missing(conn, photos_dir=str(photos))
    finally:
        conn.set_trace_callback(None)
    assert len(result.newly_missing) == 10
    updates = [s for s in captured if s.startswith("UPDATE photos SET missing=1")]
    assert len(updates) >= 4 and all(s.count("?") <= 3 for s in updates)


def test_the_db_layer_imports_nothing_from_bpp_web():
    offenders = []
    for path in (BPP / "db").rglob("*.py"):
        text = path.read_text()
        if re.search(r"^\s*(from bpp\.web\b|import bpp\.web\b)", text, re.MULTILINE):
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == []


def test_duplicates_album_ui_hook_is_registered_by_the_web_layer():
    from bpp.db.smart_albums import SmartAlbumRegistry
    from bpp.web import album_siblings  # noqa: F401  (registers the hook)

    assert SmartAlbumRegistry.get_ui_metadata_fn("smart_duplicates") is not None


def test_phase5_backfill_thread_is_joined_with_the_store_threads():
    src = (BPP / "web" / "analysis_store.py").read_text()
    assert "phase5_thread" in src
    joined = src[src.index("def join_threads") :]
    assert '("phase5_thread", self.phase5_thread)' in joined
    init = (BPP / "web" / "state_init.py").read_text()
    assert "phase5_thread = " in init


def test_sidebar_uses_only_the_servers_icon_and_tooltip():
    src = (BPP / "web" / "static" / "js" / "modules" / "albums-render-helpers.mjs").read_text()
    assert 'albumType === "smart_score"' not in src
    assert "Top 10% by BPP score" not in src
