"""TDD: get_photos_page() returns the correct DB-level LIMIT/OFFSET slice."""

from __future__ import annotations


def _make_conn():
    from bpp.db.connection import get_db
    from bpp.db.photos import bulk_upsert_photos
    from bpp.db.schema import create_tables

    conn = get_db(":memory:")
    create_tables(conn)
    bulk_upsert_photos(
        conn,
        [
            {
                "filepath": f"/lib/photo_{i:02d}.jpg",
                "original_filename": f"photo_{i:02d}.jpg",
                "file_size": 1024,
                "file_mtime": 1700000000.0 + i,
                "date": f"2024-01-{i + 1:02d}",
                "aggregate_score": float(i),
            }
            for i in range(10)
        ],
    )
    return conn


class TestGetPhotosPage:
    def test_returns_correct_page(self):
        from bpp.db.photos import get_photos_page

        conn = _make_conn()
        page = get_photos_page(conn, limit=3, offset=0)
        assert len(page) == 3
        page2 = get_photos_page(conn, limit=3, offset=3)
        assert len(page2) == 3
        # No overlap
        ids_1 = {r["id"] for r in page}
        ids_2 = {r["id"] for r in page2}
        assert ids_1.isdisjoint(ids_2)

    def test_last_page_returns_remainder(self):
        from bpp.db.photos import get_photos_page

        conn = _make_conn()
        page = get_photos_page(conn, limit=3, offset=9)
        assert len(page) == 1

    def test_offset_beyond_end_returns_empty(self):
        from bpp.db.photos import get_photos_page

        conn = _make_conn()
        page = get_photos_page(conn, limit=5, offset=100)
        assert page == []

    def test_excludes_deleted_by_default(self):
        from bpp.db.photos import get_photos_page

        conn = _make_conn()
        conn.execute("UPDATE photos SET deleted_at='2024-01-01' WHERE id=1")
        conn.commit()
        page = get_photos_page(conn, limit=100, offset=0)
        ids = [r["id"] for r in page]
        assert 1 not in ids

    def test_include_deleted_flag(self):
        from bpp.db.photos import get_photos_page

        conn = _make_conn()
        conn.execute("UPDATE photos SET deleted_at='2024-01-01' WHERE id=1")
        conn.commit()
        page = get_photos_page(conn, limit=100, offset=0, include_deleted=True)
        ids = [r["id"] for r in page]
        assert 1 in ids

    def test_same_order_as_get_all_photos(self):
        from bpp.db.photos import get_all_photos, get_photos_page

        conn = _make_conn()
        all_photos = get_all_photos(conn)
        page = get_photos_page(conn, limit=len(all_photos), offset=0)
        assert [r["id"] for r in page] == [r["id"] for r in all_photos]
