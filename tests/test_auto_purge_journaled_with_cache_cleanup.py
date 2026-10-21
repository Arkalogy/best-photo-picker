"""The 30-day auto purge is the same permanent delete the owner can run:
journaled (a crash mid-way is replayed at the next start) and it sweeps
the purged photos' cached variants."""

from __future__ import annotations

import os

from PIL import Image


def test_auto_purge_removes_cache_variants_and_journals(tmp_path, monkeypatch):
    from bpp.web.app import create_app
    from bpp.web.state import get_ctx
    from bpp.web.state_ops import auto_purge

    app = create_app(library_path=str(tmp_path / "lib"))
    app.config["TESTING"] = True
    from bpp.db.photos import upsert_photo

    with app.app_context():
        ctx = get_ctx()
        d = os.path.join(ctx.state["library_path"], "photos")
        os.makedirs(d, exist_ok=True)
        fp = os.path.join(d, "old.jpg")
        Image.new("RGB", (8, 8)).save(fp, "JPEG")
        conn = ctx.get_conn()
        pid = upsert_photo(conn, {"filepath": fp})
        conn.execute("UPDATE photos SET deleted_at=datetime('now', '-40 days') WHERE id=?", (pid,))
        conn.commit()
        ctx.thumbs.build_map_from_paths([fp])
        h = ctx.thumbs.get_hash(fp)
        variant = os.path.join(ctx.thumbs.cache_dir, f"{h}_full.jpg")
        with open(variant, "wb") as f:
            f.write(b"x")
        started: list = []
        import bpp.db.journal as journal

        real_start = journal.journal_start
        monkeypatch.setattr(
            journal, "journal_start", lambda *a, **k: started.append(1) or real_start(*a, **k)
        )
        auto_purge(ctx)
        assert not os.path.exists(fp)
        assert not os.path.exists(variant), "cached variant left behind"
        assert started == [1], "the purge was not journaled"
        assert conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 0
