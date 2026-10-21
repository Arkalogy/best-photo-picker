"""Second-pass extensibility review (v0.1.3): a plugin smart album has a
public creation helper and documented callback signatures, non-smart_
types are refused at registration, a plugin export handler's failure is
per-photo, and the import worker fires post_analyze."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_ensure_smart_album_is_public_and_creates_the_row(tmp_path):
    from bpp.db.albums import list_albums
    from bpp.db.connection import init_db
    from bpp.db.photos import upsert_photo
    from bpp.db.smart_albums import ensure_smart_album

    conn = init_db(str(tmp_path / "t.db"))
    (tmp_path / "a.jpg").write_bytes(b"x")
    pid = upsert_photo(conn, {"filepath": str(tmp_path / "a.jpg")})
    ensure_smart_album(
        conn, name="High confidence", album_type="smart_high", rule={}, photo_ids=[pid]
    )
    albums = [a for a in list_albums(conn) if a["album_type"] == "smart_high"]
    assert len(albums) == 1 and albums[0]["photo_count"] == 1


def test_a_plugin_album_type_must_be_smart_prefixed():
    from bpp.db.smart_albums import SmartAlbumRegistry

    with pytest.raises(ValueError, match="smart_"):
        SmartAlbumRegistry.register("myplugin_kind", lambda conn: None, lambda conn, rule: [])


def test_plugin_examples_use_the_documented_signatures():
    example = (REPO_ROOT / "bpp" / "plugins" / "example.py").read_text()
    assert "_ensure_smart_album" not in example
    assert "ensure_smart_album" in example
    demo = (
        REPO_ROOT / "examples" / "plugin_example" / "bpp_example_plugin" / "plugin.py"
    ).read_text()
    assert "lambda conn, config" not in demo
    docs = (REPO_ROOT / "docs" / "plugins.md").read_text()
    assert "refresh_fn(conn)" in docs and "get_ids_fn(conn, rule)" in docs
    assert "ensure_smart_album(" in docs


def test_a_plugin_export_handler_failure_is_per_photo(tmp_path):
    from PIL import Image

    from bpp.output.export import export_selected
    from bpp.output.export_modes import ExportModeRegistry, register_export_mode

    def handler(src, dest, **kw):
        if src.endswith("bad.jpg"):
            raise RuntimeError("missing credentials")
        Image.open(src).save(dest)

    register_export_mode("myplugin_s3", handler, description="S3", replace=True)
    try:
        lib = tmp_path / "lib"
        lib.mkdir()
        items = []
        for name in ("good.jpg", "bad.jpg", "also.jpg"):
            Image.new("RGB", (8, 8)).save(lib / name, "JPEG")
            items.append({"filepath": str(lib / name), "aggregate_score": 0.5})
        result = export_selected(
            items, items, str(tmp_path / "out"), mode="myplugin_s3", library_path=str(lib)
        )
    finally:
        ExportModeRegistry._reset_for_tests() if hasattr(
            ExportModeRegistry, "_reset_for_tests"
        ) else None
    assert len(result.failures) == 1 and "missing credentials" in result.failures[0]["error"]
    assert result.exported == 2


def test_import_worker_fires_post_analyze():
    src = (REPO_ROOT / "bpp" / "web" / "import_worker.py").read_text()
    assert "dispatch_post_analyze(conn, valid)" in src


def test_docs_match_the_code():
    docs = (REPO_ROOT / "docs" / "plugins.md").read_text()
    assert (
        "attaching CLI\n        subcommands" not in docs and "attaching CLI subcommands" not in docs
    )
    assert "ships four modes" in docs or "`copy`, `hardlink`, `symlink` and `zip`" in docs
    assert "any subset of the four" not in docs and "All four methods" not in docs
    assert "'near_duplicate'" in docs
    assert "example.py:175-185" not in docs
    assert "`sensitive`" in docs
    proto = (REPO_ROOT / "bpp" / "plugin_protocol.py").read_text()
    assert "attaching\n  CLI subcommands" not in proto and "attaching CLI subcommands" not in proto
    registry = (REPO_ROOT / "bpp" / "scoring" / "registry.py").read_text()
    assert "RECOMPUTE_WEIGHT_KEYS" not in registry
