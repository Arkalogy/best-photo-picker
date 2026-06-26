"""Tests for cache + models directory resolution."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts with a known-clean env so we test precedence
    explicitly."""
    monkeypatch.delenv("BPP_CACHE_DIR", raising=False)
    monkeypatch.delenv("BPP_MODELS_DIR", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    yield


class TestCacheDir:
    def test_default_is_home_cache_bpp(self):
        from bpp.utils.paths import cache_dir

        assert cache_dir() == Path.home() / ".cache" / "bpp"

    def test_xdg_cache_home_overrides_default(self, monkeypatch, tmp_path):
        from bpp.utils.paths import cache_dir

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert cache_dir() == tmp_path / "bpp"

    def test_xdg_cache_home_expands_user(self, monkeypatch):
        """Regression: a literal "~" in XDG_CACHE_HOME must expand. An
        unexpanded "~" survived into the registered model path and made
        Redownload write to a bogus "~" directory (Errno 2). Every
        cache_dir() branch must expanduser(), not just BPP_CACHE_DIR."""
        from bpp.utils.paths import cache_dir

        monkeypatch.setenv("XDG_CACHE_HOME", "~/.cache")
        result = cache_dir()
        assert "~" not in str(result), f"literal ~ leaked: {result}"
        assert result == Path.home() / ".cache" / "bpp"

    def test_bpp_cache_dir_overrides_everything(self, monkeypatch, tmp_path):
        """BPP_CACHE_DIR is the highest-precedence override — wins
        even when XDG_CACHE_HOME is also set."""
        from bpp.utils.paths import cache_dir

        monkeypatch.setenv("BPP_CACHE_DIR", str(tmp_path / "explicit"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        assert cache_dir() == tmp_path / "explicit"

    def test_bpp_cache_dir_expands_user(self, monkeypatch):
        from bpp.utils.paths import cache_dir

        monkeypatch.setenv("BPP_CACHE_DIR", "~/custom-cache")
        assert cache_dir() == Path.home() / "custom-cache"


class TestModelsDir:
    def test_default_is_cache_models(self):
        from bpp.utils.paths import models_dir

        assert models_dir() == Path.home() / ".cache" / "bpp" / "models"

    def test_inherits_xdg_cache_home(self, monkeypatch, tmp_path):
        from bpp.utils.paths import models_dir

        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert models_dir() == tmp_path / "bpp" / "models"

    def test_inherits_bpp_cache_dir(self, monkeypatch, tmp_path):
        from bpp.utils.paths import models_dir

        monkeypatch.setenv("BPP_CACHE_DIR", str(tmp_path / "cache"))
        assert models_dir() == tmp_path / "cache" / "models"

    def test_bpp_models_dir_overrides_everything(self, monkeypatch, tmp_path):
        """BPP_MODELS_DIR is its own escape hatch — wins over both
        BPP_CACHE_DIR and XDG_CACHE_HOME."""
        from bpp.utils.paths import models_dir

        monkeypatch.setenv("BPP_MODELS_DIR", str(tmp_path / "models"))
        monkeypatch.setenv("BPP_CACHE_DIR", str(tmp_path / "cache"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        assert models_dir() == tmp_path / "models"


class TestNoHardcodedCachePaths:
    """Source-scan: production code should resolve cache/models dirs
    via bpp.utils.paths, not bare ~/.cache/bpp literals. Catches
    accidental drift if a future contributor adds a new ML model and
    forgets to use the helper."""

    REPO_ROOT = Path(__file__).resolve().parent.parent

    # Allow-list:
    # - bpp/utils/paths.py — the implementation
    # - bpp/scoring/model_base.py — docstring example
    # - bpp/scoring/clip_embed.py / clip_tokenizer.py — module
    #   docstrings mention the path for documentation
    ALLOWED: ClassVar[set[str]] = {
        "bpp/utils/paths.py",
        "bpp/scoring/model_base.py",
        "bpp/scoring/clip_embed.py",
        "bpp/scoring/clip_tokenizer.py",
    }

    def test_no_bare_cache_bpp_literal_in_production(self):
        # Substring match below is sufficient — regex was unused.
        hits: list[tuple[Path, int, str]] = []
        for py in (self.REPO_ROOT / "bpp").rglob("*.py"):
            rel = str(py.relative_to(self.REPO_ROOT))
            if rel in self.ALLOWED:
                continue
            in_doc = False
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                # Skip inside docstrings + comments
                triple_count = line.count('"""') + line.count("'''")
                has_triple = '"""' in line or "'''" in line
                if has_triple:
                    if triple_count % 2 == 1:
                        in_doc = not in_doc
                    continue
                if in_doc:
                    continue
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                # Look for `.cache` followed eventually by `bpp` on the same line
                # Matches: .cache/bpp, ".cache" / "bpp", etc.
                if ".cache" in line and "bpp" in line:
                    hits.append((py, i, line.strip()))
        assert not hits, (
            "Production code must use bpp.utils.paths.cache_dir() / "
            "models_dir() instead of bare ~/.cache/bpp literals.\n"
            "Hits:\n" + "\n".join(f"  {p}:{ln} — {line}" for p, ln, line in hits)
        )
