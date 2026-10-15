"""Model downloads and uninstalls don't race, a download that already
failed in a run isn't silently retried, and direct (non-loader)
downloads still show a status line."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from bpp.utils import download as dl


@pytest.fixture(autouse=True)
def _no_skipped_downloads(monkeypatch):
    """Code under test may mark downloads as skipped for "this process";
    keep that from leaking into other tests."""
    monkeypatch.setenv("BPP_SKIP_DOWNLOADS", "")


def test_a_download_that_failed_earlier_in_the_run_is_skipped(monkeypatch):
    dl.skip_downloads_in_this_process(["opencv_yunet"])
    with (
        patch("urllib.request.urlopen") as urlopen,
        pytest.raises(dl.DownloadSkippedError),
    ):
        dl.download_file("https://x", "/nonexistent/f", registry_id="opencv_yunet")
    urlopen.assert_not_called()
    assert "opencv_yunet" in os.environ["BPP_SKIP_DOWNLOADS"]  # inherited by subprocesses


def test_uninstall_endpoint_waits_for_scrfd_and_singleton_downloads():
    from bpp.scoring import face_scrfd, segmentation
    from bpp.web.bp_models import _download_lock_for

    assert _download_lock_for("SCRFD 2.5g") is face_scrfd._scrfd_model.download_lock
    entry = next(
        e
        for e in __import__("bpp.scoring.model_base").scoring.model_base.ModelRegistry.all()
        if getattr(e.reset, "__self__", None) is segmentation._segmenter
    )
    assert _download_lock_for(entry.name) is segmentation._segmenter.download_lock


def test_lama_uninstall_waits_for_its_download(tmp_path, monkeypatch):
    from bpp.ai import inpainting

    monkeypatch.setattr(inpainting, "_LAMA_MODEL_PATH", tmp_path / "big-lama.pt")
    done: list = []
    with inpainting._LAMA.download_lock:
        t = threading.Thread(target=lambda: done.append(inpainting.remove_local_weights()))
        t.start()
        time.sleep(0.1)
        assert done == []
    t.join(5)
    assert done == [0]


def test_pets_download_holds_the_download_lock(tmp_path, monkeypatch):
    from bpp.scoring import pets

    monkeypatch.setattr(pets, "_get_model_path", lambda: str(tmp_path / "y.onnx"))
    held: list = []

    def fetch(url, dest, **kw):
        held.append(pets._yolo.download_lock.locked())
        Path(dest).write_bytes(b"x")

    with patch("bpp.utils.download.download_file", side_effect=fetch):
        pets.ensure_model()
    assert held == [True]


@pytest.mark.parametrize("which", ["yunet", "sface"])
def test_direct_downloads_are_announced(tmp_path, monkeypatch, which):
    said: list = []

    def fetch(url, dest, **kw):
        Path(dest).write_bytes(b"x")

    with (
        dl.announce_downloads(said.append),
        patch("bpp.utils.download.download_file", side_effect=fetch),
    ):
        if which == "yunet":
            from bpp.scoring import face_yunet

            monkeypatch.setattr(face_yunet, "_YUNET_MODEL_PATH", str(tmp_path / "y.onnx"))
            monkeypatch.setattr(face_yunet, "_enforce_yunet_policy", lambda: None)
            face_yunet._ensure_yunet_model()
        else:
            from bpp.scoring import face_embed_sface_runtime as sf

            monkeypatch.setattr(sf, "_SFACE_MODEL_PATH", str(tmp_path / "s.onnx"))
            monkeypatch.setattr(sf, "_enforce_sface_policy", lambda: None)
            sf._ensure_sface_model()
    assert len(said) == 1 and said[0].startswith("Downloading")
