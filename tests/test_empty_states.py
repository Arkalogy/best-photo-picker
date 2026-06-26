"""Regression tests for empty-state layout in deleted/hidden views.

Ensures:
1. navigateToDeleted() and navigateToHidden() hide the timeline bar
2. .empty-state CSS spans full grid width (grid-column: 1 / -1)
"""

from __future__ import annotations

import re
from pathlib import Path

JS_DIR = Path(__file__).resolve().parent.parent / "bpp" / "web" / "static" / "js"
CSS_DIR = Path(__file__).resolve().parent.parent / "bpp" / "web" / "static" / "css"


class TestTimelineHiddenInEmptyViews:
    """navigateToDeleted/Hidden must hide the timeline bar."""

    def test_navigate_to_deleted_hides_timeline(self):
        src = (JS_DIR / "modules" / "deleted.mjs").read_text()
        # Extract the navigateToDeleted function body
        match = re.search(
            r"function\s+navigateToDeleted\s*\([^)]*\)\s*\{(.*?)^}",
            src,
            re.DOTALL | re.MULTILINE,
        )
        assert match, "navigateToDeleted() not found in deleted.js"
        body = match.group(1)
        assert ('hide("timeline-bar")' in body) or ('hide?.("timeline-bar")' in body), (
            'navigateToDeleted() must call hide("timeline-bar") '
            "to prevent the timeline from showing on an empty deleted view"
        )

    def test_navigate_to_hidden_hides_timeline(self):
        src = (JS_DIR / "modules" / "deleted.mjs").read_text()
        match = re.search(
            r"function\s+navigateToHidden\s*\([^)]*\)\s*\{(.*?)^}",
            src,
            re.DOTALL | re.MULTILINE,
        )
        assert match, "navigateToHidden() not found in deleted.js"
        body = match.group(1)
        assert ('hide("timeline-bar")' in body) or ('hide?.("timeline-bar")' in body), (
            'navigateToHidden() must call hide("timeline-bar") '
            "to prevent the timeline from showing on an empty hidden view"
        )


class TestEmptyStateCSS:
    """Empty state must span the full grid so it centers properly."""

    def test_empty_state_spans_full_grid(self):
        css = (CSS_DIR / "app.css").read_text()
        # Find the .empty-state rule block
        match = re.search(r"\.empty-state\s*\{([^}]+)\}", css)
        assert match, ".empty-state rule not found in app.css"
        rule = match.group(1)
        assert "grid-column" in rule, (
            ".empty-state must have grid-column: 1 / -1 "
            "to span the full photo grid width instead of squishing into one column"
        )
