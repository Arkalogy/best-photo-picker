"""Shared model-download mechanics: a corrupt cache heals itself, failure
reasons are plain words, every writer uses its own temp file, stale temp
files from crashed runs are cleaned up, and concurrent callers share one
download."""

from __future__ import annotations

import os
import threading
import time
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from bpp.scoring.model_base import ModelIntegrityError, ModelSingleton
from bpp.utils import download as dl

GOOD = b"good model bytes"


@pytest.fixture(autouse=True)
def _no_skipped_downloads(monkeypatch):
    """Code under test may mark downloads as skipped for "this process";
    keep that from leaking into other tests."""
    monkeypatch.setenv("BPP_SKIP_DOWNLOADS", "")


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _singleton(target: Path, *, url: str | None = "https://example.com/m.onnx", **kw):
    ms = ModelSingleton(
        name="test",
        model_path=target,
        model_url=url,
        model_sha256=_sha(GOOD),
        create_fn=lambda p: object(),
        registry_id="openai_clip_vit_b32_onnx",
        **kw,
    )
    ms._license_allows_load = lambda: True  # type: ignore[method-assign]
    return ms


def _writes(data: bytes = GOOD, seen: list | None = None):
    def fake(url, dest, *, registry_id, sha256=None, **kw):
        if seen is not None:
            seen.append(dest)
        Path(dest).write_bytes(data)
        if sha256 and _sha(data) != sha256:
            os.remove(dest)
            raise ModelIntegrityError("SHA-256 mismatch")

    return fake


# ── 1-2: a corrupt cached file heals in every caller ─────────────────


def test_corrupt_cache_is_redownloaded_by_the_shared_loader(tmp_path):
    target = tmp_path / "m.onnx"
    target.write_bytes(b"truncated")
    ms = _singleton(target)
    with patch("bpp.utils.download.download_file", side_effect=_writes()) as fetch:
        assert ms.ensure_model() == target
    assert target.read_bytes() == GOOD
    assert fetch.call_count == 1


def test_get_heals_a_corrupt_cache_instead_of_failing(tmp_path):
    target = tmp_path / "m.onnx"
    target.write_bytes(b"truncated")
    ms = _singleton(target)
    with patch("bpp.utils.download.download_file", side_effect=_writes()):
        assert ms.get() is not None


def test_a_redownload_that_still_fails_verification_raises(tmp_path):
    target = tmp_path / "m.onnx"
    target.write_bytes(b"truncated")
    ms = _singleton(target)
    with (
        patch("bpp.utils.download.download_file", side_effect=_writes(b"also bad")) as fetch,
        pytest.raises(ModelIntegrityError),
    ):
        ms.ensure_model()
    assert fetch.call_count == 1  # healed once, never looped


def test_a_corrupt_cache_with_no_way_to_refetch_stays_loud(tmp_path):
    target = tmp_path / "m.onnx"
    target.write_bytes(b"truncated")
    ms = _singleton(target, url=None)
    with pytest.raises(ModelIntegrityError):
        ms.ensure_model()


# ── 3: failure reasons in plain words ────────────────────────────────


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (
            urllib.error.HTTPError("https://x", 404, "Not Found", None, None),
            "the download site returned HTTP 404",
        ),
        (urllib.error.URLError(TimeoutError("timed out")), "the download timed out"),
        (
            urllib.error.URLError(OSError("nodename nor servname")),
            "no internet connection, or the download site is unreachable",
        ),
        (TimeoutError("timed out"), "the download timed out"),
        (
            OSError("disk full"),
            "a file couldn't be written or deleted — check free disk space and folder permissions",
        ),
    ],
)
def test_failures_are_worded_plainly(tmp_path, exc, reason):
    ms = _singleton(tmp_path / "m.onnx")
    with (
        patch("bpp.utils.download.download_file", side_effect=exc),
        patch("time.sleep"),
        pytest.raises(RuntimeError) as err,
    ):
        ms.ensure_model_or_raise()
    assert str(err.value) == reason


