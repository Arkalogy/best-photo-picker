// @ts-check
/**
 * Keyboard focus for dialogs: into the dialog when it opens, back to where
 * it was when it closes. Every dialog (the generic modal, confirm / prompt,
 * the license dialog, the library picker) goes through these two calls, so
 * a keyboard or screen-reader user is never left focused on something
 * behind an open dialog, or stranded at the top of the page after it.
 */

import { menuTriggerOf } from "./menu-a11y.mjs";

/** What had focus before each overlay opened. */
const _previous = new WeakMap();
/** The Tab-wrap handler installed on each open overlay. */
const _trap = new WeakMap();

const FOCUSABLE =
  'button:not([disabled]):not([hidden]), [href], input:not([disabled]):not([type="hidden"]), ' +
  "select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

/**
 * Remember what has focus, then move it into ``overlay``: to ``prefer``
 * when given (a prompt's input), else the first control inside, else the
 * overlay itself (made focusable so Escape still reaches it).
 * @param {HTMLElement | null} overlay
 * @param {HTMLElement | null} [prefer]
 */
export function focusInto(overlay, prefer) {
  if (!overlay) return;
  let was = document.activeElement;
  // Opened from a menu item (about to be hidden with its menu): go back
  // to what opened the menu instead.
  const menu = was?.closest?.('[role="menu"]');
  if (menu) was = menuTriggerOf(menu) || was;
  if (was instanceof HTMLElement && !overlay.contains(was)) _previous.set(overlay, was);
  if (!_trap.has(overlay)) {
    /** @param {KeyboardEvent} e */
    const onTab = (e) => {
      if (e.key !== "Tab") return;
      const all = /** @type {HTMLElement[]} */ ([...overlay.querySelectorAll(FOCUSABLE)]);
      if (!all.length) {
        e.preventDefault(); // nothing to go to: stay (never the page behind)
        return;
      }
      const first = all[0];
      const last = all[all.length - 1];
      const at = all.indexOf(/** @type {HTMLElement} */ (document.activeElement));
      if (at < 0 || (e.shiftKey ? at === 0 : at === all.length - 1)) {
        // From the overlay itself, or past either end: wrap.
        e.preventDefault();
        (e.shiftKey ? last : first).focus({ preventScroll: true });
      }
    };
    overlay.addEventListener("keydown", onTab);
    _trap.set(overlay, onTab);
  }
  const target =
    prefer || /** @type {HTMLElement | null} */ (overlay.querySelector(FOCUSABLE)) || overlay;
  if (target === overlay && !overlay.hasAttribute("tabindex")) overlay.tabIndex = -1;
  try {
    target.focus({ preventScroll: true });
  } catch {
    /* an element that can't take focus: nothing to do */
  }
}

/**
 * Whether any dialog is open. The static overlays in index.html stay in
 * the DOM and show with ``.visible`` (a few hide with ``.hidden``);
 * dialogs built on demand (the pickers) mark themselves ``data-dialog-open``.
 * @returns {boolean}
 */
export function anyDialogOpen() {
  return !!document.querySelector(
    '[role="dialog"].visible, [role="dialog"][data-dialog-open], ' +
      '#onboarding-overlay:not(.hidden), #slideshow-overlay:not(.hidden), ' +
      "#library-picker-overlay:not(.hidden)"
  );
}

/**
 * Give focus back to what had it before ``overlay`` opened (if that is
 * still on the page), and forget it.
 * @param {HTMLElement | null} overlay
 */
export function restoreFocusFrom(overlay) {
  if (!overlay) return;
  const onTab = _trap.get(overlay);
  if (onTab) {
    overlay.removeEventListener("keydown", onTab);
    _trap.delete(overlay);
  }
  const was = _previous.get(overlay);
  _previous.delete(overlay);
  if (was && was.isConnected) {
    try {
      was.focus({ preventScroll: true });
    } catch {
      /* gone or unfocusable: leave focus where it is */
    }
  }
}
