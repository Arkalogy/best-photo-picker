"""The synchronous export endpoint validates through the same helper as
the streaming one, so the two can't drift."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_sync_export_calls_the_shared_validator():
    src = (REPO_ROOT / "bpp" / "web" / "bp_export.py").read_text()
    body = src[src.index("def api_export(") : src.index("def _validate_export_params(")]
    assert "_validate_export_params(" in body
    assert 'params.get("fmt"' not in body, "inline fmt validation duplicates the helper"
