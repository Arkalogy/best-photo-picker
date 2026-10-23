"""Source-scanning regression tests for JS/HTML/CSS — part 3 of 3: TestMobileLightbox."""

from __future__ import annotations

import re

from tests._js_source_scan_shared import (
    CSS_DIR,
    TEMPLATE_DIR,
    _all_js_content,
    _all_module_content,
    _read,
)


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
