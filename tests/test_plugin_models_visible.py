"""Models registered by plugins appear in the pre-analysis download list
and in Settings → Models, as docs/plugins.md promises."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bpp.scoring.model_base import ModelEntry, ModelRegistry

_NAME = "Plugin test model"


@pytest.fixture
def plugin_model(tmp_path):
    def _register(legal_id="openai_clip_vit_b32_onnx", present=False):
        path = tmp_path / "plugin.onnx"
        if present:
            path.write_bytes(b"x" * 2048)
        ModelRegistry.register(
            ModelEntry(
                name=_NAME,
                path=str(path),
                url="https://models.example.org/plugin.onnx",
                sha256="0" * 64,
                reset=lambda: None,
                legal_id=legal_id,
            ),
            replace=True,
            builtin=False,
        )
        return path

    yield _register
    ModelRegistry._entries.pop(_NAME, None)
    ModelRegistry._plugin.discard(_NAME)


def test_builtin_models_are_not_listed_as_plugins():
    from bpp.scoring.model_base import plugin_model_entries
    from bpp.web.bp_models import _ensure_model_modules_imported

    _ensure_model_modules_imported()
    assert [e.name for e in plugin_model_entries()] == []


def test_missing_plugin_model_is_in_the_download_list(plugin_model):
    from bpp.web.model_filter import compute_pending_and_blocked

    plugin_model()
    items, _ = compute_pending_and_blocked()
    item = next(i for i in items if i["name"] == _NAME)
    assert item["host"] == "models.example.org"
    assert item["legal_entry_id"] == "openai_clip_vit_b32_onnx"
    assert item["needs_acceptance"] is False


@pytest.mark.no_preaccept_permissive
def test_restricted_plugin_model_waits_for_its_license(plugin_model):
    from bpp.web.model_filter import compute_pending_and_blocked

    plugin_model(legal_id="insightface_scrfd_25g")
    items, blocked = compute_pending_and_blocked()
    assert all(i["name"] != _NAME for i in items)
    assert any(b["name"] == _NAME for b in blocked)


def test_present_plugin_model_is_not_pending(plugin_model):
    from bpp.web.model_filter import compute_pending_and_blocked

    plugin_model(present=True)
    items, blocked = compute_pending_and_blocked()
    assert all(i["name"] != _NAME for i in items + blocked)


def test_plugin_model_shows_in_settings_models(plugin_model):
    from bpp.web.models_status import build_model_features

    plugin_model(present=True)
    feats = build_model_features(SimpleNamespace(config={}))
    feat = next(f for f in feats if f["label"] == f"Plugin model: {_NAME}")
    assert feat["status"] == "ready"
    assert feat["files"][0]["name"] == _NAME
    assert feat["size_bytes"] == 2048


def test_plugin_model_can_be_redownloaded_by_name(plugin_model):
    from bpp.web import bp_models

    path = plugin_model()
    assert bp_models._model_path_url_sha(_NAME) == (
        str(path),
        "https://models.example.org/plugin.onnx",
        "0" * 64,
    )
    assert bp_models._resolve_legal_id(_NAME) == "openai_clip_vit_b32_onnx"


@pytest.mark.parametrize(
    ("url", "legal_id", "sha"),
    [
        (None, "openai_clip_vit_b32_onnx", "0" * 64),
        ("https://x.org/m.onnx", None, "0" * 64),
        ("https://x.org/m.onnx", "no_such_registry_entry", "0" * 64),
        ("https://x.org/m.onnx", "openai_clip_vit_b32_onnx", None),
    ],
    ids=["no-url", "no-legal-id", "unknown-legal-id", "no-sha256"],
)
def test_plugin_file_bpp_cannot_fetch_offers_no_download(tmp_path, url, legal_id, sha):
    from bpp.web.models_status import build_model_features

    ModelRegistry.register(
        ModelEntry(
            name=_NAME,
            path=str(tmp_path / "m.onnx"),
            url=url,
            sha256=sha,
            reset=lambda: None,
            legal_id=legal_id,
        ),
        replace=True,
        builtin=False,
    )
    try:
        feat = next(
            f
            for f in build_model_features(SimpleNamespace(config={}))
            if f["label"] == f"Plugin model: {_NAME}"
        )
        assert feat["no_download"] is True  # no Download, not in "Download all"
        assert "plugin" in feat["download_hint"].lower()
        assert "lib_only" not in feat  # Uninstall stays available
        from bpp.web.model_filter import compute_pending_and_blocked

        items, blocked = compute_pending_and_blocked()
        assert all(i["name"] != _NAME for i in items + blocked)  # prompt agrees
    finally:
        ModelRegistry._entries.pop(_NAME, None)
        ModelRegistry._plugin.discard(_NAME)


def test_plugin_model_with_bundled_weights_is_not_a_download(tmp_path):
    from bpp.scoring.model_base import ModelSingleton
    from bpp.web.model_filter import compute_pending_and_blocked
    from bpp.web.models_status import build_model_features

    bundled = tmp_path / "bundled.onnx"
    bundled.write_bytes(b"w")
    single = ModelSingleton(
        name=_NAME,
        model_path=tmp_path / "cache.onnx",
        model_url="https://x.org/m.onnx",
        create_fn=lambda p: object(),
        registry_id="openai_clip_vit_b32_onnx",
        bundled_path=str(bundled),
    )
    ModelRegistry.register(
        ModelEntry(
            name=_NAME,
            path=str(tmp_path / "cache.onnx"),
            url="https://x.org/m.onnx",
            sha256="0" * 64,
            reset=single.reset,
        ),
        replace=True,
        builtin=False,
    )
    try:
        items, blocked = compute_pending_and_blocked()
        assert all(i["name"] != _NAME for i in items + blocked)
        feat = next(
            f
            for f in build_model_features(SimpleNamespace(config={}))
            if f["label"] == f"Plugin model: {_NAME}"
        )
        assert feat["status"] == "ready"
        assert feat["bundled"] is True
        assert feat["files"][0]["exists"] is False  # nothing cached: no Uninstall
    finally:
        ModelRegistry._entries.pop(_NAME, None)
        ModelRegistry._plugin.discard(_NAME)


def test_reset_for_tests_clears_plugin_marks():
    snapshot = (dict(ModelRegistry._entries), set(ModelRegistry._plugin))
    try:
        ModelRegistry._reset_for_tests()
        assert ModelRegistry._plugin == set()
    finally:
        ModelRegistry._entries.update(snapshot[0])
        ModelRegistry._plugin.update(snapshot[1])


def test_plugin_cannot_take_over_a_builtin_model_name():
    from bpp.web.bp_models import _ensure_model_modules_imported

    _ensure_model_modules_imported()
    real = ModelRegistry.get("SFace recognition")
    with pytest.raises(ValueError, match="built-in"):
        ModelRegistry.register(
            ModelEntry(
                name="SFace recognition",
                path="/tmp/x",
                url="https://evil.example/x",
                sha256=None,
                reset=lambda: None,
            ),
            replace=True,
            builtin=False,
        )
    assert ModelRegistry.get("SFace recognition") is real


def test_bpp_module_imported_by_a_plugin_stays_builtin():
    """A plugin importing a bpp scoring module for the first time must not
    turn bpp's own files into plugin files."""
    import importlib

    import bpp.scoring.segmentation as seg
    from bpp.scoring.model_base import plugin_model_entries

    # Its file path is read from the model cache dir at registration;
    # an earlier test may have pointed that elsewhere, and re-registering
    # a name at another path is refused. Only the origin matters here.
    ModelRegistry._entries.pop("Selfie segmenter", None)
    importlib.reload(seg)  # bpp's own module registers again
    assert "Selfie segmenter" not in {e.name for e in plugin_model_entries()}