def test_ensure_model_or_raise_returns_the_path(tmp_path):
    ms = _singleton(tmp_path / "m.onnx")
    with patch("bpp.utils.download.download_file", side_effect=_writes()):
        assert ms.ensure_model_or_raise() == tmp_path / "m.onnx"


def test_unaccepted_license_is_the_reason(tmp_path):
    ms = _singleton(tmp_path / "m.onnx")
    ms._license_allows_load = lambda: False  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="license"):
        ms.ensure_model_or_raise()


# ── 6: concurrent callers share one download, each gets its own reason ──


def test_concurrent_callers_download_once(tmp_path):
    target = tmp_path / "m.onnx"
    ms = _singleton(target)
    calls = {"n": 0}
    gate = threading.Event()

    def slow(url, dest, **kw):
        calls["n"] += 1
        gate.wait(2)
        _writes()(url, dest, **kw)

    results: list = []
    with patch("bpp.utils.download.download_file", side_effect=slow):
        threads = [
            threading.Thread(target=lambda: results.append(ms.ensure_model())) for _ in range(3)
        ]
        for t in threads:
            t.start()
        time.sleep(0.2)
        gate.set()
        for t in threads:
            t.join(5)
    assert calls["n"] == 1
    assert results == [target, target, target]


# ── 4, 8: per-writer temp files; stale ones are swept ────────────────


def test_temp_path_is_unique_per_process_and_thread(tmp_path):
    dest = str(tmp_path / "m.onnx")
    mine = dl.download_temp_path(dest)
    other: list = []
    t = threading.Thread(target=lambda: other.append(dl.download_temp_path(dest)))
    t.start()
    t.join()
    assert mine != other[0]
    assert mine.startswith(dest + ".") and mine.endswith(".tmp")
    assert str(os.getpid()) in mine


def test_stale_temp_files_are_swept_but_fresh_ones_kept(tmp_path):
    dest = tmp_path / "m.onnx"
    stale = tmp_path / "m.onnx.111.222.tmp"
    legacy = tmp_path / "m.onnx.tmp"
    fresh = tmp_path / "m.onnx.333.444.tmp"  # another process mid-download
    unrelated = tmp_path / "other.onnx.111.222.tmp"
    for p in (stale, legacy, fresh, unrelated):
        p.write_bytes(b"x")
    old = time.time() - 2 * 3600
    for p in (stale, legacy, unrelated):
        os.utime(p, (old, old))
    dl.download_temp_path(str(dest))
    assert not stale.exists() and not legacy.exists()
    assert fresh.exists() and unrelated.exists()


def test_remove_partial_downloads_clears_every_leftover(tmp_path):
    d = tmp_path / "models"
    d.mkdir()
    (d / "m.onnx.99.1.tmp").write_bytes(b"p")
    (d / "m.onnx.tmp").write_bytes(b"p")
    (d / "other.onnx.1.1.tmp").write_bytes(b"keep")
    dl.remove_partial_downloads(str(d / "m.onnx"))
    assert sorted(p.name for p in d.iterdir()) == ["other.onnx.1.1.tmp"]


