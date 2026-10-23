// @ts-check
/**
 * The picker overlays (merge / split / avatar for people, identify /
 * merge for pets) as dialogs: announced as one, focus moved in and
 * handed back on close, Escape closes only the picker (capture phase, so
 * the lightbox's document handler never sees it), the backdrop closes
 * it, and the listener is always removed with it.
 */

import { focusInto, restoreFocusFrom } from "./dialog-focus.mjs";

/**
 * Attach ``overlay`` to the page as a dialog. Returns the one function
 * that closes it; every close path (Cancel, ×, backdrop, Escape, a
 * choice made) goes through it.
 * @param {HTMLElement} overlay
 * @param {{ label: string, prefer?: HTMLElement | null, onClose?: () => void }} opts
 * @returns {() => void}
 */
export function openPicker(overlay, opts) {
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", opts.label);
  overlay.setAttribute("data-dialog-open", "");
  let open = true;
  const close = () => {
    if (!open) return;
    open = false;
    document.removeEventListener("keydown", onKey, true);
    restoreFocusFrom(overlay);
    overlay.remove();
    opts.onClose?.();
  };
  /** @param {KeyboardEvent} ev */
  const onKey = (ev) => {
    if (ev.key !== "Escape") return;
    ev.stopImmediatePropagation();
    ev.preventDefault();
    close();
  };
  document.addEventListener("keydown", onKey, true);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) close();
  });
  if (!overlay.isConnected) document.body.appendChild(overlay);
  focusInto(overlay, opts.prefer);
  return close;
}

/**
 * Make a clickable element a keyboard choice: in the tab order, announced
 * as a button, activated by Enter or Space.
 * @template {HTMLElement} T
 * @param {T} el
 * @returns {T}
 */
export function pickable(el) {
  el.tabIndex = 0;
  el.setAttribute("role", "button");
  el.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    ev.preventDefault();
    ev.stopPropagation();
    el.click();
  });
  return el;
}
