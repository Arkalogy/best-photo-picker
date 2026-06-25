"""TDD tests for M-5: bulk_untag_photos must batch the IN clause."""

from __future__ import annotations

import sqlite3

from bpp.db.connection import init_db
from bpp.db.photos import upsert_photo
from bpp.db.tags import bulk_tag_photos, bulk_untag_photos, create_tag


def test_bulk_untag_over_999_photos(tmp_path):
    """bulk_untag_photos must handle >999 photo_ids without SQLite error."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    # Insert 1100 photos
    ids = []
    for i in range(1100):
        pid = upsert_photo(
            conn,
            {
                "filepath": f"/tmp/p{i}.jpg",
                "original_filename": f"p{i}.jpg",
                "file_size": 100,
                "file_mtime": 1000.0,
            },
        )
        ids.append(pid)

    tag_id = create_tag(conn, "test_tag")
    bulk_tag_photos(conn, ids, tag_id)

    # This must not raise OperationalError: too many SQL variables
    count = bulk_untag_photos(conn, ids, tag_id)
    assert count == 1100

    # Verify all untagged
    rows = conn.execute("SELECT COUNT(*) FROM photo_tags WHERE tag_id=?", (tag_id,)).fetchone()
    assert rows[0] == 0
