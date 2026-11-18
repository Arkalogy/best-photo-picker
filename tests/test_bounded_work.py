"""One odd file or request must not take the app's memory or stall it.

Work is bounded by what comes out (the model's input, a preview cell),
not only by how many pixels were decoded; text that reaches the log is
scanned in linear time; a file that never answers is not opened.
"""

from __future__ import annotations

import io
import os
import tarfile
import time

import numpy as np
import pytest

# ── log cleaning ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("run", ["_" * 200_000, "a" * 200_000, "token_" * 30_000])
def test_a_long_run_in_a_log_line_is_cleaned_in_linear_time(run):
    from bpp.utils.logging import redact_secrets

    line = f'127.0.0.1 - - "GET /{run} HTTP/1.1" 404 -'
    started = time.perf_counter()
    assert redact_secrets(line) == line
    assert time.perf_counter() - started < 2.0


def test_token_values_are_still_removed_with_their_names_kept():
    from bpp.utils.logging import redact_secrets

    secret = "s3cr3tVALUE0123456789abcdef"
    for line in (
        f"lan_share_token={secret}",
        f"'lan_share_token': '{secret}'",
        f"legacy_app_token: {secret}",
        f"auth_token = {secret}",
    ):
        cleaned = redact_secrets(line)
        assert secret not in cleaned, line
        assert cleaned.startswith(line.split(secret)[0].rstrip("'\" ")[:10]), cleaned
        assert "[REDACTED]" in cleaned


# ── images sized by their output ─────────────────────────────────────────


def _largest_resize(monkeypatch, module):
    """Record the pixel count of the largest image cv2 is asked to make."""
    import cv2

    seen = {"pixels": 0}
    real_resize, real_border = cv2.resize, cv2.copyMakeBorder

    def resize(src, dsize, *a, **k):
        seen["pixels"] = max(seen["pixels"], dsize[0] * dsize[1])
        return real_resize(src, dsize, *a, **k)

    def border(src, top, bottom, left, right, *a, **k):
        h, w = src.shape[:2]
        seen["pixels"] = max(seen["pixels"], (h + top + bottom) * (w + left + right))
        return real_border(src, top, bottom, left, right, *a, **k)

    monkeypatch.setattr(cv2, "resize", resize)
    monkeypatch.setattr(cv2, "copyMakeBorder", border)
    return seen


def test_a_very_thin_image_does_not_blow_up_smart_search_indexing(monkeypatch):
    from bpp.scoring import clip_embed

    seen = _largest_resize(monkeypatch, clip_embed)
    out = clip_embed._preprocess(np.zeros((2, 60_000, 3), dtype=np.uint8))
    assert out.shape == (1, 3, 224, 224)
    assert seen["pixels"] <= 224 * 224 * 8


def _plain_clip_prepare(image):
    """The preparation with no trimming: resize the short side to 224,
    take the centre, normalise (what the trim must not change)."""
    import cv2

    from bpp.scoring import clip_embed

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    scale = 224 / min(h, w)
    new_h, new_w = max(224, round(h * scale)), max(224, round(w * scale))
    resized = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
    y0, x0 = (new_h - 224) // 2, (new_w - 224) // 2
    return resized[y0 : y0 + 224, x0 : x0 + 224], clip_embed


@pytest.mark.parametrize("shape", [(400, 600), (600, 400), (224, 1792), (300, 2400), (2400, 300)])
def test_photos_and_panoramas_are_prepared_exactly_as_before(shape):
    """Up to 8:1 nothing is trimmed: the embedding of every ordinary
    photo and panorama is bit for bit what it was."""
    rng = np.random.default_rng(7)
    image = rng.integers(0, 255, size=(*shape, 3), dtype=np.uint8)
    cropped, clip_embed = _plain_clip_prepare(image)
    out = clip_embed._preprocess(image)
    again = clip_embed._preprocess(np.ascontiguousarray(image))
    assert np.array_equal(out, again)
    # Compare through the same normalisation by preparing the crop itself.
    import cv2

    assert np.array_equal(out, clip_embed._preprocess(cv2.cvtColor(cropped, cv2.COLOR_RGB2BGR)))


