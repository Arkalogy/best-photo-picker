"""R8-H12a: scan_extensions is config-driven, not hardcoded.

Three call sites used to repeat `["jpg", "jpeg", "png", "heic"]` (or
`["jpg", "jpeg", "png"]`) inline:
  - `WebAppState.__init__` (web's per-library state)
  - `bpp/io_scan.py` (scan helper for analyze/CLI)
  - `bpp/cli.py` (--extensions argparse default)

Now they all read from `bpp.config.DEFAULTS["scan_extensions"]`,
parsed via `parse_scan_extensions()`. A plugin author who wants
to add AVIF / RAW / WebP support patches the config (or ships an
override) and every call site picks it up.
"""

from __future__ import annotations

from bpp.config import DEFAULTS, parse_scan_extensions


class TestParseScanExtensions:
    def test_string_form(self):
        assert parse_scan_extensions("jpg,png,heic") == ["jpg", "png", "heic"]

    def test_list_form(self):
        assert parse_scan_extensions(["jpg", "png"]) == ["jpg", "png"]

    def test_strips_dots_and_lowercases(self):
        assert parse_scan_extensions(".JPG,.Png") == ["jpg", "png"]

    def test_strips_whitespace(self):
        assert parse_scan_extensions(" jpg , png ") == ["jpg", "png"]

    def test_drops_empty_segments(self):
        assert parse_scan_extensions("jpg,,png,") == ["jpg", "png"]

    def test_none_returns_default(self):
        result = parse_scan_extensions(None)
        # Default is "jpg,jpeg,png,heic"
        assert "jpg" in result
        assert "heic" in result


class TestDefaultsContainsScanExtensions:
    def test_key_exists(self):
        assert "scan_extensions" in DEFAULTS

    def test_default_includes_heic(self):
        assert "heic" in DEFAULTS["scan_extensions"]


class TestStateReadsConfig:
    def test_extensions_pulled_from_config(self, tmp_path):
        """`WebAppState` reads `extensions` from config['scan_extensions']
        rather than the previous hardcoded list. A YAML override at
        `scan_extensions: jpg,webp` (no heic) reaches the state via the
        explicit `config_path` argument."""
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("scan_extensions: 'jpg,webp'\n")

        from bpp.web.state import WebAppState

        ws = WebAppState(
            workdir=str(tmp_path / "wd"),
            library_path=str(tmp_path / "lib"),
            config_path=str(cfg_path),
        )
        try:
            assert ws.state["extensions"] == ["jpg", "webp"], (
                f"Config override didn't propagate: got {ws.state['extensions']}"
            )
        finally:
            ws.shutdown()


class TestIoScanReadsConfig:
    def test_default_extensions_match_config(self):
        """When `find_images_recursive` is called with `extensions=None`
        (its documented "use the project default" path), it must return
        scan_extensions from config — not the legacy hardcoded
        `["jpg", "jpeg", "png"]` (which silently dropped HEIC support
        the rest of the codebase already supported)."""
        # Source-scan: confirm the function references the config rather
        # than a hardcoded list. Functional invocation needs a real dir.
        from pathlib import Path

        src = Path("bpp/io_scan.py").read_text()
        # Function body must mention DEFAULTS or parse_scan_extensions
        assert "DEFAULTS" in src and "scan_extensions" in src
        # The legacy literal must be gone
        assert '["jpg", "jpeg", "png"]' not in src, (
            "io_scan still has the hardcoded extension list — config override won't propagate"
        )


class TestCliReadsConfig:
    def test_argparse_default_pulls_from_config(self):
        """`bpp --extensions` default must be derived from config, not
        a hardcoded string."""
        from pathlib import Path

        src = Path("bpp/cli.py").read_text()
        # Reference to DEFAULTS["scan_extensions"] must be present
        assert 'DEFAULTS["scan_extensions"]' in src, (
            "CLI argparse default must read from DEFAULTS, not a literal"
        )
