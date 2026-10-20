"""Source-scanning regression tests for JS/HTML/CSS — part 1 of 3."""

from __future__ import annotations

import re
from typing import ClassVar

import pytest

from tests._js_source_scan_shared import (
    JS_DIR,
    TEMPLATE_DIR,
    _all_js_content,
    _all_js_function_defs,
    _modules_blob,
    _read,
)

# ---------------------------------------------------------------------------
# Progress display: sidebar widget must stay removed
# ---------------------------------------------------------------------------


class TestProgressConsolidation:
    """Sidebar progress section was removed in 3eb6336.

    All progress display goes through the status bar (showStatusProgress /
    hideStatusProgress).  These tests prevent the sidebar widget from
    being accidentally re-introduced.
    """

    def test_no_progress_section_in_html(self):
        """index.html must not contain #progress-section."""
        html = _read(TEMPLATE_DIR / "index.html")
        assert "progress-section" not in html

    def test_no_sidebar_progress_ids_in_js(self):
        """JS must not reference the removed sidebar progress element IDs.

        The status bar uses 'status-progress-*' IDs and CLIP uses
        'clip-progress-*' — those are fine.  Only the old bare sidebar
        IDs (progress-section, progress-title, progress-fill,
        progress-text) used via getElementById are banned.
        """
        banned = [
            '"progress-section"',
            '"progress-title"',
            '"progress-fill"',
            '"progress-text"',
        ]
        for js_file in (JS_DIR / "modules").glob("*.mjs"):
            content = _read(js_file)
            for bid in banned:
                assert bid not in content, (
                    f"{js_file.name} still references removed sidebar element {bid}"
                )

    def test_no_btn_cancel_in_html(self):
        """Sidebar cancel button was removed; status bar has its own."""
        html = _read(TEMPLATE_DIR / "index.html")
        assert 'id="btn-cancel"' not in html

    def test_no_resetCancelButton_in_js(self):
        """resetCancelButton() was removed with the sidebar progress."""
        for js_file in (JS_DIR / "modules").glob("*.mjs"):
            content = _read(js_file)
            assert "resetCancelButton" not in content, (
                f"{js_file.name} still references removed resetCancelButton"
            )

    def test_status_bar_progress_exists(self):
        """Status bar progress elements must exist in index.html."""
        html = _read(TEMPLATE_DIR / "index.html")
        assert 'id="status-progress"' in html
        assert 'id="status-progress-text"' in html
        assert 'id="status-progress-fill"' in html

    def test_showStatusProgress_defined(self):
        """showStatusProgress() must be defined in analysis.js."""
        content = _modules_blob()
        assert "function showStatusProgress" in content

    def test_hideStatusProgress_defined(self):
        """hideStatusProgress() must be defined in analysis.js."""
        content = _modules_blob()
        assert "function hideStatusProgress" in content


# ---------------------------------------------------------------------------
# Score format: must use percentage (N%) everywhere, not decimal (.XX)
# ---------------------------------------------------------------------------


class TestScoreFormatConsistency:
    """Score values must display as percentages (e.g. '9%'), never as
    dot-prefixed decimals (e.g. '.09').

    Grid card overlay and lightbox must use the same format.
    """

    def test_grid_card_scores_use_percentage(self):
        """photos.js score-val must use N% format, not .XX."""
        content = _modules_blob()
        # The score-val span should contain a % sign
        assert re.search(r'class="score-val".*?%', content), (
            "photos.js score-val should use percentage format"
        )
        # Score lines must not use the old .padStart(2,"0") decimal pattern.
        # (padStart IS used legitimately in _formatDuration for time strings.)
        for line in content.splitlines():
            if "score" in line.lower() and ".padStart(2" in line:
                pytest.fail(
                    f"photos.js uses .padStart decimal format in a score line: {line.strip()}"
                )

    def test_lightbox_scores_use_percentage(self):
        """lightbox.js score values must use N% format."""
        content = _modules_blob()
        assert re.search(r'class="lb-score-val".*?%', content), (
            "lightbox.js lb-score-val should use percentage format"
        )


# ---------------------------------------------------------------------------
# Sidebar boost label
# ---------------------------------------------------------------------------


class TestSidebarBoostLabel:
    """Sidebar boost slider must use friendly labels."""

    def test_boost_label_is_people_boost(self):
        content = _modules_blob()
        assert "People boost" in content

    def test_boost_has_tooltip(self):
        content = _modules_blob()
        assert (
            "title=" in content.split("People boost")[1].split("</label>")[0]
            or 'title="' in content.split("nav-face-boost-header")[0]
        )