def test_registration_from_outside_bpp_is_a_plugin_file(tmp_path):
    """A plugin's module (any module outside the ``bpp`` package) — at
    import, in setup(), or later in a hook — registers plugin files."""
    from bpp.scoring.model_base import plugin_model_entries

    ModelRegistry.register(
        ModelEntry(
            name="file from a plugin module",
            path=str(tmp_path / "f"),
            url=None,
            sha256=None,
            reset=lambda: None,
        ),
        replace=True,
    )
    try:
        assert "file from a plugin module" in {e.name for e in plugin_model_entries()}
    finally:
        ModelRegistry._entries.pop("file from a plugin module", None)
        ModelRegistry._plugin.discard("file from a plugin module")


@pytest.mark.no_preaccept_permissive
def test_license_pending_plugin_row_waits_for_acceptance(plugin_model):
    from bpp.web.models_status import build_model_features

    plugin_model(legal_id="insightface_scrfd_25g")
    feat = next(
        f
        for f in build_model_features(SimpleNamespace(config={}))
        if f["label"] == f"Plugin model: {_NAME}"
    )
    assert feat["no_download"] is True
    assert "license" in feat["download_hint"].lower()


@pytest.mark.no_preaccept_permissive
def test_plugin_download_size_comes_from_its_registry_entry(plugin_model):
    from bpp.web.model_filter import compute_pending_and_blocked

    plugin_model(legal_id="insightface_scrfd_25g")  # declares 3,291,737 bytes
    _, blocked = compute_pending_and_blocked()
    item = next(b for b in blocked if b["name"] == _NAME)
    assert item["size_mb"] == pytest.approx(3.1, abs=0.1)