@pytest.mark.parametrize("shape", [(40, 40_000), (7080, 28_200)])
def test_a_long_image_does_not_blow_up_the_content_filter(monkeypatch, shape):
    """The square is side x side whatever the pixel count: bounded for a
    thin strip and for a wide image just under any 'thin' rule."""
    from bpp.scoring import nudity

    seen = _largest_resize(monkeypatch, nudity)

    class _Lazy:
        """Stands in for the decoded image without holding its pixels."""

        def __init__(self, shape):
            self.shape = (*shape, 3)

    import cv2

    real_resize = cv2.resize
    monkeypatch.setattr(
        cv2,
        "resize",
        lambda src, dsize, *a, **k: (
            seen.__setitem__("pixels", max(seen["pixels"], dsize[0] * dsize[1]))
            or np.zeros((dsize[1], dsize[0], 3), dtype=np.uint8)
            if isinstance(src, _Lazy)
            else real_resize(src, dsize, *a, **k)
        ),
    )
    blob = nudity._to_blob(_Lazy(shape))
    assert blob.shape[-2:] == (nudity._INPUT_SIZE, nudity._INPUT_SIZE)
    assert seen["pixels"] <= nudity._PAD_LIMIT**2


@pytest.mark.parametrize("shape", [(300, 400), (3000, 4000), (1500, 7000)])
def test_photos_and_ordinary_panoramas_are_padded_as_before(monkeypatch, shape):
    from bpp.scoring import nudity

    seen = _largest_resize(monkeypatch, nudity)
    nudity._to_blob(np.zeros((*shape, 3), dtype=np.uint8))
    assert seen["pixels"] == max(shape) ** 2  # the same square the scores were tuned on


@pytest.mark.parametrize(
    ("frame", "cell"),
    [
        ((180, 320), (160, 90)),
        ((1920, 1080), (160, 284)),
        ((300, 4), (160, 480)),
        ((1, 4000), (160, 1)),
    ],
)
def test_a_preview_cell_has_a_bounded_height(frame, cell):
    from bpp.utils.video import sprite_cell_size

    h, w = frame
    assert sprite_cell_size(w, h, 160) == cell


# ── files that are not files ─────────────────────────────────────────────


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes are a POSIX thing")
def test_a_pipe_named_like_a_photo_is_not_imported(tmp_path, caplog):
    from bpp.io_scan import scan_images

    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "real.jpg").write_bytes(b"x")
    os.mkfifo(tmp_path / "sub" / "A_0002.jpg")
    os.mkfifo(tmp_path / "top.jpg")
    with caplog.at_level("WARNING"):
        found = [os.path.basename(p) for p in scan_images(str(tmp_path), recursive=True)]
    assert found == ["real.jpg"]
    assert "A_0002.jpg" in caplog.text  # the owner can see what was left out
    assert [os.path.basename(p) for p in scan_images(str(tmp_path), recursive=False)] == []


def test_links_inside_an_archive_are_not_unpacked(tmp_path):
    """A hard link in a tar is unpacked as a full copy of its target and
    was never counted against the size limit."""
    from bpp.web.analyze_archive import extract_archive_into_workdir

    archive = tmp_path / "photos.tar"
    with tarfile.open(archive, "w") as tf:
        data = b"\xff\xd8" + b"0" * 4096
        info = tarfile.TarInfo("a.jpg")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        for i in range(20):
            link = tarfile.TarInfo(f"copy{i}.jpg")
            link.type = tarfile.LNKTYPE
            link.linkname = "a.jpg"
            tf.addfile(link)
        sym = tarfile.TarInfo("s.jpg")
        sym.type = tarfile.SYMTYPE
        sym.linkname = "a.jpg"
        tf.addfile(sym)
    events = []
    out = extract_archive_into_workdir(str(archive), str(tmp_path / "work"), events.append)
    assert out is not None, events
    names = sorted(n for _d, _s, files in os.walk(out) for n in files)
    assert names == ["a.jpg"]
    assert any("21 links" in str(e.get("filepath", "")) for e in events), events


# ── the server ───────────────────────────────────────────────────────────


def test_tls_handshakes_do_not_run_on_the_accepting_thread():
    from bpp.web.wsgi_bind import lazy_tls

    calls = []

    class _Inner:
        def wrap_socket(self, sock, **kw):
            calls.append(kw)
            return sock

    assert lazy_tls(None) is None
    assert lazy_tls(_Inner()).wrap_socket("sock", server_side=True) == "sock"
    assert calls == [{"server_side": True, "do_handshake_on_connect": False}]


