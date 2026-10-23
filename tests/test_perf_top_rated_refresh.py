"""The Top Rated refresh reads only the candidates' CLIP embeddings and
compares each candidate against the accepted set in one vector op, not
the whole library matrix and a Python loop over every accepted photo."""

from __future__ import annotations

import numpy as np

from bpp.db.albums import get_album_photos, list_albums
from bpp.db.clip import upsert_clip_embedding
from bpp.db.connection import init_db
from bpp.db.photos import upsert_photo


def test_top_rated_loads_only_candidate_embeddings(tmp_path, monkeypatch):
    import bpp.db.clip as clip

    conn = init_db(str(tmp_path / "t.db"))
    rng = np.random.default_rng(0)
    ids = []
    for i in range(30):
        (tmp_path / f"p{i}.jpg").write_bytes(b"x")
        pid = upsert_photo(
            conn, {"filepath": str(tmp_path / f"p{i}.jpg"), "aggregate_score": i / 30}
        )
        ids.append(pid)
        v = rng.normal(size=512).astype(np.float32)
        upsert_clip_embedding(conn, pid, v / np.linalg.norm(v))
    # the two best photos are the same scene
    same = rng.normal(size=512).astype(np.float32)
    same /= np.linalg.norm(same)
    upsert_clip_embedding(conn, ids[29], same)
    upsert_clip_embedding(conn, ids[28], same)
    conn.commit()

    def _whole_library(*a, **k):
        raise AssertionError("the refresh loaded every embedding in the library")

    monkeypatch.setattr(clip, "get_all_clip_embeddings", _whole_library)
    monkeypatch.setattr(clip, "load_clip_matrix", _whole_library)

    from bpp.db.smart_album_refreshers import _refresh_score_album

    _refresh_score_album(conn)
    album = next(a for a in list_albums(conn) if a["name"] == "Top Rated")
    chosen = {p["id"] for p in get_album_photos(conn, album["id"])}
    assert ids[29] in chosen
    assert ids[28] not in chosen, "the near-identical runner-up was filtered"
    assert len(chosen) == 3
