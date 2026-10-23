"""Regressions for the second whole-tree review batch: video decoding
never reaches the network, the signing key is never echoed, the
preview endpoint pages, cleared libraries release the CLIP matrix, the
nearest-centroid scan runs in blocks, the global hash pass and the
near-duplicate clustering are vectorised (and give the old answers),
duplicate pages bound their sibling lists, and the desktop shell's
overlays and capability match the page it loads."""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent

DASH = """<?xml version="1.0"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static">
 <Period><AdaptationSet mimeType="video/mp4"><Representation id="1">
  <BaseURL>http://127.0.0.1:1/marker.mp4</BaseURL>
 </Representation></AdaptationSet></Period></MPD>"""


def test_a_manifest_with_a_video_extension_never_reaches_the_decoder(tmp_path, monkeypatch):
    from bpp.utils import video

    fake = tmp_path / "holiday.mp4"
    fake.write_text(DASH)
    opened: list[str] = []

    class _Cap:
        def __init__(self, *a, **k):
            opened.append(str(a[0]))

        def isOpened(self):
            return False

        def release(self):
            pass

    monkeypatch.setattr(video.cv2, "VideoCapture", _Cap)
    assert video.extract_video_metadata(str(fake)) is None
    assert video.extract_video_frame(str(fake)) is None
    assert opened == [], "the decoder was handed a playlist"


def test_a_real_container_header_is_still_decoded(tmp_path, monkeypatch):
    from bpp.utils import video

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + os.urandom(64))
    opened: list[str] = []

    class _Cap:
        def __init__(self, *a, **k):
            opened.append(str(a[0]))

        def isOpened(self):
            return False

        def release(self):
            pass

    monkeypatch.setattr(video.cv2, "VideoCapture", _Cap)
    assert video.open_capture(str(clip)) is None
    assert opened == [str(clip)]


def test_the_registry_publisher_reads_the_key_without_echo():
    src = (ROOT / "scripts" / "publish_registry_manifest.py").read_text()
    assert "getpass.getpass(" in src
    assert 'input(\n            "Paste the private key' not in src


def test_the_preview_endpoint_pages(tmp_path):
    from bpp.db.photos import upsert_photo
    from bpp.web.app import create_app
    from bpp.web.bp_photos import PREVIEW_PAGE_MAX

    lib = tmp_path / "lib"
    (lib / "photos").mkdir(parents=True)
    app = create_app(workdir=str(lib / "data"), library_path=str(lib))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    with app.app_context():
        conn = ctx.get_conn()
        for i in range(PREVIEW_PAGE_MAX + 20):
            upsert_photo(
                conn,
                {"filepath": str(lib / "photos" / f"{i}.jpg"), "file_size": 1, "file_mtime": 1},
            )
        conn.execute("UPDATE photos SET missing = 0")
        conn.commit()
    body = app.test_client().get("/api/v1/photos/preview").get_json()
    assert body["count"] == PREVIEW_PAGE_MAX and body["total"] == PREVIEW_PAGE_MAX + 20
    page = app.test_client().get("/api/v1/photos/preview?limit=10&offset=15").get_json()
    assert page["count"] == 10 and page["offset"] == 15
    assert (
        app.test_client().get("/api/v1/photos/preview?limit=10000").get_json()["count"]
        == PREVIEW_PAGE_MAX
    )


def test_clearing_the_library_releases_the_clip_matrix(tmp_path):
    import gc
    import weakref

    from bpp.web.app import create_app

    lib = tmp_path / "lib"
    (lib / "photos").mkdir(parents=True)
    app = create_app(workdir=str(lib / "data"), library_path=str(lib))
    app.config["TESTING"] = True
    ctx = app.extensions["bpp"]
    matrix = np.zeros((100, 8), np.float32)
    ref = weakref.ref(matrix)
    ctx.caches.clip_cache = {"embeddings": {}, "ready": True, "matrix": matrix, "matrix_ids": [1]}
    del matrix
    assert (
        app.test_client().delete("/api/v1/library", json={"confirmation": "delete"}).status_code
        == 200
    )
    gc.collect()
    assert ref() is None, "the cleared library's CLIP matrix is still referenced"


def test_nearest_centroid_scan_in_blocks_matches_the_dense_answer(monkeypatch):
    from bpp.web import bp_faces_cluster_ops as ops

    rng = np.random.default_rng(5)
    centroids = {cid: rng.normal(size=16).astype(np.float32) for cid in range(1, 41)}
    embs = np.stack(
        [centroids[rng.integers(1, 41)] + rng.normal(scale=0.05, size=16) for _ in range(300)]
    )
    embs = embs.astype(np.float32)
    face_ids = list(range(1000, 1300))
    hard = {1: {2}, 7: {8, 9}}
    dense = ops._nearest_cluster_updates(face_ids, embs, centroids, hard, threshold=0.6)
    monkeypatch.setattr(ops, "NEAREST_BLOCK_BYTES", 40 * 8 * 7)  # seven faces per block
    blocked = ops._nearest_cluster_updates(face_ids, embs, centroids, hard, threshold=0.6)
    assert blocked == dense


