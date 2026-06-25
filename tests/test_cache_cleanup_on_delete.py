"""TDD tests for M-2: permanent delete must clean up cache files."""

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


def test_remove_for_hash_catches_unregistered_suffixes(tmp_path):
    """Glob-based cleanup must catch suffixes that aren't in
    PHOTO_CACHE_SUFFIXES — that's the whole point of switching from
    iterating the registry to globbing. Pins the regression: if a
    future contributor adds a new variant suffix and forgets to
    update the registry, cleanup STILL works."""
    thumb_dir = tmp_path / "thumbs"
    thumb_dir.mkdir()
    cache = ThumbnailCache(str(thumb_dir))

    h = "abc12300"
    # The base thumb is the only suffix in PHOTO_CACHE_SUFFIXES that's
    # an empty string. Future hypothetical variants:
    (thumb_dir / f"{h}.jpg").write_bytes(b"thumb")
    (thumb_dir / f"{h}_future_variant.jpg").write_bytes(b"unknown")
    (thumb_dir / f"{h}_another_one.png").write_bytes(b"also unknown")

    removed = cache.remove_for_hash(h)

    # All three deleted via glob, even though only "" is in the registry
    assert removed == 3
    assert not (thumb_dir / f"{h}_future_variant.jpg").exists()
    assert not (thumb_dir / f"{h}_another_one.png").exists()


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