def test_a_silent_caller_does_not_stop_others_from_connecting_over_tls():
    """A caller that connects and says nothing used to hold the one
    accepting thread in its handshake: nobody else got in."""
    import socket
    import ssl
    import threading

    pytest.importorskip("cryptography")
    from flask import Flask
    from werkzeug.serving import make_server

    from bpp.web.wsgi_bind import lazy_tls, request_handler

    app = Flask(__name__)
    app.add_url_rule("/ping", view_func=lambda: "pong")
    server = make_server(
        "127.0.0.1",
        0,
        app,
        threaded=True,
        ssl_context=lazy_tls("adhoc"),
        request_handler=request_handler(),
    )
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    silent = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        raw = socket.create_connection(("127.0.0.1", port), timeout=5)
        with ssl._create_unverified_context().wrap_socket(raw) as client:
            client.sendall(b"GET /ping HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
            reply = b""
            while chunk := client.recv(4096):
                reply += chunk
        assert reply.endswith(b"pong")
    finally:
        silent.close()
        server.shutdown()
        server.server_close()


def _serving(timeout_s, body=b"pong"):
    import threading

    from flask import Flask
    from werkzeug.serving import make_server

    from bpp.web.wsgi_bind import request_handler

    app = Flask(__name__)
    app.add_url_rule("/ping", view_func=lambda: body)
    handler = type("Handler", (request_handler(),), {"timeout": timeout_s})
    server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def test_a_connection_that_sends_nothing_is_dropped(caplog):
    import logging
    import socket

    server, port = _serving(0.5)
    try:
        with caplog.at_level(logging.INFO):
            silent = socket.create_connection(("127.0.0.1", port), timeout=5)
            assert silent.recv(1) == b""  # closed by the server, not by us
            silent.close()
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR], "not an error line"
    finally:
        server.shutdown()
        server.server_close()


def test_a_slow_reader_still_gets_the_whole_answer():
    """The limit is on waiting for a request, not on how long a reply
    takes to be read."""
    import socket

    body = b"x" * (6 * 1024 * 1024)
    server, port = _serving(0.5, body)
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=10)
        client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        client.sendall(b"GET /ping HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
        got = 0
        started = time.perf_counter()
        while chunk := client.recv(65536):
            got += len(chunk)
            time.sleep(0.02)  # steady, but slower than the limit allows in total
        client.close()
        assert time.perf_counter() - started > 0.5
        assert got >= len(body)
    finally:
        server.shutdown()
        server.server_close()


def test_the_limit_is_what_the_servers_use():
    from bpp.web.wsgi_bind import CONNECTION_TIMEOUT_S, request_handler

    assert request_handler().timeout == CONNECTION_TIMEOUT_S
    assert 30 <= CONNECTION_TIMEOUT_S <= 300


def test_raw_thumbnails_wait_their_turn_and_skip_a_file_that_just_failed(tmp_path, monkeypatch):
    from bpp.utils import raw

    path = tmp_path / "broken.dng"
    path.write_bytes(b"not a raw file")
    tries = []

    def failing(p):
        assert raw.decode_slot()._value < 2, "the decode holds one of the shared slots"
        tries.append(p)

    monkeypatch.setattr(raw, "open_raw_as_pil", failing)
    assert raw.open_raw_as_pil_bounded(str(path)) is None
    assert raw.open_raw_as_pil_bounded(str(path)) is None
    assert len(tries) == 1  # the second request did not decode it again
    path.write_bytes(b"replaced with another file!")
    assert raw.open_raw_as_pil_bounded(str(path)) is None
    assert len(tries) == 2  # a changed file is tried again


def test_requests_that_arrive_together_decode_a_broken_raw_once(tmp_path, monkeypatch):
    import threading

    from bpp.utils import raw

    path = tmp_path / "slow.dng"
    path.write_bytes(b"slow to fail")
    tries = []

    def slow_failure(p):
        tries.append(p)
        time.sleep(0.2)

    monkeypatch.setattr(raw, "open_raw_as_pil", slow_failure)
    threads = [
        threading.Thread(target=raw.open_raw_as_pil_bounded, args=(str(path),)) for _ in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(tries) == 1


# ── other inputs ─────────────────────────────────────────────────────────


def test_a_manifest_that_cannot_be_encoded_is_just_invalid():
    from bpp.registry.signed_manifest import verify_manifest

    result = verify_manifest(
        {
            "schema_version": 1,
            "manifest_version": 1,
            "entries": [{"id": "\ud800"}],
            "signatures": [],
        },
        trusted_keys=[],
    )
    assert result.is_valid is False


def test_a_stretched_raw_frame_counts_at_its_stretched_size(monkeypatch):
    from bpp.utils import raw

    class _Sizes:
        width, height = 6000, 4000
        pixel_aspect = 0.1  # the frame comes out ten times taller

    class _Raw:
        sizes = _Sizes()

        def open_file(self, path):
            pass

        def unpack(self):
            raise AssertionError("the frame must not be unpacked")

        def close(self):
            pass

    pytest.importorskip("rawpy")
    monkeypatch.setattr(raw, "RAWPY_AVAILABLE", True, raising=False)
    monkeypatch.setattr(raw.rawpy, "RawPy", _Raw)
    monkeypatch.setattr(raw, "MAX_RAW_PIXELS", 100_000_000)
    with pytest.raises(raw.RawTooLarge, match="stretched x10"):
        raw._decode_path("/x.dng", False)
