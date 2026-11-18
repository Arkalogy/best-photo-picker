"""A content hash read from the library database is only ever used as a
cache file name when it is a well-formed hash.

The ``sha256`` column travels with the library folder. A value such as
an absolute path, joined to the cache folder, names a file anywhere on
the disk; the cache sweep would then delete it and object removal would
write to it.
"""

from __future__ import annotations

import os

import pytest

from bpp.constants import PHOTO_CACHE_SUFFIX_FULL, PHOTO_CACHE_SUFFIX_INPAINTED
from bpp.db.photos_sha import content_cache_key, get_sha256_map
from bpp.web.photo_cache import cached_variant_paths
from bpp.web.thumbnails import ThumbnailCache

GOOD_SHA = "0123456789abcdef" * 4
GOOD_KEY = GOOD_SHA[:32]


@pytest.mark.parametrize(
    "stored",
    [
        "/tmp/owner/holiday",
        "../../owner/holiday",
        "0123456789ABCDEF" * 4,
        GOOD_SHA[:63],
        GOOD_SHA + "/x",
        GOOD_KEY + "/../../../../owner_pictures/holiday" + "0" * 20,
        "",
        None,
        12,
    ],
)
def test_a_stored_value_that_is_not_a_sha256_gives_no_cache_key(stored):
    assert content_cache_key(stored) is None


def test_a_real_sha256_gives_its_prefix():
    assert content_cache_key(GOOD_SHA) == GOOD_KEY


def test_variant_paths_skip_hashes_that_are_not_cache_names(tmp_path):
    cache = str(tmp_path / "cache")
    bad = {str(tmp_path / "owner" / "holiday"), "../holiday", GOOD_KEY.upper(), GOOD_KEY[:31]}

    assert cached_variant_paths(cache, bad) == []
    paths = cached_variant_paths(cache, bad | {GOOD_KEY})
    assert paths, "a well-formed hash lost its variants"
    assert all(os.path.dirname(p) == cache for p in paths), paths


def test_the_cache_sweep_leaves_a_file_named_by_a_crafted_hash(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    owner = tmp_path / "owner"
    owner.mkdir()
    victim = owner / f"holiday{PHOTO_CACHE_SUFFIX_FULL}.jpg"
    victim.write_bytes(b"owner file")
    inpainted = cache_dir / f"{GOOD_KEY}{PHOTO_CACHE_SUFFIX_INPAINTED}.png"
    inpainted.write_bytes(b"cached")
    thumbs = ThumbnailCache(str(cache_dir))

    thumbs.remove_for_hash("f" * 32, content_hash=str(owner / "holiday"))
    assert victim.exists(), "a file outside the cache folder was deleted"

    thumbs.remove_for_hash("f" * 32, content_hash=GOOD_KEY)
    assert not inpainted.exists(), "a real content hash no longer sweeps its variants"


def test_the_hash_map_drops_values_that_are_not_sha256(tmp_path):
    from bpp.db.connection import init_db

    conn = init_db(str(tmp_path / "t.db"))
    rows = [("/p/good.jpg", GOOD_SHA), ("/p/abs.jpg", "/tmp/owner/holiday"), ("/p/none.jpg", None)]
    for fp, sha in rows:
        conn.execute(
            "INSERT INTO photos (filepath, sha256, original_filename, file_size, file_mtime) "
            "VALUES (?, ?, 'x.jpg', 1, 0.0)",
            (fp, sha),
        )
    conn.commit()

    assert get_sha256_map(conn, [r[0] for r in rows]) == {"/p/good.jpg": GOOD_KEY}
    conn.close()