@pytest.mark.parametrize(
    "writer",
    ["singleton", "pets", "yunet", "sface", "redownload"],
)
def test_every_model_writer_uses_a_per_writer_temp_file(tmp_path, monkeypatch, writer):
    seen: list = []

    def plain(url, dest, **kw):  # no sha check — just record the temp
        seen.append(dest)
        Path(dest).write_bytes(b"x")

    with patch("bpp.utils.download.download_file", side_effect=plain):
        if writer == "singleton":
            ms = _singleton(tmp_path / "m.onnx")
            # Pinned to the stub's bytes: an unpinned model is refused
            # before any fetch (tests/test_model_singleton_unpinned.py).
            ms.model_sha256 = _sha(b"x")
            ms.ensure_model()
        elif writer == "pets":
            from bpp.scoring import pets

            monkeypatch.setattr(pets, "_get_model_path", lambda: str(tmp_path / "y.onnx"))
            pets.ensure_model()
        elif writer == "yunet":
            from bpp.scoring import face_yunet

            monkeypatch.setattr(face_yunet, "_YUNET_MODEL_PATH", str(tmp_path / "yunet.onnx"))
            monkeypatch.setattr(face_yunet, "_enforce_yunet_policy", lambda: None)
            face_yunet._ensure_yunet_model()
        elif writer == "sface":
            from bpp.scoring import face_embed_sface_runtime as sf

            monkeypatch.setattr(sf, "_SFACE_MODEL_PATH", str(tmp_path / "sface.onnx"))
            monkeypatch.setattr(sf, "_enforce_sface_policy", lambda: None)
            sf._ensure_sface_model()
        else:
            from bpp.scoring import model_lifecycle
            from bpp.web import bp_models

            monkeypatch.setattr(model_lifecycle, "reset_model_file", lambda name: None)
            monkeypatch.setattr(
                "bpp.web.models_status._file_info", lambda name, path: {"name": name}
            )
            bp_models._fetch_redownload("x", str(tmp_path / "r.onnx"), "https://x", None, "lid")
    assert seen, writer
    assert not seen[0].endswith(".onnx.tmp"), seen[0]
    assert str(os.getpid()) in seen[0], seen[0]


# ── review round 2 ──────────────────────────────────────────────────


def test_a_bad_bundled_copy_is_not_loaded_on_repair(tmp_path):
    """A cached file that fails its check is replaced from the bundle —
    and the bundle copy is checked too, so bad bytes never load."""
    target = tmp_path / "m.onnx"
    bundled = tmp_path / "bundled.onnx"
    bundled.write_bytes(b"bad bundle")
    target.write_bytes(b"bad bundle")
    ms = _singleton(target, url=None, bundled_path=str(bundled))
    with pytest.raises(ModelIntegrityError):
        ms.ensure_model()
    with pytest.raises(ModelIntegrityError):
        ms.get()


def test_downloads_are_announced(tmp_path):
    target = tmp_path / "m.onnx"
    ms = _singleton(target)
    said: list = []
    with (
        dl.announce_downloads(said.append),
        patch("bpp.utils.download.download_file", side_effect=_writes()),
    ):
        ms.ensure_model()
        target.write_bytes(b"damaged")
        ms.ensure_model()
    assert said == [
        "Downloading test…",
        "Re-downloading test — the saved copy was damaged…",
    ]


def test_announcing_is_off_outside_the_context(tmp_path):
    ms = _singleton(tmp_path / "m.onnx")
    with patch("bpp.utils.download.download_file", side_effect=_writes()):
        ms.ensure_model()  # no callback set: must not fail


def test_uninstall_waits_for_an_in_flight_download(tmp_path):
    """Removing the files while a download runs must not let the
    download's retry put the model back afterwards."""
    target = tmp_path / "m.onnx"
    ms = _singleton(target)
    started, release = threading.Event(), threading.Event()

    def slow(url, dest, **kw):
        started.set()
        release.wait(2)
        _writes()(url, dest, **kw)

    with patch("bpp.utils.download.download_file", side_effect=slow):
        t = threading.Thread(target=ms.ensure_model)
        t.start()
        started.wait(2)
        freed: list = []
        remover = threading.Thread(target=lambda: freed.append(ms.remove_files()))
        remover.start()
        time.sleep(0.1)
        assert remover.is_alive()  # blocked behind the download
        release.set()
        t.join(5)
        remover.join(5)
    assert not target.exists()
    assert freed == [len(GOOD)]


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (
            urllib.error.URLError(
                __import__("ssl").SSLCertVerificationError("certificate verify failed")
            ),
            "the download site's security certificate couldn't be verified",
        ),
        (
            __import__("ssl").SSLError("handshake failure"),
            "a secure connection to the download site failed",
        ),
        (ConnectionResetError(54, "Connection reset by peer"), "the connection dropped"),
        (
            __import__("http.client").client.IncompleteRead(b"", 10),
            "the connection dropped",
        ),
    ],
)
def test_connection_failures_are_worded_plainly(tmp_path, exc, reason):
    ms = _singleton(tmp_path / "m.onnx")
    with (
        patch("bpp.utils.download.download_file", side_effect=exc),
        patch("time.sleep"),
        pytest.raises(RuntimeError) as err,
    ):
        ms.ensure_model_or_raise()
    assert str(err.value) == reason


