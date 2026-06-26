// @ts-check
/**
 * Lightweight navigation helpers — persist current view to localStorage
 * (so a refresh restores the album/lightbox you were on) and render
 * the breadcrumb in the toolbar title.
 *
 * Reads classic globals via `window` (`currentView`, `currentAlbumId`,
 * `currentViewId`, `lightboxIdx`, `currentGridItems`).
 */

import { esc, escapeAttr } from "./text-format.mjs";

/** @typedef {{view: string, albumId: number | null, viewId: string | null, lightboxPath?: string, filter?: string}} NavState */

export function saveNavState() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const filterEl = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
  /** @type {NavState} */
  const state = {
    view: win.currentView,
    albumId: win.currentAlbumId,
    viewId: win.currentViewId,
    filter: filterEl?.value || "all",
  };
  if (typeof win.lightboxIdx === "number" && win.lightboxIdx >= 0 && items[win.lightboxIdx]) {
    state.lightboxPath = items[win.lightboxIdx].filepath;
  }
  try {
    localStorage.setItem("bpp_nav", JSON.stringify(state));
  } catch {
    /* localStorage quota or disabled — best-effort */
  }
}

/** @returns {NavState | null} */
export function getSavedNavState() {
  try {
    const raw = localStorage.getItem("bpp_nav");
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Render the toolbar title — either a plain string or a "Parent / Current"
 * breadcrumb where Parent is a clickable link.
 *
 * @param {string} title
 * @param {string} [parentLabel]
 * @param {string} [parentAction]
 */
export function updateBreadcrumbs(title, parentLabel, parentAction) {
  const el = document.getElementById("toolbar-title");
  if (!el) return;
  if (parentLabel && parentAction) {
    // Parse the action into the data-action dispatch convention
    // (`data-action="fn" data-arg0="v"`). The old code only stripped a
    // trailing empty "()", so "switchToLibrary()" worked but
    // "navigateTo('people')" was left intact as the data-action value — which
    // the dispatcher can't resolve, so the Faces/Pets/Groups parent crumb did
    // nothing. Match `fn` or `fn('arg')` / `fn("arg")`.
    const m = /^(\w+)\(\s*(?:'([^']*)'|"([^"]*)")?\s*\)$/.exec(parentAction.trim());
    const fn = m ? m[1] : parentAction.replace(/\(\)$/, "");
    const arg = m ? (m[2] ?? m[3]) : undefined;
    const argAttr = arg !== undefined ? ` data-arg0="${escapeAttr(arg)}"` : "";
    el.innerHTML = `<span class="bc-link" data-action="${escapeAttr(fn)}"${argAttr}>${esc(parentLabel)}</span><span class="bc-sep">/</span><span class="bc-current">${esc(title)}</span>`;
  } else {
    el.textContent = title;
  }
}
