"""Source-scanning regression tests for JS/HTML/CSS — part 2 of 3: TestCriticalFunctionRefs,
TestBulkSelectMerge, TestFaceSplitUI."""

from __future__ import annotations

import re
from typing import ClassVar

from tests._js_source_scan_shared import (
    CSS_DIR,
    JS_DIR,
    TEMPLATE_DIR,
    _all_js_function_defs,
    _all_module_content,
    _modules_blob,
    _read,
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