def test_buffalo_s_zip_downloads_to_a_per_writer_temp_file(tmp_path, monkeypatch):
    from bpp.scoring import face_embed_buffalo_s as bs

    monkeypatch.setattr(bs, "_extracted_model_path", lambda: tmp_path / "w600k_mbf.onnx")
    seen: list = []

    def stop(url, dest, **kw):
        seen.append(dest)
        raise OSError("offline")

    with patch("bpp.utils.download.download_file", side_effect=stop), pytest.raises(OSError):
        bs.ensure_buffalo_s_model()
    assert seen[0].startswith(str(tmp_path / "w600k_mbf.zip") + ".")
    assert str(os.getpid()) in seen[0]


def test_lama_uninstall_clears_partial_downloads(tmp_path, monkeypatch):
    from bpp.ai import inpainting

    weights = tmp_path / "big-lama.pt"
    weights.write_bytes(b"x" * 5)
    leftover = tmp_path / "big-lama.pt.1.2.tmp"
    leftover.write_bytes(b"p")
    monkeypatch.setattr(inpainting, "_LAMA_MODEL_PATH", weights)
    assert inpainting.remove_local_weights() == 5
    assert not weights.exists() and not leftover.exists()


def test_buffalo_s_uninstall_clears_partial_downloads(tmp_path, monkeypatch):
    from bpp.scoring import face_embed_buffalo_s as bs

    monkeypatch.setattr(bs, "_extracted_model_path", lambda: tmp_path / "w600k_mbf.onnx")
    leftover = tmp_path / "w600k_mbf.zip.1.2.tmp"
    leftover.write_bytes(b"p")
    bs.remove_local_weights()
    assert not leftover.exists()


# ── review round 3 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exc",
    [
        OSError(28, "No space left on device", "/Users/someone/.cache/bpp/m.onnx.1.2.tmp"),
        PermissionError(13, "Permission denied", "/Users/someone/.cache/bpp"),
    ],
)
def test_file_errors_never_show_a_local_path(tmp_path, exc):
    ms = _singleton(tmp_path / "m.onnx")
    with (
        patch("bpp.utils.download.download_file", side_effect=exc),
        patch("time.sleep"),
        pytest.raises(RuntimeError) as err,
    ):
        ms.ensure_model_or_raise()
    assert "/Users" not in str(err.value)
    assert str(err.value) == (
        "a file couldn't be written or deleted — check free disk space and folder permissions"
    )


def test_an_unwritable_model_folder_is_a_plain_failure(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_bytes(b"x")
    ms = _singleton(blocker / "sub" / "m.onnx")  # parent can't be created
    with pytest.raises(RuntimeError, match="couldn't be written"):
        ms.ensure_model_or_raise()


def test_a_refused_connection_is_unreachable_not_dropped(tmp_path):
    ms = _singleton(tmp_path / "m.onnx")
    with (
        patch(
            "bpp.utils.download.download_file",
            side_effect=urllib.error.URLError(ConnectionRefusedError(61, "refused")),
        ),
        patch("time.sleep"),
        pytest.raises(RuntimeError) as err,
    ):
        ms.ensure_model_or_raise()
    assert str(err.value) == "no internet connection, or the download site is unreachable"
