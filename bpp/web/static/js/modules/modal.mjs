// @ts-check
/**
 * Generic icon + title + body modal.
 *
 * Distinct from `appConfirm` (in dialogs.mjs):
 *   - `appConfirm` uses `#confirm-overlay` / `.confirm-dialog` and is
 *     plain-text; ideal for yes/no decisions.
 *   - `showModal` uses `#modal-overlay` and renders an icon + title +
 *     body block; used for richer notifications like "No faces
 *     detected" with an emoji + explanation.
 *
 * Bridged onto window — `resolveModal` and `closeModal` are referenced
 * from inline `` and from the `<div data-action="closeModal" data-pass-event="true">`
 * backdrop attribute.
 */

/** @type {((value: any) => void) | null} */
let _modalResolve = null;
/** @type {((e: KeyboardEvent) => void) | null} */
let _modalKeyHandler = null;

/**
 * Show a modal with an icon, title, body, and either an OK button or
 * Cancel + Confirm pair.
 *
 * @param {string} icon - Single character or emoji to render in the icon slot.
 * @param {string} title
 * @param {string} body
 * @param {{ confirm?: string, danger?: boolean }} [opts] -
 *   When `confirm` is set, renders Cancel + that label as a confirm
 *   button. `danger` styles the confirm button as destructive.
 * @returns {Promise<boolean>}
 */
export function showModal(icon, title, body, opts) {
  const iconEl = document.getElementById("modal-icon");
  const titleEl = document.getElementById("modal-title");
  const bodyEl = document.getElementById("modal-body");
  const actionsEl = document.getElementById("modal-actions");
  const overlayEl = document.getElementById("modal-overlay");
  if (!iconEl || !titleEl || !bodyEl || !actionsEl || !overlayEl) {
    return Promise.resolve(false);
  }

  iconEl.textContent = icon;
  titleEl.textContent = title;
  bodyEl.textContent = body;

  if (opts && opts.confirm) {
    const dangerClass = opts.danger ? "modal-btn-danger" : "modal-btn-primary";
    actionsEl.innerHTML = `
      <button class="modal-btn modal-btn-secondary" data-action="resolveModal" data-arg0="false">Cancel</button>
      <button class="modal-btn ${dangerClass}" data-action="resolveModal" data-arg0="true">${opts.confirm}</button>
    `;
  } else {
    actionsEl.innerHTML =
      '<button class="modal-btn modal-btn-primary" data-action="resolveModal" data-arg0="true">OK</button>';
  }

  overlayEl.classList.add("visible");

  // Esc dismissal — capture-phase + stopImmediatePropagation so an
  // open showModal sitting over the lightbox doesn't also pass Esc
  // through to the lightbox handler underneath. Cleaned up in
  // resolveModal so it never accumulates across modal opens.
  _modalKeyHandler = (e) => {
    if (e.key === "Escape") {
      e.stopImmediatePropagation();
      e.preventDefault();
      resolveModal(false);
    }
  };
  document.addEventListener("keydown", _modalKeyHandler, true);

  return new Promise((resolve) => {
    _modalResolve = resolve;
  });
}

/**
 * Resolve the active modal. Bound to inline  in the
 * rendered button HTML, so this must be on window.
 *
 * @param {any} value
 */
export function resolveModal(value) {
  const overlay = document.getElementById("modal-overlay");
  overlay?.classList.remove("visible");
  if (_modalKeyHandler) {
    document.removeEventListener("keydown", _modalKeyHandler, true);
    _modalKeyHandler = null;
  }
  if (_modalResolve) {
    _modalResolve(value);
    _modalResolve = null;
  }
}

/**
 * Close the modal when the user clicks the backdrop. Inline:
 *   <div id="modal-overlay" data-action="closeModal" data-pass-event="true">
 * Click bubbles from inner elements; we only treat clicks on the
 * overlay itself as a close.
 *
 * @param {MouseEvent} [e]
 */
export function closeModal(e) {
  if (e && e.target !== document.getElementById("modal-overlay")) return;
  resolveModal(false);
}