# ---------------------------------------------------------------------------
# Avatar picker: quality-sorted, larger previews
# ---------------------------------------------------------------------------


class TestAvatarPickerUI:
    """Avatar picker must sort by quality and use larger previews."""

    def test_avatar_grid_cell_size_at_least_80px(self):
        """Grid cells should be at least 80px, not the old 64px.

        Scoped to people-pickers.mjs since the v0.1 cleanup — searching
        the whole modules blob picks up unrelated tiny-icon widths first.
        """
        content = _read(JS_DIR / "modules" / "people-pickers.mjs")
        assert "minmax(64px" not in content, (
            "people-pickers.mjs avatar picker still uses 64px grid cells"
        )
        # Avatar picker uses flexbox with fixed-size cells (currently 88px)
        m = re.search(r"width:\s*(\d+)px.*height:\s*(\d+)px", content)
        assert m and int(m.group(1)) >= 80, (
            "people-pickers.mjs avatar picker cells should be >= 80px"
        )


# ---------------------------------------------------------------------------
# Dismiss / restore: misleading text fixed, restore endpoint exists
# ---------------------------------------------------------------------------


class TestDismissRestoreUI:
    """Dismiss must have honest confirmation text, restore must exist."""

    def test_dismiss_confirmation_no_excluded_filter_lie(self):
        """Dismiss dialog must NOT claim 'Recoverable from Excluded filter'."""
        content = _modules_blob()
        assert "Recoverable from Excluded filter" not in content, (
            "people.js still has misleading dismiss confirmation text"
        )

    def test_restore_function_exists(self):
        """A restorePerson/restoreDismissed function must exist."""
        content = _modules_blob()
        assert re.search(r"function restore(Person|Dismissed)", content), (
            "people.js missing restore function for dismissed faces"
        )

    def test_restore_face_function_exists(self):
        """Individual face restore must exist."""
        content = _modules_blob()
        assert "function restoreFace" in content, (
            "people.js missing restoreFace function for per-face restore"
        )

    def test_dismissed_section_rendered(self):
        """People view must have an 'Ignored' or 'Dismissed' section."""
        content = _modules_blob()
        assert re.search(r"(Ignored|Dismissed)\s*(faces|people)", content, re.IGNORECASE), (
            "people.js missing Ignored/Dismissed section in People view"
        )

    def test_dismissed_faces_grid_exists(self):
        """Dismissed section must render a grid of face thumbnails."""
        content = _modules_blob()
        assert "dismissed-faces-grid" in content, (
            "people.js missing dismissed faces grid for thumbnail display"
        )

    def test_dismissed_api_endpoint_used(self):
        """JS must fetch dismissed faces from the API for display."""
        content = _modules_blob()
        assert "/api/v1/faces/dismissed" in content, (
            "people.js missing call to /api/faces/dismissed endpoint"
        )

    def test_filter_pills_include_minor_and_ignored(self):
        """Filter pills must include minor clusters and ignored faces."""
        content = _modules_blob()
        # Must have filter values for minor and ignored
        assert '"minor"' in content, "people.js missing 'minor' filter for < N photos clusters"
        assert '"ignored"' in content, "people.js missing 'ignored' filter for dismissed faces"


# ---------------------------------------------------------------------------
# Lightbox map: inline map for geotagged photos
# ---------------------------------------------------------------------------


class TestLightboxMap:
    """Lightbox must show an inline map for photos with GPS data."""

    def test_lightbox_map_container_in_html(self):
        """index.html must have a map container inside the lightbox panel."""
        html = _read(TEMPLATE_DIR / "index.html")
        assert 'id="lb-map"' in html or 'id="lb-map-container"' in html, (
            "index.html missing lightbox map container element"
        )

    def test_lightbox_map_init_in_js(self):
        """lightbox.js must initialize a Leaflet map for GPS display."""
        content = _modules_blob()
        assert re.search(r"L\.map\(", content), (
            "lightbox.js must use L.map() to create an inline map"
        )


# ---------------------------------------------------------------------------
# Display toggle: never use style.display="" on elements with CSS display rules
# ---------------------------------------------------------------------------


