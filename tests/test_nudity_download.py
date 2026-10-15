"""The content-filter model downloads through the shared loader: retries,
a per-process temp file, and a visible step before analysis."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bpp.scoring import nudity


@pytest.fixture
def cache(tmp_path, monkeypatch):
    target = tmp_path / "nudenet" / "320n.onnx"
    monkeypatch.setattr(nudity._nudenet, "model_path", target)
    # License accepted for these download-mechanics tests.
    monkeypatch.setattr(nudity._nudenet, "_license_allows_load", lambda: True)
    return target


def _writes(dest_seen):
    def fake(url, dest, *, registry_id, sha256=None, **kw):
        dest_seen.append(dest)
        Path(dest).write_bytes(b"onnx")

    return fake


def test_a_failed_download_is_retried(cache):
    seen: list = []
    calls = {"n": 0}

    def flaky(url, dest, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("connection reset")
        _writes(seen)(url, dest, **kw)

    with patch("bpp.utils.download.download_file", side_effect=flaky), patch("time.sleep"):
        path = nudity.ensure_nudenet_model()
    assert Path(path) == cache and cache.read_bytes() == b"onnx"
    assert calls["n"] == 2


def test_each_process_downloads_to_its_own_temp_file(cache):
    import os

    seen: list = []
    with patch("bpp.utils.download.download_file", side_effect=_writes(seen)):
        nudity.ensure_nudenet_model()
    assert seen and seen[0] != str(cache) + ".tmp"
    assert str(os.getpid()) in seen[0]


def _preflight(monkeypatch, *, blocked, on_disk, toggles=None):
    from bpp.web import analyze_model_preflight as pf

    events: list = []
    ensure = []
    monkeypatch.setattr(nudity, "is_available", lambda: True)
    monkeypatch.setattr(nudity, "is_on_disk", lambda: on_disk)
    monkeypatch.setattr(nudity, "ensure_nudenet_model", lambda: ensure.append(1) or "x")
    with (
        patch(
            "bpp.registry.policy.is_load_blocked",
            side_effect=lambda mid: blocked if mid == nudity.REGISTRY_ID else False,
        ),
        patch.object(pf, "_download_builtin_models", lambda emit, toggles: set()),
    ):
        failed = pf._download_accepted_models(events.append, toggles or {})
    assert failed == set()
    return events, ensure


def test_preflight_downloads_the_accepted_content_filter_with_a_status(cache, monkeypatch):
    from bpp.web import analyze_model_preflight as pf

    events: list = []
    monkeypatch.setattr(nudity, "is_available", lambda: True)
    with (
        patch("bpp.registry.policy.is_load_blocked", return_value=False),
        patch.object(
            pf, "preflight_models", lambda emit, t: set(pf._download_content_filter(emit) and ())
        ),
        patch("bpp.utils.download.download_file", side_effect=_writes([])),
    ):
        pf.preflight_run_config(events.append, {})
    assert {"type": "status", "message": "Downloading content filter model (NudeNet)…"} in events


def test_preflight_leaves_it_alone_when_unaccepted(monkeypatch):
    _, ensure = _preflight(monkeypatch, blocked=True, on_disk=False)
    assert ensure == []


def test_preflight_rechecks_a_present_file(monkeypatch):
    """A cached file is verified (and healed if damaged) before scoring."""
    _, ensure = _preflight(monkeypatch, blocked=False, on_disk=True)
    assert ensure == [1]


def test_preflight_skips_it_when_the_content_filter_is_off(monkeypatch):
    _, ensure = _preflight(
        monkeypatch, blocked=False, on_disk=False, toggles={"model_nudity": False}
    )
    assert ensure == []


def test_a_failed_download_says_why(cache):
    with (
        patch("bpp.utils.download.download_file", side_effect=RuntimeError("network unreachable")),
        patch("time.sleep"),
        pytest.raises(RuntimeError) as exc,
    ):
        nudity.ensure_nudenet_model()
    # Just the reason — callers add their own "Couldn't download …".
    assert str(exc.value) == "network unreachable"


def test_unaccepted_license_says_so(cache, monkeypatch):
    monkeypatch.setattr(nudity._nudenet, "_license_allows_load", lambda: False)
    with pytest.raises(RuntimeError, match="license"):
        nudity.ensure_nudenet_model()


def test_a_bad_fresh_download_is_not_fetched_twice(cache):
    from bpp.scoring.model_base import ModelIntegrityError

    calls = {"n": 0}

    def bad(url, dest, **kw):
        calls["n"] += 1
        raise ModelIntegrityError("sha mismatch")

    with (
        patch("bpp.utils.download.download_file", side_effect=bad),
        pytest.raises(ModelIntegrityError),
    ):
        nudity.ensure_nudenet_model()
    assert calls["n"] == 1


def test_uninstall_removes_leftover_partial_downloads(cache):
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"x" * 10)
    leftover = cache.with_suffix(".onnx.1234.5678.tmp")
    leftover.write_bytes(b"partial")
    assert nudity.remove_local_weights() == 10
    assert not cache.exists() and not leftover.exists()


def test_a_corrupt_cached_file_is_replaced(cache):
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"truncated")
    seen: list = []
    with patch("bpp.utils.download.download_file", side_effect=_writes(seen)):
        path = nudity.ensure_nudenet_model()
    assert Path(path).read_bytes() == b"onnx" and len(seen) == 1


def test_a_failed_content_filter_download_never_blocks_analysis(monkeypatch):
    from bpp.web import analyze_model_preflight as pf

    events: list = []
    monkeypatch.setattr(nudity, "is_available", lambda: True)
    monkeypatch.setattr(nudity, "is_on_disk", lambda: False)

    def fail():
        raise RuntimeError("no internet connection")

    monkeypatch.setattr(nudity, "ensure_nudenet_model", fail)
    with patch("bpp.registry.policy.is_load_blocked", return_value=False):
        assert pf._download_content_filter(events.append) is False  # must not raise
    assert {
        "type": "warning",
        "message": "Content filter model unavailable: no internet connection",
    } in events


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (
            __import__("urllib.error").error.URLError("[Errno 8] nodename nor servname"),
            "no internet connection, or the download site is unreachable",
        ),
        (TimeoutError("timed out"), "the download timed out"),
        (
            OSError("disk full"),
            "a file couldn't be written or deleted — check free disk space and folder permissions",
        ),
    ],
)
def test_network_failures_are_worded_plainly(cache, exc, reason):
    with (
        patch("bpp.utils.download.download_file", side_effect=exc),
        patch("time.sleep"),
        pytest.raises(RuntimeError) as err,
    ):
        nudity.ensure_nudenet_model()
    assert str(err.value) == reason


def test_preflight_reports_a_failed_content_filter_so_the_run_turns_it_off(monkeypatch):
    from bpp.web import analyze_model_preflight as pf

    monkeypatch.setattr(pf, "_download_builtin_models", lambda emit, toggles: {"model_pets"})
    monkeypatch.setattr(pf, "_download_content_filter", lambda emit: False)
    assert pf._download_accepted_models(lambda e: None, {}) == {"model_pets", "model_nudity"}
