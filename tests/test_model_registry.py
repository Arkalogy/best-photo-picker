"""ModelRegistry consistency tests.

Each scoring module registers its entry on import; bp_core's
redownload + uninstall endpoints look up via
`ModelRegistry.get(name)`.

These tests pin three invariants:

1. Every name the JS sends as a redownload / uninstall key
   resolves through the registry. JS keys come from
   `models_status._file_info(name=...)` calls.
2. Every registry entry has a non-empty path. Empty path = the
   redownload endpoint will write nowhere.
3. Every registry entry has a sha256. Without one, downloads
   can't be integrity-checked.

Plus a smoke test that `_reset_model_cache` is callable for
every registered name without raising.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _populate_registry() -> None:
    """Force-import every scoring module that should register itself."""
    from bpp.web.bp_models import _ensure_model_modules_imported

    _ensure_model_modules_imported()


def test_registry_has_expected_entries():
    """Pin the set of registered model names. New entries are
    welcome; this test fails when a name is dropped accidentally."""
    _populate_registry()
    from bpp.scoring.model_base import ModelRegistry

    expected = {
        "BlazeFace short-range",
        "BlazeFace full-range",
        "YuNet (primary)",
        "SFace recognition",
        "FaceLandmarker",
        "HandLandmarker",
        "PoseLandmarker",
        "Selfie segmenter",
        "SCRFD 2.5g",
        "CLIP visual",
        "CLIP text",
        "CLIP vocabulary",
        "YOLO pet detector",
    }
    actual = set(ModelRegistry.names())
    missing = expected - actual
    assert not missing, f"Missing registry entries: {missing}"


def test_each_entry_has_required_fields():
    _populate_registry()
    from bpp.scoring.model_base import ModelRegistry

    for entry in ModelRegistry.all():
        assert entry.name, f"empty name in {entry!r}"
        assert entry.path, f"{entry.name}: empty path"
        assert entry.sha256, f"{entry.name}: missing sha256 (integrity check)"
        # url can be None for bundled-in-wheel models, but every
        # current registered entry has a download URL
        assert entry.url, f"{entry.name}: missing url"
        assert callable(entry.reset), f"{entry.name}: reset is not callable"


def test_models_status_names_match_registry():
    """Static-scan `bpp/web/models_status.py` for every
    `_file_info("<name>", ...)` call and assert each first-arg name
    is registered. JS sends those names back to the server as the
    redownload / uninstall key — drift here breaks the UI."""
    _populate_registry()
    from bpp.scoring.model_base import ModelRegistry

    text = (REPO_ROOT / "bpp" / "web" / "models_status.py").read_text()
    # `_file_info("Some Name", ...)` — capture the first string arg
    pattern = re.compile(r'_file_info\(\s*"([^"]+)"')
    ui_names = set(pattern.findall(text))

    # Filter to names that are also redownloadable models (not pure
    # bundled-not-tracked entries). We want every UI name to be in
    # the registry — bundled-only models like the BlazeFace short-
    # range ALSO register because they have a download fallback.
    registered = set(ModelRegistry.names())
    missing = ui_names - registered
    assert not missing, (
        f"models_status.py exposes UI names that aren't in the "
        f"ModelRegistry: {sorted(missing)}. Either register them "
        f"in their scoring module, OR (if they're documentation-"
        f"only entries) add an exception list to this test."
    )


def test_reset_callable_for_every_entry():
    """Smoke-test that every registered reset() can be called. The
    actual reset behavior is per-singleton; we only confirm no
    AttributeError / NameError at call time. Lazy bugs in module-
    global resetters (typo'd globals, stale closures) surface here
    rather than during a live redownload."""
    _populate_registry()
    from bpp.scoring.model_base import ModelRegistry

    for entry in ModelRegistry.all():
        # Reset is idempotent — safe to call repeatedly without
        # actually downloading anything.
        entry.reset()


def test_no_resolved_model_path_contains_literal_tilde():
    """Regression: every model path the redownload/uninstall endpoints
    resolve must be expanduser'd. A literal "~" reaches makedirs()/open()
    unexpanded and the download fails with Errno 2 (writes to a bogus "~"
    dir). The loader expanded elsewhere, so this only bit Redownload —
    invisible to render-level tests."""
    _populate_registry()
    from bpp.scoring.model_base import ModelRegistry
    from bpp.web.bp_models import _model_path_url_sha

    for name in ModelRegistry.names():
        path, _url, _sha = _model_path_url_sha(name)
        assert "~" not in path, f"{name}: literal ~ in resolved path {path!r}"


def test_redownload_model_expands_tilde_path(monkeypatch, tmp_path):
    """End-to-end-ish: an entry whose path carries a literal "~" must
    download to the expanded location, never a "~" directory. Mocks the
    network so only the path handling is exercised."""
    import os

    from bpp.scoring.model_base import ModelEntry, ModelRegistry
    from bpp.web import bp_models

    monkeypatch.setenv("HOME", str(tmp_path))  # expanduser("~") → tmp_path
    ModelRegistry.register(
        ModelEntry(
            name="FAKE tilde model",
            path="~/bpp_redl/fake.onnx",
            url="https://example.invalid/fake.onnx",
            sha256="0" * 64,
            reset=lambda: None,
        ),
        replace=True,
    )

    captured = {}

    def _fake_download(url, dest, *, registry_id, timeout=120, sha256=None):
        captured["dest"] = dest
        captured["registry_id"] = registry_id
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(b"fake-weights")

    monkeypatch.setattr("bpp.utils.download.download_file", _fake_download)
    try:
        bp_models._redownload_model("FAKE tilde model")
    finally:
        ModelRegistry._entries.pop("FAKE tilde model", None)

    assert "~" not in captured["dest"], f"literal ~ reached FS: {captured['dest']}"
    expected = tmp_path / "bpp_redl" / "fake.onnx"
    assert expected.exists(), f"weights not at expanded path {expected}"
    assert not (tmp_path.parent / "~").exists()


def test_resolve_via_bp_core_helper():
    """Pin the public surface that bp_core exposes for redownload /
    uninstall. Tests must keep working through the registry
    indirection."""
    _populate_registry()
    from bpp.web.bp_models import _model_path_url_sha

    path, url, sha = _model_path_url_sha("BlazeFace short-range")
    assert path
    assert url.startswith("https://")
    assert sha and len(sha) == 64

    # Unknown name still raises ValueError
    import pytest

    with pytest.raises(ValueError, match="Unknown model"):
        _model_path_url_sha("nonexistent model")


def test_reset_model_cache_silently_ignores_unknown():
    """The legacy if/elif chain silently no-op'd on unknown names
    (it just fell off the end). The registry path matches."""
    _populate_registry()
    from bpp.web.bp_models import _reset_model_cache

    # Should not raise
    _reset_model_cache("definitely not a model")
