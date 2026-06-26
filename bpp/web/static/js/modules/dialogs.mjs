// @ts-check
/**
 * Custom confirm / prompt dialogs.
 *
 * Replaces the browser's `confirm()` and `prompt()` (which freeze the
 * page and look out of place inside Tauri). Renders a dialog into
 * `.confirm-dialog` inside an
 * `#confirm-overlay`, returns a Promise that resolves when the user
 * clicks OK / Cancel or hits Esc.
 *
 * Bridged onto window — `appConfirm`, `appPrompt`, and
 * `resolveConfirm` (the last one is referenced from inline
 * `` in the rendered HTML, so it must be globally
 * reachable).
 */

import { esc, escapeAttr } from "./text-format.mjs";

/**
 * @typedef {(value: boolean) => void} ConfirmResolver
 */

/** @type {ConfirmResolver | ((value: any) => void) | null} */
let _confirmResolve = null;

/**
 * Override the active resolver. Used by custom dialogs (e.g.
 * `_promptFaceInstall` in analysis.js) that build their own button
 * HTML and need to bind the click handlers to a Promise resolver.
 *
 * @param {((value: any) => void) | null} fn
 */
export function _setConfirmResolve(fn) {
  _confirmResolve = fn;
}

/** @param {string} dialogHTML */
export function _showConfirmOverlay(dialogHTML) {
  const dialog = /** @type {HTMLElement | null} */ (
    document.querySelector(".confirm-dialog")
  );
  if (!dialog) return;
  dialog.innerHTML = dialogHTML;
  const overlay = document.getElementById("confirm-overlay");
  overlay?.classList.add("visible");
  // Capture phase + stopImmediatePropagation so ESC is consumed before
  // it reaches the lightbox's global keydown handler (which would
  // otherwise also close the lightbox).
  document.addEventListener("keydown", _onConfirmKey, true);
}

/** @param {KeyboardEvent} e */
function _onConfirmKey(e) {
  if (e.key === "Escape") {
    e.stopPropagation();
    e.stopImmediatePropagation();
    resolveConfirm(false);
  }
}

/**
 * Show a yes/no dialog. Resolves to `true` on confirm, `false` on
 * cancel or Esc.
 *
 * Two call shapes are supported:
 *   appConfirm("Delete this?", { okLabel: "Delete" })
 *   appConfirm("Delete this?", "This cannot be undone")
 *   appConfirm("Delete this?", "This cannot be undone", { okLabel: "Delete" })
 *
 * @param {string} message
 * @param {string | null | { okLabel?: string, okClass?: string, bodyHTML?: string }} [subtitle]
 * @param {{ okLabel?: string, okClass?: string, bodyHTML?: string }} [opts]
 * @returns {Promise<boolean>}
 */
export function appConfirm(message, subtitle, opts) {
  // Two-arg call: subtitle slot holds opts
  if (typeof subtitle === "object" && subtitle !== null) {
    return appConfirm(message, null, subtitle);
  }
  const { okLabel = "OK", okClass = "primary", bodyHTML = "" } = opts || {};
  return new Promise((resolve) => {
    _confirmResolve = resolve;
    let html = `<p>${esc(message)}</p>`;
    if (subtitle) {
      html += `<p class="confirm-sub">${esc(/** @type {string} */ (subtitle))}</p>`;
    }
    if (bodyHTML) html += bodyHTML;
    html += `<div class="confirm-actions">
      <button data-action="resolveConfirm" data-arg0="false">Cancel</button>
      <button class="${escapeAttr(okClass)}" id="confirm-ok" data-action="resolveConfirm" data-arg0="true">${esc(okLabel)}</button>
    </div>`;
    _showConfirmOverlay(html);
  });
}

/**
 * Show a single-line text input dialog. Resolves to the trimmed
 * input on OK, or `null` on cancel.
 *
 * @param {string} title
 * @param {{ placeholder?: string, value?: string, okLabel?: string, okClass?: string }} [opts]
 * @returns {Promise<string | null>}
 */
export function appPrompt(title, opts) {
  const {
    placeholder = "",
    value = "",
    okLabel = "OK",
    okClass = "primary",
  } = opts || {};
  return new Promise((resolve) => {
    _confirmResolve = (ok) => {
      const input = /** @type {HTMLInputElement | null} */ (
        document.getElementById("confirm-input")
      );
      resolve(ok && input ? input.value.trim() : null);
    };
    const html = `<p>${esc(title)}</p>
      <input type="text" class="confirm-input" id="confirm-input"
        placeholder="${escapeAttr(placeholder)}" value="${escapeAttr(value)}">
      <div class="confirm-actions">
        <button data-action="resolveConfirm" data-arg0="false">Cancel</button>
        <button class="${escapeAttr(okClass)}" id="confirm-ok" data-action="resolveConfirm" data-arg0="true">${esc(okLabel)}</button>
      </div>`;
    _showConfirmOverlay(html);
    const input = /** @type {HTMLInputElement | null} */ (
      document.getElementById("confirm-input")
    );
    if (input) {
      input.focus();
      if (value) input.select();
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          resolveConfirm(true);
        }
      });
    }
  });
}

/**
 * Resolve the active dialog. Bound to the inline  in the
 * rendered button HTML, so this must be exported and bridged onto
 * window.
 *
 * @param {boolean} result
 */
export function resolveConfirm(result) {
  document.removeEventListener("keydown", _onConfirmKey, true);
  const overlay = document.getElementById("confirm-overlay");
  overlay?.classList.remove("visible");
  if (_confirmResolve) {
    _confirmResolve(result);
    _confirmResolve = null;
  }
}
