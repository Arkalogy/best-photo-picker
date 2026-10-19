"""Shared RAW decoding: every full-size reader (content filter, pets,
CLIP search) gets camera RAW photos; network-drive glitches are retried
without holding the whole file in memory; missing RAW support is said
once, with its real cause; RAW previews are written atomically and fail
with a reason."""

from __future__ import annotations

import sys
import threading
import types
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from bpp.utils import raw


class _LibRawIOError(Exception):
    pass


def _fake_rawpy(monkeypatch, rgb=None, fail_times=0, seen=None):
    seen = seen if seen is not None else []
    calls = {"n": 0}

    class _Raw:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def postprocess(self, **kw):
            seen.append(kw)
            return rgb

    def imread(arg):
        seen.append(arg)
        calls["n"] += 1
        if calls["n"] <= fail_times:
            raise _LibRawIOError("read error")
        return _Raw()

    monkeypatch.setitem(
        sys.modules, "rawpy", types.SimpleNamespace(imread=imread, LibRawIOError=_LibRawIOError)
    )
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True)
    return seen, calls


def test_the_full_size_reader_opens_raw_photos_as_bgr(monkeypatch):
    from bpp.scoring.aggregate import read_image_for_scoring

    rgb = np.zeros((40, 60, 3), np.uint8)
    rgb[..., 0] = 255  # red
    _fake_rawpy(monkeypatch, rgb)
    img = read_image_for_scoring("/photos/a.CR2")
    assert img.shape == (40, 60, 3)
    assert img[0, 0].tolist() == [0, 0, 255]  # BGR: red is last


def test_a_drive_read_error_is_retried_from_the_path_not_from_memory(monkeypatch):
    import errno

    rgb = np.zeros((4, 6, 3), np.uint8)
    seen: list = []
    calls = {"n": 0}

    class _Raw:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def postprocess(self, **kw):
            seen.append(kw)
            return rgb

    def imread(arg):
        seen.append(arg)
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EIO, "Input/output error")
        return _Raw()

    monkeypatch.setitem(
        sys.modules, "rawpy", types.SimpleNamespace(imread=imread, LibRawIOError=_LibRawIOError)
    )
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True)
    with patch("time.sleep"):
        out = raw.decode_raw_rgb("/photos/a.CR2")
    assert out is rgb and calls["n"] == 2
    assert seen.count("/photos/a.CR2") == 2  # decoded from the path each time
    assert {"use_camera_wb": True, "half_size": False} in seen  # full size


def test_a_damaged_raw_file_fails_at_once_without_retries(monkeypatch):
    _, calls = _fake_rawpy(monkeypatch, fail_times=99)
    with patch("time.sleep") as sleep, pytest.raises(_LibRawIOError):
        raw.decode_raw_rgb("/photos/truncated.NEF")
    assert calls["n"] == 1
    sleep.assert_not_called()


def test_search_decodes_raw_at_half_size(monkeypatch):
    from bpp.scoring import clip_embed

    sizes: list = []
    monkeypatch.setattr(
        raw,
        "decode_raw_rgb",
        lambda p, *, half_size=False: sizes.append(half_size) or np.zeros((4, 6, 3), np.uint8),
    )
    monkeypatch.setattr(clip_embed, "compute_clip_embedding", lambda img: "embedding")
    assert clip_embed.compute_clip_embedding_from_file("/photos/a.CR2") == "embedding"
    assert sizes == [True]


def test_a_file_that_cant_be_decoded_raises_for_the_caller(monkeypatch):
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True)
    monkeypatch.setitem(
        sys.modules,
        "rawpy",
        types.SimpleNamespace(
            imread=MagicMock(side_effect=ValueError("not a raw file")),
            LibRawIOError=_LibRawIOError,
        ),
    )
    with pytest.raises(ValueError):
        raw.decode_raw_rgb("/photos/a.CR2")
    assert raw.open_raw_as_pil("/photos/a.CR2") is None  # the PIL helper still swallows it


def test_missing_raw_support_is_one_warning_with_the_reason(monkeypatch):
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", False)
    monkeypatch.setattr(raw, "_RAWPY_IMPORT_ERROR", "libraw.dylib not found")
    monkeypatch.setattr(raw, "_missing_rawpy_once", threading.Lock())
    with patch.object(raw, "log") as log:
        assert raw.open_raw_as_pil("/photos/a.CR2") is None
        assert raw.convert_raw_to_jpeg("/photos/c.CR2", "/tmp/never.jpg") is None
    assert log.warning.call_count == 1
    message = log.warning.call_args.args[0] % log.warning.call_args.args[1:]
    assert "libraw.dylib not found" in message
    assert "bppicker[raw]" in message and "desktop app" in message


def test_raw_to_jpeg_conversion_uses_the_shared_decoder(tmp_path, monkeypatch):
    rgb = np.full((4, 6, 3), 200, np.uint8)
    monkeypatch.setattr(raw, "decode_raw_rgb", lambda p, **kw: rgb)
    out = tmp_path / "a.jpg"
    assert raw.convert_raw_to_jpeg(str(tmp_path / "a.CR2"), str(out)) == str(out)
    assert out.stat().st_size > 0


def test_a_failed_preview_write_leaves_no_partial_file(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(raw, "decode_raw_rgb", lambda p, **kw: np.zeros((4, 6, 3), np.uint8))
    previews = tmp_path / "previews"
    previews.mkdir()
    out = previews / "a.jpg"

    def half_written(self, fp, *a, **kw):
        with open(fp, "wb") as f:
            f.write(b"\xff\xd8 partial")
        raise OSError(28, "No space left on device")

    with patch.object(Image.Image, "save", half_written):
        assert raw.convert_raw_to_jpeg(str(tmp_path / "a.CR2"), str(out)) is None
    assert not out.exists()
    assert list(previews.iterdir()) == []  # no temp file left either


def test_preview_failure_says_why_without_local_details(monkeypatch):
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", False)
    monkeypatch.setattr(
        raw, "_RAWPY_IMPORT_ERROR", "dlopen(/Users/someone/x/_rawpy.so): libraw not loaded"
    )
    reason, recovery = raw.preview_failure()
    assert reason == "RAW support isn't available in this install"
    assert "desktop app" in recovery and "bppicker[raw]" in recovery
    assert "/Users" not in reason + recovery  # the import error stays in the log
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True)
    reason, recovery = raw.preview_failure()
    assert reason == "the RAW file couldn't be converted"
    assert "Activity" in recovery


def test_the_quiet_reader_does_not_warn_per_photo_when_raw_support_is_missing(monkeypatch):
    from bpp.scoring import aggregate

    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", False)
    with patch.object(aggregate, "log") as log, patch.object(raw, "log"):
        assert aggregate.read_image_for_scoring("/photos/a.CR2") is None
        assert aggregate.read_image_for_scoring("/photos/b.CR2") is None
    log.warning.assert_not_called()  # the RAW module warns once instead


def test_the_preview_endpoint_returns_reason_and_recovery(monkeypatch):
    from bpp.errors import BppError
    from bpp.web import bp_media

    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", False)
    err = bp_media.raw_preview_error("abc123")
    assert isinstance(err, BppError)
    body = err.to_dict()
    assert body["error"] == "RAW support isn't available in this install"
    assert "desktop app" in body["context"]["recovery"]
    assert "`" not in body["context"]["recovery"]
