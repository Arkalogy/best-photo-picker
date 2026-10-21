"""Re-running Analyze writes scores; it must not blank the provenance
the import wrote (sha256, import batch, original filename), which the
scoring rows never carry."""

from __future__ import annotations

from bpp.db.connection import init_db
from bpp.db.photos import bulk_upsert_photos, get_photo_by_path, upsert_photo


def test_scoring_rows_without_provenance_keep_the_stored_values(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    fp = str(tmp_path / "photos" / "batch" / "IMG_1.jpg")
    upsert_photo(
        conn,
        {
            "filepath": fp,
            "sha256": "a" * 64,
            "import_batch": "2024-06 trip",
            "original_filename": "DSC_0001.JPG",
        },
    )
    bulk_upsert_photos(conn, [{"filepath": fp, "date": "2024-06-01T10:00:00", "blur_score": 0.5}])
    row = get_photo_by_path(conn, fp)
    assert row["sha256"] == "a" * 64
    assert row["import_batch"] == "2024-06 trip"
    assert row["original_filename"] == "DSC_0001.JPG"
    assert row["blur_score"] == 0.5


def test_original_filename_is_never_rewritten_by_an_upsert(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    fp = str(tmp_path / "IMG_1.jpg")
    upsert_photo(conn, {"filepath": fp, "original_filename": "DSC_0001.JPG"})
    bulk_upsert_photos(conn, [{"filepath": fp, "original_filename": "IMG_1.jpg"}])
    assert get_photo_by_path(conn, fp)["original_filename"] == "DSC_0001.JPG"


def test_provenance_given_explicitly_still_updates(tmp_path):
    conn = init_db(str(tmp_path / "t.db"))
    fp = str(tmp_path / "IMG_1.jpg")
    upsert_photo(conn, {"filepath": fp, "sha256": "a" * 64, "import_batch": "old"})
    bulk_upsert_photos(conn, [{"filepath": fp, "sha256": "b" * 64, "import_batch": "new"}])
    row = get_photo_by_path(conn, fp)
    assert row["sha256"] == "b" * 64
    assert row["import_batch"] == "new"