def _old_global_pass(items, threshold):
    """The reference: the Python loop the vectorised pass replaced."""
    from bpp.dedupe.phash import dual_hash_distance, hamming_distance

    reps = []
    for it in items:
        if it.get("phash") is None:
            continue
        matched = False
        for rep in reps:
            if it.get("ahash") is not None and rep.get("ahash") is not None:
                d = dual_hash_distance(it["phash"], it["ahash"], rep["phash"], rep["ahash"])
            else:
                d = hamming_distance(it["phash"], rep["phash"])
            if d <= threshold:
                matched = True
                break
        if not matched:
            reps.append(it)
    return [r["filepath"] for r in reps]


def test_the_vectorised_global_hash_pass_keeps_the_old_answer():
    """Same representatives as the loop it replaced, hashes signed as
    SQLite hands them back (one month, so month protection is moot)."""
    from bpp.dedupe.cluster import _global_dedup

    rnd = random.Random(11)
    base = [rnd.getrandbits(64) for _ in range(40)]
    items = []
    for i in range(400):
        ph = base[i % 40] ^ sum(1 << rnd.randrange(64) for _ in range(rnd.randrange(0, 14)))
        ph &= (1 << 64) - 1
        ph = ph - (1 << 64) if ph >= (1 << 63) else ph
        ah = (base[i % 40] ^ 0xF0F0) if rnd.random() < 0.7 else None
        if ah is not None and ah >= (1 << 63):
            ah -= 1 << 64
        items.append(
            {
                "filepath": f"/p{i}.jpg",
                "phash": ph,
                "ahash": ah,
                "aggregate_score": rnd.random(),
                "date": f"2024-05-{(i % 28) + 1:02d}T10:00:00",
                "date_month": "2024-05",
            }
        )
    assert any(it["phash"] < 0 for it in items)
    got = [r["filepath"] for r in _global_dedup(items, 8)]
    ordered = sorted(items, key=lambda d: -d["aggregate_score"])
    assert got == _old_global_pass(ordered, 8)


def test_near_duplicate_clustering_is_fast_and_right_for_undated_rows(tmp_path):
    import time

    from bpp.db.connection import init_db
    from bpp.db.dedupe import assign_near_duplicate_clusters
    from bpp.db.photos import upsert_photo

    db = str(tmp_path / "t.db")
    conn = init_db(db)
    rnd = random.Random(2)
    seed = rnd.getrandbits(63)
    for i in range(2500):
        ph = (
            seed
            if i % 50 == 0
            else (seed ^ sum(1 << rnd.randrange(64) for _ in range(20))) & ((1 << 64) - 1)
        )
        ph = ph - (1 << 64) if ph >= (1 << 63) else ph  # SQLite stores signed 64-bit
        upsert_photo(
            conn,
            {
                "filepath": f"/u/{i}.jpg",
                "file_size": 1,
                "file_mtime": 1,
                "phash": ph,
                "ahash": None,
                "date": None,
            },
        )
    conn.execute("UPDATE photos SET missing = 0")
    conn.commit()
    t0 = time.monotonic()
    assign_near_duplicate_clusters(conn, hamming_threshold=8, time_window_seconds=60)
    assert time.monotonic() - t0 < 5.0, "undated rows must not be a Python-level quadratic"
    sizes = conn.execute("SELECT cluster_size FROM photos WHERE phash = ?", (seed,)).fetchall()
    assert sizes and all(s[0] == 50 for s in sizes)


def test_duplicate_page_rows_carry_a_bounded_sibling_list(tmp_path):
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo
    from bpp.web.album_siblings import SIBLINGS_PER_ROW, attach_cluster_siblings

    db = str(tmp_path / "t.db")
    conn = init_db(db)
    n = SIBLINGS_PER_ROW * 3
    for i in range(n):
        upsert_photo(conn, {"filepath": f"/c/{i}.jpg", "file_size": 1, "file_mtime": 1})
    conn.execute("UPDATE photos SET dup_cluster_id = 7, cluster_size = ?, missing = 0", (n,))
    conn.commit()
    rows = [{"filepath": f"/c/{i}.jpg", "dup_cluster_id": 7} for i in range(5)]
    photos = [dict(r) for r in rows]
    attach_cluster_siblings(conn, None, photos, rows)
    assert all(len(p["similar_photos"]) == SIBLINGS_PER_ROW for p in photos)
    assert all(p["cluster_size"] == n for p in photos)


def test_the_dev_launcher_starts_an_open_server():
    script = (ROOT / "desktop" / "scripts" / "dev-macos.sh").read_text()
    assert "--open-loopback" in script


def test_the_api_doc_matches_the_clip_cap_override_endpoint():
    doc = (ROOT / "docs" / "API.md").read_text()
    assert '{"enable": true}' in doc and '{"value": "bypass"}' not in doc


def test_the_shell_overlays_use_listeners_and_the_page_origin_may_invoke():
    screens = (ROOT / "desktop" / "src-tauri" / "src" / "screens.rs").read_text()
    overlays = screens.split("fn show_update_required")[0]
    assert "onclick=" not in overlays, "inline handlers are blocked by the page's CSP"
    assert "addEventListener('click'" in overlays
    cap = json.loads((ROOT / "desktop" / "src-tauri" / "capabilities" / "default.json").read_text())
    assert cap["remote"]["urls"] == ["http://127.0.0.1:5001/*"]
    for cmd in ("set-app-theme", "exit-app", "restart-app", "open-url"):
        assert f"allow-{cmd}" in cap["permissions"]
    build = (ROOT / "desktop" / "src-tauri" / "build.rs").read_text()
    assert 'commands(&["set_app_theme", "exit_app", "restart_app", "open_url"])' in build
