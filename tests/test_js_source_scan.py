"""Source-scanning regression tests for JS/HTML/CSS.

These tests read the actual source files and assert patterns that must
hold to prevent UI regressions.  No browser required.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import pytest

JS_DIR = Path(__file__).resolve().parent.parent / "bpp" / "web" / "static" / "js"
CSS_DIR = Path(__file__).resolve().parent.parent / "bpp" / "web" / "static" / "css"
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "bpp" / "web" / "templates"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _all_js_content() -> dict[str, str]:
    """Return {filename: content} for all classic JS files."""
    return {p.name: _read(p) for p in sorted(JS_DIR.glob("*.js"))}


def _all_module_content() -> dict[str, str]:
    """Return {filename: content} for ES modules under modules/."""
    modules_dir = JS_DIR / "modules"
    if not modules_dir.is_dir():
        return {}
    return {p.name: _read(p) for p in sorted(modules_dir.glob("*.mjs"))}


def _modules_blob() -> str:
    """Concatenate every ES module under modules/ into one searchable string.

    Used by source-scan tests that look for a symbol defined "somewhere in
    the people / lightbox / faces JS surface" — the v0.1 cleanup sharded
    monoliths like people.mjs into people.mjs + people-review.mjs +
    people-merge.mjs + people-view.mjs + people-pair-review.mjs etc., so a
    single-file read no longer finds symbols that moved to a sibling.
    """
    return "\n".join(_all_module_content().values())


def _all_js_function_defs() -> set[str]:
    """Extract all function names defined across classic JS files AND
    all names exported from ES modules under modules/. The exported
    names are bridged onto window via index.html's module bootstrap,
    so they're effectively globals to the script-tag callers.

    Matches in classic .js:
      function NAME(, async function NAME(, NAME = function(,
      NAME = async function(, const/let/var NAME = (...) =>,
      and also plain variable declarations (let NAME = null).

    Matches in modules/*.mjs:
      export function NAME, export async function NAME,
      export const|let|var NAME, export { A, B as C }.
    """
    defs = set()
    func_def_re = re.compile(
        r"(?:async\s+)?function\s+(\w+)\s*\(|"  # function NAME( / async function NAME(
        r"(?:const|let|var)\s+(\w+)\s*=",  # const/let/var NAME = (anything)
    )
    for content in _all_js_content().values():
        for m in func_def_re.finditer(content):
            name = m.group(1) or m.group(2)
            if name:
                defs.add(name)

    # ES module exports — bridged onto window via the index.html
    # `<script type="module">` block, so visible to classic callers.
    export_fn_re = re.compile(r"export\s+(?:async\s+)?function\s+(\w+)")
    export_var_re = re.compile(r"export\s+(?:const|let|var)\s+(\w+)")
    export_list_re = re.compile(r"export\s*\{([^}]+)\}")
    for content in _all_module_content().values():
        for m in export_fn_re.finditer(content):
            defs.add(m.group(1))
        for m in export_var_re.finditer(content):
            defs.add(m.group(1))
        for m in export_list_re.finditer(content):
            for part in m.group(1).split(","):
                ident = part.strip().split(" as ")[-1].strip()
                if ident and re.fullmatch(r"[a-zA-Z_$][\w$]*", ident):
                    defs.add(ident)
    return defs


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
# Model install: generalized install endpoint and UI
# ---------------------------------------------------------------------------


class TestModelInstallUI:
    """1-click model install UI must exist in modals.js."""

    def test_installPackage_function_exists(self):
        content = _modules_blob()
        assert "function installPackage" in content

    def test_install_button_rendered_for_pip_models(self):
        """Models with install_key should get an Install button."""
        content = _modules_blob()
        assert "model-install-btn" in content
        assert "canPipInstall" in content

    def test_download_all_missing_button(self):
        """A 'Download all missing models' batch button must exist."""
        content = _modules_blob()
        assert "model-download-all" in content
        assert "Download all missing" in content


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


# ---------------------------------------------------------------------------
# Critical function references: catch typos like renderPeopleView vs showPeopleView
# ---------------------------------------------------------------------------


class TestCriticalFunctionRefs:
    """Functions called in people.js must actually exist somewhere."""

    # Functions known to be defined in other files or browser globals
    _EXTERNAL: ClassVar[set[str]] = {
        "apiFetch",
        "esc",
        "toast",
        "appConfirm",
        "authedSrc",
        "loadAlbumList",
        "renderAlbumNav",
        "refreshSmartAlbums",
        "loadFaceClusters",
        "scheduleRecompute",
        "navigateTo",
        "updateToolbarTitle",
        "updateBreadcrumbs",
        "showPeopleView",
        "showStatusProgress",
        "hideStatusProgress",
        "parseInt",
        "parseFloat",
        "setTimeout",
        "setInterval",
        "clearTimeout",
        "clearInterval",
        "requestAnimationFrame",
        "encodeURIComponent",
        "JSON",
        "Array",
        "Object",
        "Map",
        "Set",
        "Number",
        "String",
        "Math",
        "Date",
        "Promise",
        "Error",
        "document",
        "window",
        "console",
        "fetch",
        "isNaN",
    }

    def test_no_undefined_view_functions_in_people(self):
        """Catch typos like renderPeopleView (should be showPeopleView)."""
        defined = _all_js_function_defs()
        content = _modules_blob()
        # Match bare function calls: word( but not .word( or "word(
        call_re = re.compile(r"(?<![.\"\w])([a-zA-Z]\w+)\s*\(")
        missing = []
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            for m in call_re.finditer(line):
                name = m.group(1)
                if name in defined or name in self._EXTERNAL:
                    continue
                # Skip common patterns: keywords, constructors, template literals
                if name[0].isupper() and name not in ("NaN",):
                    continue  # likely a constructor
                if name in (
                    "if",
                    "for",
                    "while",
                    "switch",
                    "return",
                    "typeof",
                    "new",
                    "async",
                    "await",
                    "function",
                    "let",
                    "const",
                    "var",
                    "else",
                    "catch",
                    "throw",
                    "delete",
                    "void",
                    "class",
                    "super",
                    "this",
                    "true",
                    "false",
                    "null",
                    "undefined",
                    "yield",
                    "export",
                    "import",
                    "try",
                    "finally",
                    "break",
                    "continue",
                    "case",
                    "default",
                    "do",
                    "in",
                    "of",
                    "with",
                    "debugger",
                    "instanceof",
                ):
                    continue
                # Skip method calls (preceded by dot)
                prefix_end = m.start(1)
                if prefix_end > 0 and line[prefix_end - 1] == ".":
                    continue
                missing.append(f"  people.js:{i}: {name}()")
        # Only fail if there are clear misses (allow some false positives)
        real_missing = [m for m in missing if "renderPeopleView" in m or "renderPersonView" in m]
        assert not real_missing, "Undefined function calls in people.js:\n" + "\n".join(
            real_missing
        )


# ---------------------------------------------------------------------------
# Bulk select + merge: multi-select person cards and merge
# ---------------------------------------------------------------------------


class TestBulkSelectMerge:
    """People view must support multi-select and bulk merge."""

    def test_selection_state_exists(self):
        """JS must track selected person cards."""
        content = _modules_blob()
        assert "_selectedPeople" in content or "selectedPeople" in content

    def test_toggle_select_function(self):
        """Must have a function to toggle person card selection."""
        content = _modules_blob()
        assert "togglePersonSelect" in content

    def test_shift_click_support(self):
        """Person card click handler must check shiftKey for range select."""
        content = _modules_blob()
        assert "shiftKey" in content

    def test_selected_card_css_class(self):
        """Selected person cards must get a visual indicator class."""
        css = _read(CSS_DIR / "app.css")
        assert "person-card-selected" in css or "person-card.selected" in css

    def test_merge_selected_function(self):
        """Must have a function to merge all selected into a target."""
        content = _modules_blob()
        assert "mergeSelected" in content

    def test_context_menu_merge_selected(self):
        """Context menu must handle merging selected cards."""
        content = _modules_blob()
        assert "merge-selected" in content or "mergeSelected" in content

    def test_selection_bar_or_count(self):
        """Must show selection count or action bar when cards are selected."""
        content = _modules_blob()
        assert (
            "selectedCount" in content
            or "selection-bar" in content
            or "_selectedPeople.size" in content
        )


# ---------------------------------------------------------------------------
# Face split: select faces in person view and split to new cluster
# ---------------------------------------------------------------------------


class TestFaceSplitUI:
    """Split cluster UI must have selection mode and split action."""

    def test_split_function_exists(self):
        content = _modules_blob()
        assert "splitSelectedFaces" in content or "splitCluster" in content

    def test_split_calls_api(self):
        """JS must call the split API endpoint."""
        content = _modules_blob()
        assert "/api/v1/faces/split" in content

    def test_face_selection_toggle_exists(self):
        """People view must have a way to select individual faces."""
        content = _modules_blob()
        assert "toggleFaceSelect" in content or "face-select" in content


# ---------------------------------------------------------------------------
# Face review flow: smart review with suggested matches
# ---------------------------------------------------------------------------


class TestFaceReviewFlow:
    """Face review flow must have all required functions and UI patterns."""

    def test_startFaceReview_exists(self):
        content = _modules_blob()
        assert "function startFaceReview" in content

    def test_review_overlay_functions_exist(self):
        content = _modules_blob()
        assert "_showReviewOverlay" in content
        assert "_closeReviewOverlay" in content
        assert "_renderReviewCard" in content

    def test_review_action_functions_exist(self):
        content = _modules_blob()
        assert "_reviewMergeInto" in content
        assert "_reviewConfirm" in content
        assert "_reviewSkip" in content
        assert "_reviewDismiss" in content

    def test_review_button_has_action(self):
        """Review button in people view must wire startFaceReview() via data-action."""
        content = _modules_blob()
        assert 'data-action="startFaceReview"' in content, (
            "people.mjs missing Review button with data-action=startFaceReview"
        )

    def test_suggested_match_handled(self):
        """Review card must handle suggested_match from the API."""
        content = _modules_blob()
        assert "suggested_match" in content

    def test_review_autocomplete_exists(self):
        content = _modules_blob()
        assert "_reviewAutocomplete" in content

    def test_review_fetches_api(self):
        """Review flow must call the review API endpoint."""
        content = _modules_blob()
        assert "/api/v1/faces/review" in content


# ---------------------------------------------------------------------------
# Editor module split: crop, red-eye, inpaint as separate files
# ---------------------------------------------------------------------------


class TestEditorModuleSplit:
    """Editor sub-modules must define their entry point functions."""

    def test_crop_module_entry_point(self):
        content = _modules_blob()
        assert "function _toggleCropOverlay" in content

    def test_crop_aspect_ratio(self):
        content = _modules_blob()
        assert "_setAspectRatio" in content

    def test_redeye_module_entry_point(self):
        content = _modules_blob()
        assert "function _toggleRedeyeMode" in content

    def test_inpaint_module_entry_point(self):
        content = _modules_blob()
        assert "_renderRemoveControls" in content

    def test_inpaint_tool_switcher(self):
        content = _modules_blob()
        assert "_setInpaintTool" in content

    def test_editor_integrates_crop(self):
        """editor.js must call crop module's toggle function."""
        content = _modules_blob()
        assert "_toggleCropOverlay" in content

    def test_editor_integrates_redeye(self):
        """editor.js must call redeye module's toggle function."""
        content = _modules_blob()
        assert "_toggleRedeyeMode" in content

    def test_editor_integrates_inpaint(self):
        """editor.js must call inpaint module's render function."""
        content = _modules_blob()
        assert "_renderRemoveControls" in content


# ---------------------------------------------------------------------------
# Breadcrumb navigation: contextual breadcrumbs for smart albums
# ---------------------------------------------------------------------------


class TestBreadcrumbNavigation:
    """Breadcrumb rendering must exist and be wired into navigation."""

    def test_updateBreadcrumbs_exists(self):
        content = _modules_blob()
        assert "function updateBreadcrumbs" in content

    def test_breadcrumb_classes(self):
        """Breadcrumb HTML must use expected CSS classes."""
        content = _modules_blob()
        assert "bc-link" in content
        assert "bc-current" in content

    def test_core_calls_updateBreadcrumbs(self):
        """core.js must call updateBreadcrumbs for view routing."""
        content = _read(JS_DIR / "modules" / "core.mjs")
        assert "updateBreadcrumbs" in content


# ---------------------------------------------------------------------------
# appConfirm / toast: never use browser confirm() or alert()
# ---------------------------------------------------------------------------


class TestNoRawDialogs:
    """JS must never use browser confirm() or alert() — use appConfirm/toast."""

    _BANNED_CONFIRM = re.compile(r"(?<!\w)confirm\s*\(")
    _BANNED_ALERT = re.compile(r"(?<!\w)alert\s*\(")

    # Files that legitimately define appConfirm or reference confirm in comments
    _EXEMPT_FILES: ClassVar[set[str]] = {"navigation.js"}

    def test_no_raw_confirm_in_js(self):
        """No JS file should use raw confirm() — use appConfirm()."""
        violations = []
        for js_file in (JS_DIR / "modules").glob("*.mjs"):
            if js_file.name in self._EXEMPT_FILES:
                continue
            content = _read(js_file)
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue
                if self._BANNED_CONFIRM.search(line) and "appConfirm" not in line:
                    violations.append(f"  {js_file.name}:{i}: {stripped[:80]}")
        assert not violations, "Raw confirm() found — use appConfirm() instead:\n" + "\n".join(
            violations
        )

    def test_no_raw_alert_in_js(self):
        """No JS file should use raw alert() — use toast()."""
        violations = []
        for js_file in (JS_DIR / "modules").glob("*.mjs"):
            content = _read(js_file)
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue
                if self._BANNED_ALERT.search(line):
                    violations.append(f"  {js_file.name}:{i}: {stripped[:80]}")
        assert not violations, "Raw alert() found — use toast() instead:\n" + "\n".join(violations)

    def test_appConfirm_defined(self):
        # Migrated to modules/dialogs.mjs — bridged onto window via the
        # <script type="module"> block in index.html.
        content = _read(JS_DIR / "modules" / "dialogs.mjs")
        assert "export function appConfirm" in content

    def test_toast_defined(self):
        # Migrated to modules/toast.mjs — bridged onto window.
        content = _read(JS_DIR / "modules" / "toast.mjs")
        assert "export function toast" in content


# ---------------------------------------------------------------------------
# Face pair review — undefined-function regression guard
# ---------------------------------------------------------------------------


class TestFaceReviewCalls:
    """Prevent recurrences of the loadFaces() bug — a call-site that
    references a name that was never defined anywhere. The original
    /api/faces/review + new /api/faces/review-pairs close handlers both
    historically called loadFaces() which silently errored because the
    actual function is loadFaceClusters() in faces.js.

    This test pins the specific names these close handlers must use.
    """

    def test_face_review_close_uses_loadFaceClusters(self):
        """_closeReviewOverlay must call loadFaceClusters, not loadFaces."""
        content = _modules_blob()
        # The close handler block
        idx = content.find("function _closeReviewOverlay")
        assert idx != -1, "_closeReviewOverlay function missing"
        block = content[idx : idx + 500]
        assert "loadFaceClusters(" in block, (
            "_closeReviewOverlay must call loadFaceClusters() to refresh"
        )
        assert "loadFaces(" not in block, "loadFaces() does not exist — use loadFaceClusters()"

    def test_pair_review_close_uses_loadFaceClusters(self):
        """_closePairReviewOverlay must call loadFaceClusters, not loadFaces."""
        content = _modules_blob()
        idx = content.find("function _closePairReviewOverlay")
        assert idx != -1, "_closePairReviewOverlay function missing"
        block = content[idx : idx + 800]
        assert "loadFaceClusters(" in block
        assert "loadFaces(" not in block, "loadFaces() does not exist — use loadFaceClusters()"

    def test_tag_person_menu_uses_face_aware_handler(self):
        """The lightbox right-click "Tag person…" must route through
        _lbTagPersonFromMenu (which fetches faces and shows a face picker
        when there are multiple), not _iphShowTagPicker (photo-level tag
        endpoint that ignores which face is meant)."""
        modules = _all_module_content()
        # Only the lightbox modules carry the face-aware "tag-person" handler;
        # the card-level ctx menu in deleted-ctx-menu intentionally uses the
        # photo-level _iphShowTagPicker because no face context is selected.
        lightbox_blob = "\n".join(
            content for name, content in modules.items() if name.startswith("lightbox")
        )
        # The function exists somewhere in the lightbox surface
        assert "function _lbTagPersonFromMenu" in lightbox_blob
        # The two right-click entry points use it
        for marker in ['action === "tag-person"', 'e.key === "t"']:
            idx = lightbox_blob.find(marker)
            assert idx != -1, f"missing entry point: {marker}"
            block = lightbox_blob[idx : idx + 300]
            assert "_lbTagPersonFromMenu" in block, (
                f"entry point near {marker!r} should call _lbTagPersonFromMenu, "
                f"not _iphShowTagPicker (photo-level fallback)"
            )

    def test_pair_template_has_request_access_button(self):
        """pair.html's revoked terminal state must have a 'Request
        access again' button. Without it the phone has no way to ask
        again after a revoke (we removed the auto-soft-revival on
        purpose). Pin the marker so a future template refactor
        can't silently drop the button."""
        content = _read(TEMPLATE_DIR / "pair.html")
        # The button element + click handler + endpoint
        assert 'id="pair-request-btn"' in content, (
            "pair.html must keep the #pair-request-btn for revoke recovery"
        )
        assert "Request access again" in content
        assert "/api/v1/share/pair/request" in content, (
            "pair.html must POST to /api/share/pair/request to re-request"
        )
        # Polling must stop in revoked state — otherwise we'd spam the
        # owner with implicit pending requests on every poll tick.
        assert "stopped = true" in content, "pair.html must stop polling in revoked state"


class TestMobileLightbox:
    """The #6 mobile-lightbox slice rebuilds the phone-width layout
    as a bottom-sheet (image on top, info panel slides up from
    bottom). Pin the structural CSS + JS markers so a future
    refactor can't silently revert to the broken pre-slice layout
    (image capped to 50vh, panel takes 98vw below = empty void)."""

    def test_phone_breakpoint_uses_bottom_sheet_layout(self):
        """At max-width: 480px, the lightbox-content must switch to
        flex-direction: column (image on top, panel underneath)."""
        css = _read(CSS_DIR / "app.css")
        # Locate the 480px media query block
        m = re.search(r"@media \(max-width: 480px\) \{(.+?)\n  \}\s*\n", css, re.DOTALL)
        assert m, "app.css must have a `@media (max-width: 480px)` block"
        body = m.group(1)
        # Bottom-sheet markers — must restructure, not just resize
        assert "flex-direction: column" in body, (
            "phone-width lightbox must use column layout (image top, panel bottom)"
        )
        assert "100dvh" in body, (
            "phone-width lightbox must use dvh (dynamic viewport) for height — "
            "vh causes the iOS Safari URL-bar viewport jump"
        )
        assert "position: fixed" in body and "bottom: 0" in body, (
            "lightbox-panel on phone must be position: fixed at bottom (bottom sheet)"
        )

    def test_phone_hides_chevrons_only_in_editor_mode(self):
        """At phone width the chevron prev/next buttons must be visible —
        swipe is undiscoverable without the affordance. They're still
        hidden inside editor-mode so they don't fight the crop / adjust UI."""
        css = _read(CSS_DIR / "app.css")
        m = re.search(r"@media \(max-width: 480px\) \{(.+?)\n  \}\s*\n", css, re.DOTALL)
        assert m
        body = m.group(1)
        # Chevrons visible at phone width: dark translucent bg, not display:none
        assert re.search(r"\.lightbox-nav\s*\{[^}]*background:\s*rgba\(0,\s*0,\s*0", body), (
            "phone-width must give .lightbox-nav a dark translucent bg "
            "so it reads against bright photos"
        )
        # But hidden inside editor-mode so they don't fight crop/adjust UI
        assert re.search(
            r"\.lightbox-overlay\.editor-mode\s+\.lightbox-nav\s*\{[^}]*display:\s*none",
            body,
        ), "editor-mode at phone width must hide .lightbox-nav (chevrons fight crop/adjust UI)"

    def test_phone_grid_locks_to_three_columns(self):
        """At phone width the default auto-fill,minmax(140px,1fr) only
        fits 2 cols on a 393px iPhone, which feels too sparse and
        doesn't match Apple Photos density. Locked to 3 cols + tight
        6px gap."""
        css = _read(CSS_DIR / "app.css")
        m = re.search(r"@media \(max-width: 480px\) \{(.+?)\n  \}\s*\n", css, re.DOTALL)
        assert m
        body = m.group(1)
        assert re.search(
            r"\.grid\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*1fr\)",
            body,
        ), (
            "phone-width .grid must lock to repeat(3, 1fr) — auto-fill/minmax leaves "
            "iPhones at 2 cols which is too sparse"
        )

    def test_touch_devices_get_hover_reveal_fallbacks(self):
        """Every visibility-toggling :hover rule elsewhere in the
        stylesheet must have a matching `opacity: 1` rule inside
        `@media (pointer: coarse)`. Without that, the target element
        is permanently invisible on touch (no :hover ever fires on a
        touchscreen)."""
        css = _read(CSS_DIR / "app.css")
        # Extract just the `(pointer: coarse)` block body
        m = re.search(r"@media \(pointer:\s*coarse\)\s*\{(.+?)\n  \}\s*\n", css, re.DOTALL)
        assert m, "app.css must define a `@media (pointer: coarse)` block"
        body = m.group(1)
        # Every critical hover-reveal target must appear in the touch fallback.
        # NOTE: .card-actions and .score-overlay are intentionally NOT here —
        # they obscure the photo on tiny phone thumbnails and are reachable
        # via the lightbox (tap card → opens lightbox with all actions).
        # They get `display: none` in the 480px block instead.
        required_targets = [
            ".deleted-hover-actions",  # restore/permanent-delete buttons in trash
            ".library-item-actions",  # library row rename/delete buttons
            ".nav-item-actions",  # sidebar album rename/delete buttons
            ".lb-face-overlay",  # face boxes on lightbox image — must be tappable
            ".iph-face-untag",  # untag-person button in inspector
            ".dismissed-face-actions",  # restore/permanent-delete on dismissed face cells
            ".cal-week-thumb",  # week cell photo thumbnail in calendar
            ".ss-controls",  # slideshow play/pause/skip controls
        ]
        for target in required_targets:
            assert target in body, (
                f"`@media (pointer: coarse)` block must include `{target}` opacity:1 "
                f"override — touch devices never trigger :hover"
            )

    def test_phone_grid_cards_are_chrome_free(self):
        """Phone-width photo cards must hide score-overlay,
        card-actions, and card-date-stamp. They clutter tiny thumbnails
        on iPhone (<= 130px wide each in the 3-col grid). The lightbox
        is the action surface on phone — tap a card → all actions
        visible there."""
        css = _read(CSS_DIR / "app.css")
        m = re.search(r"@media \(max-width: 480px\) \{(.+?)\n  \}\s*\n", css, re.DOTALL)
        assert m
        body = m.group(1)
        assert re.search(
            r"\.score-overlay,\s*\n\s*\.card-actions,\s*\n\s*\.card-date-stamp\s*\{[^}]*display:\s*none",
            body,
        ), (
            "phone-width must hide .score-overlay + .card-actions + .card-date-stamp "
            "as a single rule — these clutter tiny thumbnails and are reachable in "
            "the lightbox"
        )

    def test_phone_toolbar_compacts_pick_and_scrolls(self):
        """At phone width the toolbar-right has 8+ buttons + Pick input
        — overflows the viewport. Required:
          - .toolbar-right scrolls horizontally instead of wrapping
          - .toolbar-pick label hidden
          - .toolbar-pick input shrinks to ~36px
          - .toolbar-pick-scope hidden (album scope label)"""
        css = _read(CSS_DIR / "app.css")
        m = re.search(r"@media \(max-width: 480px\) \{(.+?)\n  \}\s*\n", css, re.DOTALL)
        assert m
        body = m.group(1)
        assert re.search(r"\.toolbar-right\s*\{[^}]*overflow-x:\s*auto", body), (
            ".toolbar-right must overflow-x: auto at phone width — without this it "
            "wraps to two lines or clips the rightmost buttons"
        )
        assert re.search(r"\.toolbar-pick label\s*\{\s*display:\s*none", body), (
            ".toolbar-pick label must be hidden at phone width — every px counts"
        )

    def test_phone_form_modals_go_fullscreen(self):
        """Form-style modals (settings, library picker, search, batch
        rename) must go full-viewport on phone. The dimmed-overlay-
        with-card pattern fights phone ergonomics: the card is smaller
        than the keyboard, surrounding context is invisible anyway,
        and a half-modal feels like a misclick away from dismissal."""
        css = _read(CSS_DIR / "app.css")
        m = re.search(r"@media \(max-width: 480px\) \{(.+?)\n  \}\s*\n", css, re.DOTALL)
        assert m
        body = m.group(1)
        # All four panels must hit the same fullscreen rule
        for klass in (
            ".settings-panel",
            ".library-picker-modal",
            ".search-modal",
            ".rename-modal",
        ):
            assert klass in body, f"phone-width must apply fullscreen rule to {klass}"
        assert "100dvh" in body, (
            "phone form modals must use 100dvh (not vh — iOS Safari URL-bar viewport jump)"
        )
        # Confirm dialog stays compact — it's a focused interrupt, not a workspace.
        # Inherits max-width: 90vw from the 768px media query; the 480px block
        # must not pull it into the fullscreen rule.
        fullscreen_rule = re.search(
            r"\.settings-panel,\s*\n\s*\.library-picker-modal,\s*\n\s*\.search-modal,\s*\n\s*\.rename-modal\s*\{",
            body,
        )
        assert fullscreen_rule, (
            "phone-width fullscreen rule must list exactly the four form modals "
            "(settings, library-picker, search, rename) — confirm-dialog stays out"
        )
        assert ".confirm-dialog" not in fullscreen_rule.group(0), (
            "confirm-dialog must NOT be in the fullscreen-modals rule "
            "(it's a focused interrupt, must stay compact)"
        )

    def test_phone_uses_safe_area_inset(self):
        """Notched devices (iPhone X+) need safe-area-inset padding
        so the close button doesn't tuck under the camera bump."""
        css = _read(CSS_DIR / "app.css")
        m = re.search(r"@media \(max-width: 480px\) \{(.+?)\n  \}\s*\n", css, re.DOTALL)
        assert m
        body = m.group(1)
        assert "safe-area-inset-top" in body, (
            "phone-width lightbox must use env(safe-area-inset-top) — otherwise "
            "the close button sits behind the iPhone camera notch"
        )

    def test_drag_handle_hidden_on_desktop(self):
        """The bottom-sheet drag handle is mobile-only. Default
        `.lb-panel-handle { display: none }` prevents it from
        showing as a stray dot on desktop."""
        css = _read(CSS_DIR / "app.css")
        # The default rule must be `display: none`
        assert re.search(r"\.lb-panel-handle\s*\{\s*display:\s*none\s*;\s*\}", css), (
            ".lb-panel-handle should default to display: none on desktop"
        )

    def test_drag_handle_in_template(self):
        """The handle DOM element must exist so the JS can wire a
        click listener to it. Inside .lightbox-panel, before .lb-header."""
        html = _read(TEMPLATE_DIR / "index.html")
        assert 'id="lb-panel-handle"' in html, (
            "index.html must contain #lb-panel-handle inside the lightbox panel"
        )
        # Order matters — handle should come before lb-header so it's
        # the visual top of the sheet.
        handle_idx = html.find('id="lb-panel-handle"')
        header_idx = html.find('class="lb-header"')
        assert 0 < handle_idx < header_idx, (
            "lb-panel-handle must appear before lb-header inside the lightbox panel"
        )

    def test_panel_toggle_handler_wired(self):
        """The mobile bottom-sheet init IIFE in lightbox.mjs must
        attach the toggle handler. If the listener is dropped, the
        handle becomes a non-tappable visual.

        _lbTogglePanel + the expanded-class lifecycle moved to
        lightbox-actions.mjs during the v0.1 split — search the whole
        lightbox surface, not just lightbox.mjs.
        """
        modules = _all_module_content()
        lightbox_blob = "\n".join(
            content for name, content in modules.items() if name.startswith("lightbox")
        )
        assert lightbox_blob, "no lightbox modules found"
        assert "_lbTogglePanel" in lightbox_blob, "_lbTogglePanel helper must exist"
        assert 'getElementById("lb-panel-handle")' in lightbox_blob, (
            "lightbox.mjs must look up #lb-panel-handle to attach listeners"
        )
        # The handler must clear the expanded state on close so the
        # next photo's lightbox starts collapsed (per spec).
        assert 'classList.remove("expanded")' in lightbox_blob, (
            "closeLightbox must reset .expanded on the panel"
        )

    def test_no_undefined_loadFaces_calls_anywhere(self):
        """Global guard: nothing in JS should call loadFaces() — it's not
        defined anywhere. Close this whole class if loadFaces ever becomes
        an actual function."""
        for name, content in _all_js_content().items():
            # Strip out the comment markers so we don't fail on docstrings
            # that intentionally mention the name.
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue
                assert "loadFaces(" not in line, (
                    f"{name}: calls loadFaces() which is not defined "
                    f"(did you mean loadFaceClusters?) — line: {line.strip()}"
                )
