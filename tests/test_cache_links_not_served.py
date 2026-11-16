"""A link under a cache name is never read, sent or written through.

The cache folder is part of the library folder. An entry the app did
not write (a link to one of the owner's files, under the name of a
photo's thumbnail) must not be served to a paired device; it is dropped
and the real derivative is generated in its place.
"""

# Shared fixtures are imported by name and re-bound as test parameters.
# ruff: noqa: F811

from __future__ import annotations

import os

import pytest

from bpp.constants import (
    PHOTO_CACHE_SUFFIX_EDITED_THUMB,
    PHOTO_CACHE_SUFFIX_FULL,
    PHOTO_CACHE_SUFFIX_SPRITE,
)
from bpp.web.photo_cache import own_cache_file
from tests._media_state_shared import _create_test_image, app_with_thumbs  # noqa: F401

SECRET = b"owner file, not a thumbnail"


@pytest.fixture
def secret(tmp_path):
    path = tmp_path / "owner_secret.jpg"
    path.write_bytes(SECRET)
    return path


def test_a_link_is_not_a_cache_file_and_is_dropped(tmp_path, secret):
    entry = tmp_path / "abc.jpg"
    entry.symlink_to(secret)

    assert own_cache_file(str(entry)) is False

    assert not entry.is_symlink() and not entry.exists()
    assert secret.read_bytes() == SECRET


def test_a_regular_file_is_a_cache_file_and_a_missing_one_is_not(tmp_path):
    entry = tmp_path / "abc.jpg"
    assert own_cache_file(str(entry)) is False
    entry.write_bytes(b"jpeg")
    assert own_cache_file(str(entry)) is True
    assert own_cache_file(str(tmp_path)) is False


def _ctx_hash(app, filepath):
    from bpp.web.state import get_ctx

    with app.app_context():
        ctx = get_ctx()
        return ctx, ctx.thumbs.get_hash(filepath)


@pytest.mark.parametrize(
    ("suffix", "url"),
    [
        ("", "/thumb/{h}"),
        (PHOTO_CACHE_SUFFIX_EDITED_THUMB, "/thumb/{h}"),
        (PHOTO_CACHE_SUFFIX_FULL, "/photo/{h}"),
    ],
)
def test_a_planted_link_is_not_served_and_the_real_image_replaces_it(
    app_with_thumbs, secret, suffix, url
):
    app, filepath = app_with_thumbs
    ctx, h = _ctx_hash(app, filepath)
    planted = os.path.join(ctx.thumbs.cache_dir, f"{h}{suffix}.jpg")
    os.makedirs(ctx.thumbs.cache_dir, exist_ok=True)
    os.symlink(secret, planted)
    os.utime(secret, (4102444800, 4102444800))  # newer than the photo: looks fresh

    r = app.test_client().get(url.format(h=h))

    assert r.data != SECRET, "an owner file was served through a cache link"
    assert r.status_code == 200
    assert r.data[:2] == b"\xff\xd8", "the real image was not generated"
    assert secret.read_bytes() == SECRET
    assert not os.path.islink(planted)


def test_a_planted_sprite_link_is_not_served(app_with_thumbs, secret):
    app, filepath = app_with_thumbs
    ctx, h = _ctx_hash(app, filepath)
    planted = os.path.join(ctx.thumbs.cache_dir, f"{h}{PHOTO_CACHE_SUFFIX_SPRITE}.jpg")
    os.makedirs(ctx.thumbs.cache_dir, exist_ok=True)
    os.symlink(secret, planted)

    r = app.test_client().get(f"/api/v1/video/preview/{h}")

    assert r.data != SECRET
    assert secret.read_bytes() == SECRET


def test_the_cache_warmer_does_not_count_a_link_as_a_thumbnail(app_with_thumbs, secret):
    app, filepath = app_with_thumbs
    ctx, h = _ctx_hash(app, filepath)
    planted = os.path.join(ctx.thumbs.cache_dir, f"{h}.jpg")
    os.makedirs(ctx.thumbs.cache_dir, exist_ok=True)
    os.symlink(secret, planted)
    ctx.thumbs._verified.discard(h)

    ctx.thumbs.warm_cache()

    assert not os.path.islink(planted)
    assert secret.read_bytes() == SECRET


def test_a_missing_cache_file_says_what_to_check(tmp_path):
    from bpp.errors import NotFoundError
    from bpp.web.photo_cache import send_cache_file

    with pytest.raises(NotFoundError) as caught:
        send_cache_file(str(tmp_path / "gone.jpg"), mimetype="image/jpeg")

    assert "recovery" in caught.value.context, caught.value.context


def test_a_link_put_under_a_thumbnail_already_served_is_replaced(app_with_thumbs, secret):
    app, filepath = app_with_thumbs
    ctx, h = _ctx_hash(app, filepath)
    client = app.test_client()
    assert client.get(f"/thumb/{h}").status_code == 200
    planted = os.path.join(ctx.thumbs.cache_dir, f"{h}.jpg")
    os.remove(planted)
    os.symlink(secret, planted)

    r = client.get(f"/thumb/{h}")

    assert r.status_code == 200 and r.data != SECRET
    assert r.data[:2] == b"\xff\xd8"
