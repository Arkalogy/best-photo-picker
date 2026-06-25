"""Direct unit tests for bpp.scoring.model_manifest.

The manifest powers the per-model consent dialog. Coverage was at 47.7%
because the module is mostly imports + a large literal list — the
interesting test surface is the ``is_present`` predicate and
``pending_downloads`` filter, plus invariants on the registry shape.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from bpp.scoring.model_manifest import (
    ModelEntry,
    _host_of,
    all_models,
    pending_downloads,
)

# ── ModelEntry.is_present ────────────────────────────────────────────────


class TestIsPresent:
    def _make(self, path, bundled_path=None) -> ModelEntry:
        return ModelEntry(
            name="test",
            path=path,
            url="https://example.com/x.bin",
            sha256="0" * 64,
            size_mb=1.0,
            host="example.com",
            bundled_path=bundled_path,
        )

    def test_returns_true_when_cache_file_exists(self, tmp_path):
        cache = tmp_path / "model.bin"
        cache.write_bytes(b"\x00")
        entry = self._make(cache)
        assert entry.is_present() is True

    def test_returns_false_when_neither_path_exists(self, tmp_path):
        cache = tmp_path / "missing.bin"
        entry = self._make(cache)
        assert entry.is_present() is False

    def test_returns_true_when_only_bundled_exists(self, tmp_path):
        cache = tmp_path / "missing-cache.bin"
        bundled = tmp_path / "shipped.bin"
        bundled.write_bytes(b"\x00")
        entry = self._make(cache, bundled_path=str(bundled))
        assert entry.is_present() is True

    def test_cache_takes_precedence_over_bundled(self, tmp_path):
        """If both exist, is_present is still True (no ambiguity to test
        for; just verify the short-circuit doesn't crash)."""
        cache = tmp_path / "cache.bin"
        cache.write_bytes(b"\x00")
        bundled = tmp_path / "shipped.bin"
        bundled.write_bytes(b"\x00")
        entry = self._make(cache, bundled_path=str(bundled))
        assert entry.is_present() is True


# ── _host_of ─────────────────────────────────────────────────────────────


class TestHostOf:
    def test_extracts_github_host(self):
        assert _host_of("https://github.com/foo/bar/releases/x.bin") == "github.com"

    def test_extracts_huggingface_host(self):
        assert _host_of("https://huggingface.co/openai/clip/resolve/main/x") == "huggingface.co"

    def test_handles_subdomain(self):
        assert _host_of("https://storage.googleapis.com/mediapipe-models/x") == (
            "storage.googleapis.com"
        )

    def test_malformed_url_returns_unknown(self):
        # No scheme or netloc → hostname is None → falls back to "unknown"
        assert _host_of("not-a-url") == "unknown"

    def test_empty_url_returns_unknown(self):
        assert _host_of("") == "unknown"


# ── all_models ───────────────────────────────────────────────────────────


class TestAllModels:
    def test_returns_non_empty_list(self):
        models = all_models()
        assert isinstance(models, list)
        assert len(models) > 0

    def test_every_entry_is_model_entry(self):
        for entry in all_models():
            assert isinstance(entry, ModelEntry)

    def test_includes_known_model_families(self):
        names = {m.name for m in all_models()}
        # Sanity-check that the major model families are present.
        assert any("BlazeFace" in n for n in names)
        assert any("CLIP" in n for n in names)
        assert any("YOLO" in n or "pet" in n.lower() for n in names)
        assert any("SCRFD" in n for n in names)

    def test_every_entry_has_valid_sha256(self):
        sha_re = re.compile(r"^[0-9a-f]{64}$")
        for entry in all_models():
            assert sha_re.match(entry.sha256), (
                f"{entry.name} has malformed SHA-256: {entry.sha256!r}"
            )

    def test_every_entry_has_https_url(self):
        for entry in all_models():
            assert entry.url.startswith("https://"), f"{entry.name} URL is not HTTPS: {entry.url}"

    def test_every_entry_has_positive_size(self):
        for entry in all_models():
            assert entry.size_mb > 0, f"{entry.name} has non-positive size: {entry.size_mb}"

    def test_host_matches_url(self):
        for entry in all_models():
            assert entry.host == _host_of(entry.url), (
                f"{entry.name}: host {entry.host!r} doesn't match URL {entry.url!r}"
            )

    def test_path_is_path_instance(self):
        for entry in all_models():
            assert isinstance(entry.path, Path), (
                f"{entry.name} path is not a Path: {type(entry.path).__name__}"
            )

    def test_names_are_unique(self):
        names = [m.name for m in all_models()]
        assert len(names) == len(set(names)), "Duplicate model names in manifest"

    def test_paths_are_unique(self):
        """Two entries must not point at the same cache path — a single
        download can only satisfy one entry."""
        paths = [str(m.path) for m in all_models()]
        assert len(paths) == len(set(paths)), "Duplicate cache paths in manifest"


# ── pending_downloads ────────────────────────────────────────────────────


class TestPendingDownloads:
    def test_filters_by_presence(self, monkeypatch):
        """When all entries report is_present=True, pending list is empty."""
        monkeypatch.setattr(ModelEntry, "is_present", lambda self: True)
        assert pending_downloads() == []

    def test_returns_all_when_none_present(self, monkeypatch):
        """When all entries report is_present=False, pending equals all_models."""
        monkeypatch.setattr(ModelEntry, "is_present", lambda self: False)
        pending = pending_downloads()
        all_m = all_models()
        assert len(pending) == len(all_m)

    def test_subset_of_all_models(self):
        """Whatever the live presence state, pending is a subset of all."""
        all_names = {m.name for m in all_models()}
        pending_names = {m.name for m in pending_downloads()}
        assert pending_names.issubset(all_names)


# ── ModelEntry is frozen (regression for accidental mutation) ────────────


class TestImmutability:
    def test_entry_is_frozen(self):
        e = ModelEntry(
            name="x", path=Path("/tmp/x"), url="https://x", sha256="0" * 64, size_mb=1, host="x"
        )
        with pytest.raises((AttributeError, TypeError)):
            e.name = "y"