class TestNoEmptyDisplayToggle:
    """JS must never set style.display="" on elements that have explicit
    CSS display rules (flex, inline-flex, etc.).  Empty string removes the
    inline override, causing the element to fall back to its CSS rule —
    which may be display:none in a media query or the default stylesheet.

    Project rule: set an explicit value ("block"/"flex"), never empty string.
    """

    # Banned pattern. Matches both direct (`= ""`) and ternary forms
    # (`= cond ? "x" : ""` or `= cond ? "" : "x"`). The ternary form
    # was the gap that let modals.mjs:325 slip past the previous
    # per-file regex which only caught the direct form.
    _PATTERN = re.compile(r'\.style\.display\s*=\s*[^;\n]*""')

    def test_no_empty_display_in_any_module(self):
        """Sweep every .mjs file under modules/ for the banned pattern.

        Replaces four per-named-file tests that only covered toolbar /
        app / faces / clip. The sweep version closes the class: any
        future module — current or yet to be written — is covered."""
        import pathlib

        offenders: list[str] = []
        modules_dir = pathlib.Path("bpp/web/static/js/modules")
        for mjs in sorted(modules_dir.rglob("*.mjs")):
            text = mjs.read_text()
            # Strip line comments before scanning — a // comment that
            # mentions `style.display = ""` (e.g. this very file's
            # project-rule explanation) is not a runtime call.
            scrubbed = "\n".join(line.split("//", 1)[0] for line in text.splitlines())
            for i, line in enumerate(scrubbed.splitlines(), 1):
                if self._PATTERN.search(line):
                    rel = mjs.relative_to("bpp/web/static/js/modules")
                    offenders.append(f"{rel}:{i}: {line.strip()}")
        assert not offenders, (
            'Found banned `style.display = ""` (or ternary with empty-string '
            "branch) in:\n  "
            + "\n  ".join(offenders)
            + "\nUse an explicit display value ('block'/'flex'/'inline-flex') instead."
        )


# ---------------------------------------------------------------------------
# Similarity highlight: must use pre-built index, not O(n) scan
# ---------------------------------------------------------------------------


class TestSimilarityHighlightIndex:
    """Dupe-highlight hover must use the pre-built cluster map, not
    a linear scan of all grid items."""

    def test_no_linear_scan_for_similar(self):
        """photos.js must not iterate vgrid.items to find reverse similar matches."""
        content = _modules_blob()
        assert "for (const item of vgrid.items)" not in content, (
            "photos.js still uses O(n) linear scan in dupe-highlight hover"
        )

    def test_sim_cluster_map_built(self):
        """photos.js must build _simClusterMap for O(1) lookups."""
        content = _modules_blob()
        assert "_simClusterMap" in content, (
            "photos.js missing _simClusterMap for similarity cluster lookups"
        )


# ---------------------------------------------------------------------------
# JS function call integrity: every called function must be defined
# ---------------------------------------------------------------------------


class TestJSFunctionCallIntegrity:
    """Every _underscorePrefixed() call in JS must have a matching
    function definition somewhere across all 44 JS files.

    Underscore-prefixed functions are module-private helpers that must be
    defined in our codebase (not browser built-ins or library functions).
    This would have caught the _renderSingleSlider bug.
    """

    # Functions that are called but defined dynamically or in external libs
    _KNOWN_EXTERNAL: ClassVar[set[str]] = {
        "_showTooltip",
        "_hideTooltip",  # may be dynamically generated
    }

    def test_underscore_function_calls_have_definitions(self):
        """Every _prefixed function call must have a function definition."""
        defined = _all_js_function_defs()
        all_js = _all_js_content()

        # Match calls like _funcName( but not in function definitions or comments
        call_re = re.compile(r"(?<!\w)(_[a-zA-Z]\w*)\s*\(")
        # Match function/method definition to exclude
        def_re = re.compile(
            r"(?:async\s+)?function\s+(_[a-zA-Z]\w*)\s*\(|"
            r"(?:const|let|var)\s+(_[a-zA-Z]\w*)\s*="
        )

        missing = []
        for filename, content in all_js.items():
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                # Skip comments
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue
                # Skip definition lines
                if def_re.search(line):
                    continue
                for m in call_re.finditer(line):
                    name = m.group(1)
                    if name in defined or name in self._KNOWN_EXTERNAL:
                        continue
                    # Skip property access patterns like obj._method(
                    # by checking if preceded by a dot
                    prefix_end = m.start(1)
                    if prefix_end > 0 and line[prefix_end - 1] == ".":
                        continue
                    missing.append(f"  {filename}:{i}: {name}()")

        assert not missing, "Undefined _prefixed function calls found:\n" + "\n".join(missing)


