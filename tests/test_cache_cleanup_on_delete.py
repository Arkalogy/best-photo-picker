"""TDD tests: permanent delete must clean up cache files."""

from __future__ import annotations

from bpp.web.thumbnails import ThumbnailCache


def test_remove_for_hash_cleans_thumb_and_crops(tmp_path):
    thumb_dir = tmp_path / "thumbs"
    thumb_dir.mkdir()
    face_dir = tmp_path / "face_crops"
    face_dir.mkdir()
    pet_dir = tmp_path / "pet_crops"
    pet_dir.mkdir()

    # Init cache first (writes .version), then create test files
    cache = ThumbnailCache(str(thumb_dir))

    h = "abc123"
    (thumb_dir / f"{h}.jpg").write_bytes(b"thumb")
    (thumb_dir / f"{h}_full.jpg").write_bytes(b"full")
    (thumb_dir / f"{h}_edited.jpg").write_bytes(b"edited")
    (face_dir / f"{h}_0_deadbeef.jpg").write_bytes(b"face0")
    (face_dir / f"{h}_1_cafebabe.jpg").write_bytes(b"face1")
    (pet_dir / f"pet_{h}_0_12345678.jpg").write_bytes(b"pet0")

    removed = cache.remove_for_hash(h, str(face_dir), str(pet_dir))

    assert removed == 6
    assert not (thumb_dir / f"{h}.jpg").exists()
    assert not (thumb_dir / f"{h}_full.jpg").exists()
    assert not (thumb_dir / f"{h}_edited.jpg").exists()
    assert not (face_dir / f"{h}_0_deadbeef.jpg").exists()
    assert not (face_dir / f"{h}_1_cafebabe.jpg").exists()
    assert not (pet_dir / f"pet_{h}_0_12345678.jpg").exists()


def test_remove_for_hash_no_files_is_noop(tmp_path):
    thumb_dir = tmp_path / "thumbs"
    thumb_dir.mkdir()
    cache = ThumbnailCache(str(thumb_dir))
    removed = cache.remove_for_hash("nonexistent")
    assert removed == 0


def test_remove_for_hash_cleans_content_hash_inpaint(tmp_path):
    """Inpaint cache files keyed by content SHA-256 are cleaned via content_hash."""
    thumb_dir = tmp_path / "thumbs"
    thumb_dir.mkdir()
    cache = ThumbnailCache(str(thumb_dir))

    path_hash = "aaa111"
    content_hash = "bbb222"

    # path-hash-keyed variants
    (thumb_dir / f"{path_hash}.jpg").write_bytes(b"thumb")
    (thumb_dir / f"{path_hash}_full.jpg").write_bytes(b"full")
    # content-hash-keyed inpaint (new scheme)
    (thumb_dir / f"{content_hash}_inpainted.png").write_bytes(b"inpaint")
    # old path-hash-keyed inpaint (backward compat)
    (thumb_dir / f"{path_hash}_inpainted.png").write_bytes(b"old_inpaint")

    removed = cache.remove_for_hash(path_hash, content_hash=content_hash)

    assert removed == 4
    assert not (thumb_dir / f"{path_hash}.jpg").exists()
    assert not (thumb_dir / f"{path_hash}_full.jpg").exists()
    assert not (thumb_dir / f"{content_hash}_inpainted.png").exists()
    assert not (thumb_dir / f"{path_hash}_inpainted.png").exists()


def test_remove_for_hash_sweeps_every_registered_suffix_in_both_formats(tmp_path):
    """Cleanup removes exactly the registered variants (no directory
    listing — the cache folder is swept per photo on every edit save and
    delete). A suffix missing from PHOTO_CACHE_SUFFIXES would leak, which
    tests/test_cache_suffix_registry.py guards against."""
    from bpp.constants import PHOTO_CACHE_SUFFIXES

    thumb_dir = tmp_path / "thumbs"
    thumb_dir.mkdir()
    cache = ThumbnailCache(str(thumb_dir))

    h = "abc12300"
    names = [f"{h}{suffix}{ext}" for suffix in PHOTO_CACHE_SUFFIXES for ext in (".jpg", ".png")]
    for n in names:
        (thumb_dir / n).write_bytes(b"x")

    removed = cache.remove_for_hash(h)

    assert removed == len(names)
    assert not any((thumb_dir / n).exists() for n in names)


def test_remove_for_hash_does_not_touch_other_hashes(tmp_path):
    """Glob is anchored on the full hash so neighbors don't get
    caught. 32-char hashes make collision effectively impossible
    but verify defensively."""
    thumb_dir = tmp_path / "thumbs"
    thumb_dir.mkdir()
    cache = ThumbnailCache(str(thumb_dir))

    target = "a1b2c3d4e500"
    other = "a1b2c3d4e600"  # similar but different
    (thumb_dir / f"{target}.jpg").write_bytes(b"target")
    (thumb_dir / f"{other}.jpg").write_bytes(b"other")

    cache.remove_for_hash(target)

    assert not (thumb_dir / f"{target}.jpg").exists()
    assert (thumb_dir / f"{other}.jpg").exists()
