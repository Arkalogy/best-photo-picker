"""Regression tests for Round 7 hardening fixes."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np


def test_clip_embeddings_skip_malformed_blob(tmp_path):
    from bpp.web.app import create_app

    app = create_app(workdir=str(tmp_path))
    ctx = app.extensions["bpp"]
    with app.app_context():
        conn = ctx.get_conn()
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, file_size, file_mtime) "
            "VALUES (?, ?, ?, ?)",
            (str(tmp_path / "ok.jpg"), "ok.jpg", 1, 1.0),
        )
        conn.execute(
            "INSERT INTO photos (filepath, original_filename, file_size, file_mtime) "
            "VALUES (?, ?, ?, ?)",
            (str(tmp_path / "bad.jpg"), "bad.jpg", 1, 1.0),
        )
        conn.execute(
            "INSERT INTO clip_embeddings (photo_id, model_name, embedding) VALUES (?, ?, ?)",
            (1, "ViT-B-32", np.zeros(512, dtype=np.float32).tobytes()),
        )
        conn.execute(
            "INSERT INTO clip_embeddings (photo_id, model_name, embedding) VALUES (?, ?, ?)",
            (2, "ViT-B-32", np.zeros(1, dtype=np.float32).tobytes()),
        )
        conn.commit()

        embs = ctx.load_clip_embeddings()

        assert 1 in embs
        assert 2 not in embs
        assert embs[1].shape == (512,)
        with ctx.lock:
            assert ctx.clip_cache["matrix"].shape == (1, 512)


def test_zip_member_count_bomb_rejected_before_extract(tmp_path, monkeypatch):
    # Archive constants moved to bpp.web.analyze_archive during the v0.1 split.
    from bpp.web import analyze_archive
    from bpp.web.analyze_worker import AnalyzeWorker

    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for i in range(101):
            zf.writestr(f"empty/{i}.txt", "")

    monkeypatch.setattr(analyze_archive, "_MAX_ARCHIVE_MEMBERS", 100)

    events = []
    worker = AnalyzeWorker()
    monkeypatch.setattr(worker, "_emit", lambda event: events.append(event))

    worker._run(
        input_dir=str(archive),
        workdir=str(tmp_path / "work"),
        config={},
        extensions=[".jpg", ".jpeg", ".png"],
        recursive=True,
    )

    assert any(
        e.get("type") == "error" and "too many" in e.get("message", "").lower() for e in events
    )
    assert not (tmp_path / "work" / "extracted" / "empty" / "100.txt").exists()


def test_tauri_webview_does_not_expose_shell_plugin_to_js():
    caps = json.loads(
        Path("desktop/src-tauri/capabilities/default.json").read_text(encoding="utf-8")
    )

    flat = []
    for perm in caps["permissions"]:
        if isinstance(perm, str):
            flat.append(perm)
        elif isinstance(perm, dict):
            flat.append(perm.get("identifier"))

    forbidden = {
        "shell:allow-open",
        "shell:allow-execute",
        "shell:allow-spawn",
        "shell:allow-stdin-write",
        "shell:allow-kill",
    }
    assert forbidden.isdisjoint(set(flat))


def test_tauri_conf_targets_match_advertised_platform():
    """R9-claims-M1: README:18 advertises a "native macOS app" but
    tauri.conf.json bundle.targets used to be "all" (mac/win/linux).
    The sidecar binary is macOS-only (`bpp-server-aarch64-apple-darwin`),
    the build instructions are macOS-only, and the signing notes are
    macOS-specific — so "all" was a build-matrix lie.

    Pin targets to macOS bundle types only so a future contributor who
    flips `bundle.targets = "all"` and starts shipping Windows/Linux
    builds without verifying them at least has to update this test
    (and the README) deliberately."""
    conf = json.loads(Path("desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    targets = conf.get("bundle", {}).get("targets")
    assert targets != "all", (
        "bundle.targets='all' contradicts the README's macOS-only claim "
        "and the macOS-only sidecar binary. Pin to ['dmg', 'app'] "
        "until cross-platform builds are actually verified in CI."
    )
    if isinstance(targets, list):
        # Defensive: if a future cross-platform release happens, this
        # test should be updated alongside the README.
        for t in targets:
            assert t in ("dmg", "app"), (
                f"Unexpected bundle target {t!r} — if you're adding "
                "Windows / Linux support, update README's 'native "
                "macOS app' claim and add a CI gate that builds them."
            )


def test_tauri_invoked_commands_are_registered():
    """R10-L1: every command name the webview invokes must appear in
    `tauri::generate_handler!` on the Rust side. Pre-fix the injected
    disconnect-overlay called `__TAURI__?.invoke('exit_app')` against
    a command that was never registered — the Quit button was a
    silent no-op.

    Source-scan: regex out every webview-side
    `__TAURI__?.core?.invoke('NAME')` and confirm `NAME` is named in
    the handler list."""
    import re

    rust_full = Path("desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
    # Strip line comments before scanning — explanatory `// the old
    # form was __TAURI__?.invoke(...)` text in source comments would
    # otherwise trip the regex.
    rust = "\n".join(line for line in rust_full.splitlines() if not line.strip().startswith("//"))

    handler_match = re.search(
        r"tauri::generate_handler!\[([^\]]+)\]",
        rust,
    )
    assert handler_match, "Could not locate tauri::generate_handler! in main.rs"
    registered = {name.strip() for name in handler_match.group(1).split(",") if name.strip()}

    # `__TAURI__?.invoke(...)` is the v1 / wrong shape — Tauri v2
    # routes through `core.invoke`. Catch the legacy form too so a
    # future contributor copy-pasting an old snippet gets caught.
    invoked_v2 = set(
        re.findall(r"__TAURI__\??\.core\??\.invoke\(\\?['\"]([a-zA-Z_][\w]*)\\?['\"]", rust)
    )
    invoked_v1_legacy = set(
        re.findall(r"__TAURI__\??\.invoke\(\\?['\"]([a-zA-Z_][\w]*)\\?['\"]", rust)
    )
    assert not invoked_v1_legacy, (
        f"Legacy `__TAURI__.invoke(...)` call shape found for "
        f"{sorted(invoked_v1_legacy)} — Tauri v2 requires "
        "`__TAURI__.core.invoke(...)`."
    )

    missing = invoked_v2 - registered
    assert not missing, (
        f"Webview invokes commands {sorted(missing)} that are NOT in "
        f"tauri::generate_handler! (registered: {sorted(registered)}). "
        "Either register the command or remove the invoke."
    )


def test_tauri_conf_uses_official_schema():
    """R9-fr-H3: the `$schema` reference must point at the official
    Tauri schema (`https://schema.tauri.app/config/2`) so IDE schema
    completion and pre-commit JSON validators flag bad keys.

    A previous snapshot referenced `nicegui-org/nicegui`'s mirror —
    NiceGUI is an unrelated Python web framework whose schema URL
    happened to come up in a tutorial. With that URL, IDEs validate
    the config against the wrong file and silently miss typos in
    Tauri-specific keys."""
    conf_text = Path("desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    conf = json.loads(conf_text)

    schema = conf.get("$schema", "")
    assert "schema.tauri.app" in schema, (
        f"$schema should point to the official Tauri schema; got {schema!r}. "
        "Use `https://schema.tauri.app/config/2`."
    )
    assert "nicegui" not in schema.lower(), (
        "$schema must not point at NiceGUI's mirror — that's an unrelated project's schema URL."
    )


def test_tauri_conf_sets_explicit_csp():
    """R8-M2: defense-in-depth CSP. Flask sets the canonical
    Content-Security-Policy via HTTP header on every HTML response,
    but Tauri also injects a meta-CSP for paths where Flask isn't
    serving (Rust-side error screens, future bundled-assets mode).
    `csp: null` would leave those paths unprotected; this test pins
    a non-null value with the directives that matter for the SPA."""
    conf = json.loads(Path("desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))

    csp = conf.get("app", {}).get("security", {}).get("csp")
    assert csp is not None and csp != "", (
        "tauri.conf.json must set app.security.csp explicitly. "
        "`csp: null` leaves the webview unprotected on Flask-less "
        "render paths (Rust-side error screens, bundled assets)."
    )

    # Required directives for the SPA + map tiles
    required_directives = [
        "default-src 'self'",
        "script-src",
        "style-src",
        "img-src",
        "connect-src",
        "frame-src 'none'",
        "object-src 'none'",
    ]
    for directive in required_directives:
        assert directive in csp, f"CSP missing {directive!r}. Got: {csp}"

    # The OSM tile origin must be in img-src (already an opt-in
    # network feature documented in README). Without this the map
    # view would render gray squares instead of map tiles.
    assert "https://*.tile.openstreetmap.org" in csp, (
        "CSP must allow OSM tile imagery for the map view"
    )