class TestHTMLOnclickTargets:
    """Every onclick="funcName(..." in HTML must reference a function
    that is defined in the JS codebase."""

    # Built-in / special targets that don't need JS function definitions
    _KNOWN_BUILTINS: ClassVar[set[str]] = {
        "location",
        "window",
        "document",
        "history",
        "this",
        "event",
        "console",
        "navigator",
        # JS keywords that appear in onclick="if(...)" patterns
        "if",
        "for",
        "while",
        "switch",
        "return",
        "new",
        "typeof",
        "void",
        "delete",
        "throw",
        "try",
        "catch",
    }

    def test_onclick_handlers_have_definitions(self):
        """Every onclick handler must call a defined function."""
        defined = _all_js_function_defs()
        html_files = list(TEMPLATE_DIR.glob("*.html"))

        onclick_re = re.compile(r'onclick="(\w+)\s*\(')
        missing = []
        for html_file in html_files:
            content = _read(html_file)
            for m in onclick_re.finditer(content):
                name = m.group(1)
                if name in defined or name in self._KNOWN_BUILTINS:
                    continue
                # Find line number
                line_num = content[: m.start()].count("\n") + 1
                missing.append(f'  {html_file.name}:{line_num}: onclick="{name}()"')

        assert not missing, "HTML onclick handlers reference undefined functions:\n" + "\n".join(
            missing
        )

    def test_inline_onclick_in_js_templates_have_definitions(self):
        """onclick= handlers in JS template literals must call defined functions."""
        defined = _all_js_function_defs()
        all_js = _all_js_content()

        onclick_re = re.compile(r'onclick="(\w+)\s*\(')
        missing = []
        for filename, content in all_js.items():
            for m in onclick_re.finditer(content):
                name = m.group(1)
                if name in defined or name in self._KNOWN_BUILTINS:
                    continue
                line_num = content[: m.start()].count("\n") + 1
                missing.append(f'  {filename}:{line_num}: onclick="{name}()"')

        assert not missing, (
            "JS inline onclick handlers reference undefined functions:\n" + "\n".join(missing)
        )


class TestDOMElementIDs:
    """getElementById("xxx") calls in JS should reference IDs that exist
    in index.html (with exceptions for dynamically-created elements)."""

    # IDs created dynamically in JS or guarded with null checks
    _DYNAMIC_IDS: ClassVar[set[str]] = {
        "bpp-reconnect-overlay",  # created by Tauri overlay injection
        "bpp-tour-overlay",  # created dynamically in tour.js
        "iph-face-chips",  # created in lightbox panel innerHTML
        "iph-tag-picker",  # created in lightbox panel innerHTML
        "lb-date-input",  # created in lightbox date-edit flow
        "btn-analyze",  # toolbar button, guarded
        "tag-list",  # created in tags view innerHTML
    }

    def test_getelementbyid_targets_exist(self):
        """Most getElementById targets should exist in HTML."""
        html = _read(TEMPLATE_DIR / "index.html")
        all_js = _all_js_content()

        # Also collect IDs defined in JS innerHTML/template literals
        js_combined = "\n".join(all_js.values())
        html_combined = html + js_combined

        getbyid_re = re.compile(r'getElementById\("([^"]+)"\)')
        # Match id="...", id='...', id=\\"...\\" (escaped in JS),
        # and .id = "..." (programmatic assignment)
        all_ids_in_html = set(
            re.findall(r"""id=["']([^"']+)["']""", html_combined)
            + re.findall(r'id=\\"([^"]+)\\"', html_combined)
            + re.findall(r"id=\\'([^']+)\\'", html_combined)
            + re.findall(r"""\.id\s*=\s*["']([^"']+)["']""", js_combined)
        )

        missing = []
        for filename, content in all_js.items():
            for m in getbyid_re.finditer(content):
                elem_id = m.group(1)
                if elem_id in all_ids_in_html or elem_id in self._DYNAMIC_IDS:
                    continue
                # Skip IDs that use template literal interpolation
                if "${" in elem_id or "{" in elem_id:
                    continue
                line_num = content[: m.start()].count("\n") + 1
                missing.append(f'  {filename}:{line_num}: getElementById("{elem_id}")')

        assert not missing, (
            "getElementById() references IDs not found in HTML or JS templates:\n"
            + "\n".join(missing)
        )