def test_plugin_cannot_claim_a_builtin_name_before_its_module_loads():
    """In a fresh process (as at server start, before NudeNet's module is
    imported) a plugin registering "NudeNet 320n" must be refused, and the
    real module must still import."""
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent(
        """
        import sys
        from bpp.scoring.model_lifecycle import ModelEntry, ModelRegistry
        assert "bpp.scoring.nudity" not in sys.modules
        try:
            ModelRegistry.register(ModelEntry(name="NudeNet 320n", path="/tmp/x",
                url="https://evil.example/x", sha256=None, reset=lambda: None))
        except ValueError as e:
            assert "built-in" in str(e), e
        else:
            raise SystemExit("plugin took a built-in name")
        import bpp.scoring.nudity
        assert ModelRegistry.get("NudeNet 320n").url != "https://evil.example/x"
        print("ok")
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().endswith("ok"), out.stderr[-2000:]


@pytest.mark.no_preaccept_permissive
def test_blocked_plugin_model_is_flagged_for_the_prompt(plugin_model):
    from bpp.web.model_filter import compute_pending_and_blocked

    plugin_model(legal_id="insightface_scrfd_25g")
    _, blocked = compute_pending_and_blocked()
    item = next(b for b in blocked if b["name"] == _NAME)
    assert item["from_plugin"] is True


def test_withdrawn_plugin_entry_offers_no_download(plugin_model):
    import dataclasses

    from bpp.registry import get_entry, register_entry
    from bpp.registry.model_registry import _REGISTRY, ModelStatus
    from bpp.web.models_status import build_model_features

    withdrawn = dataclasses.replace(
        get_entry("openai_clip_vit_b32_onnx"),
        id="withdrawn_plugin_entry",
        status=ModelStatus.WITHDRAWN_NO_NEW_DOWNLOADS,
    )
    register_entry(withdrawn)
    try:
        plugin_model(legal_id="withdrawn_plugin_entry")
        feat = next(
            f
            for f in build_model_features(SimpleNamespace(config={}))
            if f["label"] == f"Plugin model: {_NAME}"
        )
        assert feat["no_download"] is True
        assert "no longer" in feat["download_hint"]
    finally:
        _REGISTRY._store.pop("withdrawn_plugin_entry", None)


@pytest.mark.no_preaccept_permissive
def test_blocked_plugin_model_of_a_covered_kind_is_still_listed(plugin_model):
    """Search is covered by the built-in CLIP, but a plugin's search model
    waiting on its license must still be shown, not silently dropped."""
    import dataclasses

    from bpp.registry import get_entry, register_entry
    from bpp.registry.model_registry import _REGISTRY
    from bpp.web.model_filter import compute_pending_and_blocked

    register_entry(
        dataclasses.replace(
            get_entry("insightface_scrfd_25g"), id="plugin_search_entry", kind="semantic_search"
        )
    )
    try:
        plugin_model(legal_id="plugin_search_entry")
        _, blocked = compute_pending_and_blocked()
        assert any(b["name"] == _NAME and b["from_plugin"] for b in blocked)
    finally:
        _REGISTRY._store.pop("plugin_search_entry", None)


def test_builtin_model_module_list_is_complete():
    """Every bpp module that registers a model file is in
    BUILTIN_MODEL_MODULES (else a plugin could pre-claim its names)."""
    import pathlib
    import subprocess
    import sys
    import textwrap

    root = pathlib.Path(__file__).resolve().parent.parent
    modules = sorted(
        "bpp.scoring." + p.stem
        for p in (root / "bpp" / "scoring").glob("*.py")
        if "ModelRegistry.register(" in p.read_text(encoding="utf-8")
        and p.stem != "model_lifecycle"
    )
    code = textwrap.dedent(
        f"""
        import importlib
        from bpp.scoring.model_lifecycle import ModelRegistry, ensure_builtin_model_files_registered
        ensure_builtin_model_files_registered()
        before = set(ModelRegistry.names())
        for m in {modules!r}:
            importlib.import_module(m)
        grew = set(ModelRegistry.names()) - before
        print("grew:", sorted(grew))
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert "grew: []" in out.stdout, out.stdout + out.stderr[-1500:]
