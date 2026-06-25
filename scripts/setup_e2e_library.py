#!/usr/bin/env python3
"""Set up a tiny synthetic library suitable for running the e2e suite.

Generates ~12 procedural photos (offline, no network), imports them into
the standard library layout, and runs `bpp analyze` so /api/photos is
populated. Idempotent — safe to re-run.

Usage:
    python scripts/setup_e2e_library.py [--library DIR] [--count N]

After this completes, start the server pointing at the library:
    bpp serve --library <DIR> --no-browser

Then run:
    npx playwright test
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--library",
        type=Path,
        default=Path(tempfile.gettempdir()) / "bpp_e2e_library",
        help="Library directory to create/seed.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=12,
        help="How many synthetic photos to generate.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Wipe the library directory before seeding.",
    )
    args = parser.parse_args()

    library = args.library.expanduser().resolve()

    if args.reset and library.exists():
        print(f"  Removing existing library at {library}")
        shutil.rmtree(library)

    library.mkdir(parents=True, exist_ok=True)
    # Sentinel — server publishes its presence at /api/v1/_diag/is_e2e_fixture
    # so the playwright mutation helpers can refuse to run against a real
    # user library (which is how 5 __e2e_album_* rows ended up in the demo
    # library before this guard existed).
    (library / ".bpp-e2e-fixture").write_text(
        "This file marks the directory as a synthetic e2e test library.\n"
        "Created by scripts/setup_e2e_library.py.\n"
    )
    print(f"  Library: {library}")

    # 1. Generate sample photos
    from bpp.demo.generate import generate_sample_photos

    staging = library / "_e2e_staging"
    if staging.exists():
        shutil.rmtree(staging)
    paths = generate_sample_photos(str(staging), count=args.count)
    print(f"  Generated {len(paths)} sample photos")

    # 2. Initialize DB and import via the standard pipeline.
    # bpp serve resolves the DB at <library>/data/photopicker.db
    # (see bpp/db/library.py:get_library_dirs); use the same path so
    # downstream writes (including the fixture seed below) land in the
    # DB the server actually reads.
    from bpp.db.connection import close_all_connections, get_db, init_db
    from bpp.db.library import ensure_library_dirs, import_folder

    dirs = ensure_library_dirs(str(library))
    db_path = Path(dirs["data"]) / "photopicker.db"
    init_db(str(db_path))
    conn = get_db(str(db_path))
    result = import_folder(conn, str(staging), str(library), batch_name="e2e_demo")
    print(
        f"  Imported {result.imported} photos into {library}/photos/e2e_demo "
        f"(skipped {result.skipped})"
    )
    close_all_connections()

    shutil.rmtree(staging, ignore_errors=True)

    # 3. Run analyze so /api/photos returns data
    photos_dir = library / "photos" / "e2e_demo"
    print(f"  Analyzing {photos_dir} ...")
    import argparse as _ap

    from bpp.commands import do_analyze

    # CLI default for --extensions is DEFAULTS["scan_extensions"]; we hard-code
    # the same source-of-truth so this script doesn't depend on the parent
    # parser's defaults wiring.
    from bpp.config import DEFAULTS

    ns = _ap.Namespace(
        input=str(photos_dir),
        out=str(library),
        config=None,
        max=0,
        workers=1,
        debug=False,
        dry_run=False,
        extensions=DEFAULTS["scan_extensions"],
        # do_analyze passes args.seed through to compute_aggregate;
        # mirror the CLI default at bpp/cli.py:17 (--seed defaults to 42).
        seed=42,
    )
    rc = do_analyze(ns)
    if rc != 0:
        print(f"  analyze failed with rc={rc}", file=sys.stderr)
        return rc

    # 4. Seed fixture data the procedural-photo pipeline can't produce:
    #    face clusters, favorites, hidden / deleted photos. The e2e
    #    suite gates several specs on these features, and the synthetic
    #    photos don't trip SCRFD / NudeNet / the pet detector, so we
    #    inject the rows directly. Skipped silently if the schema
    #    doesn't have a target column (e.g. running on an older DB).
    print("  Seeding face clusters + favorites + hidden + deleted ...")
    _seed_e2e_fixture_data(library, db_path)

    print(f"  ✓ Library ready: {library}")
    print("\nStart the server with:")
    print(f"  bpp serve --library {library} --no-browser")
    return 0


def _seed_e2e_fixture_data(library: Path, db_path: Path) -> None:
    """Inject face / favorite / hidden / deleted data the synthetic photos can't produce.

    The procedural photos in bpp.demo.generate look like abstract art, so:
      * SCRFD finds no real faces — face_embeddings stays empty,
        People nav doesn't render, smart_person albums don't exist.
      * No GPS, no detected pets, no marked favorites.

    For end-to-end coverage we hand-craft enough rows to make the
    feature-gated test paths exercise their UI:

      * 6 face_embeddings on 5 photos, clustered into "Alice" and "Bob".
        Photo 5 holds both clusters so groups (co-occurrence) renders.
      * 2 photos marked favorite.
      * 1 photo hidden.
      * 1 photo soft-deleted.

    All inserts are idempotent — re-running the setup script with
    --reset wipes the library first; without --reset, the writes
    overwrite/skip cleanly.
    """
    import sqlite3 as _sql

    import numpy as np

    from bpp.constants import FACE_CLUSTER_THRESHOLD_FALLBACK  # noqa: F401 (read in caller plans)
    from bpp.db.connection import close_all_connections, get_db
    from bpp.db.smart_albums import refresh_smart_albums
    from bpp.scoring.face_embed import SFACE_DISTANCE_SCALE

    conn = get_db(str(db_path))
    conn.row_factory = _sql.Row

    photos = conn.execute(
        "SELECT id, filepath FROM photos WHERE deleted_at IS NULL ORDER BY id LIMIT 5"
    ).fetchall()
    if len(photos) < 5:
        print(f"    skipping fixture seed — only {len(photos)} photos (need 5)")
        return
    photo_ids = [r["id"] for r in photos]

    # ── Face clusters ──
    # Two centroid vectors well outside the FACE_CLUSTER_THRESHOLD so the
    # post-hoc clustering pass doesn't merge them. Each face = centroid +
    # small noise + L2-normalize + scale (matches face_embed.py:509-512).
    rng = np.random.default_rng(seed=42)

    def _make_centroid() -> np.ndarray:
        v = rng.standard_normal(128).astype(np.float32)
        return v / np.linalg.norm(v)

    def _face_from(centroid: np.ndarray, noise_scale: float = 0.05) -> np.ndarray:
        v = centroid + noise_scale * rng.standard_normal(128).astype(np.float32)
        v = v / np.linalg.norm(v)
        return (v * SFACE_DISTANCE_SCALE).astype(np.float32)

    alice_centroid = _make_centroid()
    bob_centroid = _make_centroid()

    # Layout: photos[0..2] → Alice; photos[3] → Bob; photos[4] → Alice + Bob.
    # Cluster IDs 0 and 1 so identity strings line up trivially with the
    # smart_person album rendering in renderAlbumNav.
    face_rows = [
        # (photo_id, face_index, bbox, embedding, cluster_id, quality, identity)
        (photo_ids[0], 0, (100, 80, 120, 150), _face_from(alice_centroid), 0, 0.95, "Alice"),
        (photo_ids[1], 0, (110, 90, 130, 160), _face_from(alice_centroid), 0, 0.92, "Alice"),
        (photo_ids[2], 0, (90, 70, 110, 140), _face_from(alice_centroid), 0, 0.94, "Alice"),
        (photo_ids[3], 0, (200, 100, 130, 160), _face_from(bob_centroid), 1, 0.90, "Bob"),
        (photo_ids[4], 0, (150, 100, 120, 150), _face_from(alice_centroid), 0, 0.91, "Alice"),
        (photo_ids[4], 1, (350, 100, 120, 150), _face_from(bob_centroid), 1, 0.89, "Bob"),
    ]

    # Wipe any prior face data so this is idempotent on re-runs without
    # --reset (the table would otherwise grow on every invocation).
    conn.execute("DELETE FROM face_embeddings")
    for pid, fi, (bx, by, bw, bh), emb, cid, q, ident in face_rows:
        conn.execute(
            "INSERT INTO face_embeddings "
            "(photo_id, face_index, bbox_x, bbox_y, bbox_w, bbox_h, "
            " embedding, cluster_id, quality, identity, user_confirmed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (pid, fi, bx, by, bw, bh, emb.tobytes(), cid, q, ident),
        )

    # Update photos.face_count to match — used by /api/v1/photos
    # consumers (incl. tests 10 / 30 finding a multi-face photo).
    face_counts: dict[int, int] = {}
    for row in face_rows:
        face_counts[row[0]] = face_counts.get(row[0], 0) + 1
    for pid, n in face_counts.items():
        conn.execute("UPDATE photos SET face_count=? WHERE id=?", (n, pid))

    # ── Favorites ──
    # favorite lives on album_photos (per-album). Set it on the
    # all-photos album rows for the first 2 photos.
    all_album = conn.execute("SELECT id FROM albums WHERE album_type='all'").fetchone()
    if all_album:
        for pid in photo_ids[:2]:
            conn.execute(
                "UPDATE album_photos SET favorite=1 WHERE album_id=? AND photo_id=?",
                (all_album["id"], pid),
            )

    # ── Hidden + Deleted ──
    # Use photos[5..] if available so we don't conflict with the face
    # photos. Fall back to higher indices when count >= 7.
    extras = conn.execute(
        "SELECT id FROM photos WHERE deleted_at IS NULL ORDER BY id LIMIT 7 OFFSET 5"
    ).fetchall()
    if len(extras) >= 1:
        conn.execute(
            "UPDATE photos SET hidden_at=datetime('now') WHERE id=?",
            (extras[0]["id"],),
        )
    if len(extras) >= 2:
        conn.execute(
            "UPDATE photos SET deleted_at=datetime('now') WHERE id=?",
            (extras[1]["id"],),
        )

    conn.commit()

    # Smart albums (incl. smart_person) regenerate from face_embeddings
    # state — without this, the People nav still wouldn't render even
    # with face rows inserted.
    refresh_smart_albums(conn)
    conn.commit()

    print(
        f"    seeded {len(face_rows)} face_embeddings ({len(face_counts)} photos), "
        f"{min(2, len(photo_ids))} favorites, "
        f"{min(1, len(extras))} hidden, {min(1, max(0, len(extras) - 1))} deleted"
    )
    close_all_connections()


if __name__ == "__main__":
    sys.exit(main())
