// @ts-check
/**
 * Lightbox right-click context menu — show (with per-photo labels +
 * on-screen clamping) and hide.
 *
 * Split out of lightbox-actions.mjs (2026-06-17) to keep that file under
 * the 500-LOC cap. Re-exported through lightbox.mjs so the data-action /
 * data-oncontextmenu bridge keeps reaching them on window unchanged.
 */

import { sensitiveCtxLabel } from "./sensitive.mjs";

/**
 * @param {MouseEvent} e
 */
export function showLbCtxMenu(e) {
  /** @type {any} */
  const win = window;
  e.preventDefault();
  e.stopPropagation();
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx < 0) return;
  const p = items[win.lightboxIdx];
  const menu = /** @type {HTMLElement | null} */ (document.getElementById("lb-ctx-menu"));
  if (!menu) return;

  const overrides = /** @type {Record<string, string>} */ (win.overrides || {});
  const favorites = /** @type {Set<string>} */ (win.favorites || new Set());
  const faceClusters = /** @type {any[]} */ (win.faceClusters || []);
  const ov = overrides[p.filepath] || "";
  const inc = document.getElementById("lb-ctx-include");
  if (inc)
    inc.innerHTML =
      (ov === "include" ? "Clear Override" : "Include") + ' <span class="ctx-shortcut">↑</span>';
  const exc = document.getElementById("lb-ctx-exclude");
  if (exc)
    exc.innerHTML =
      (ov === "exclude" ? "Clear Override" : "Exclude") + ' <span class="ctx-shortcut">↓</span>';
  const fav = document.getElementById("lb-ctx-fav");
  if (fav)
    fav.innerHTML =
      (favorites.has(p.filepath) ? "Unfavorite" : "Favorite") +
      ' <span class="ctx-shortcut">F</span>';
  const enh = document.getElementById("lb-ctx-enhance");
  if (enh)
    enh.innerHTML =
      (p._enhanced ? "Revert Enhancement" : "Enhance") + ' <span class="ctx-shortcut">A</span>';
  const lbTag = /** @type {HTMLElement | null} */ (document.getElementById("lb-ctx-tag"));
  if (lbTag) {
    lbTag.style.display = faceClusters.length > 0 ? "flex" : "none";
    lbTag.innerHTML = 'Tag person… <span class="ctx-shortcut">T</span>';
  }
  const lbSens = /** @type {HTMLElement | null} */ (document.getElementById("lb-ctx-sensitive"));
  if (lbSens) lbSens.textContent = sensitiveCtxLabel(p);

  // Position at the click point, then clamp/flip so the menu stays
  // fully on-screen. We must show the menu before measuring (otherwise
  // offsetHeight reads as 0 while .hidden display:none is in effect).
  menu.style.left = e.clientX + "px";
  menu.style.top = e.clientY + "px";
  menu.classList.remove("hidden");
  const PAD = 8;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const w = menu.offsetWidth;
  const h = menu.offsetHeight;
  let left = e.clientX;
  let top = e.clientY;
  if (left + w + PAD > vw) left = Math.max(PAD, vw - w - PAD);
  if (top + h + PAD > vh) top = Math.max(PAD, vh - h - PAD);
  menu.style.left = left + "px";
  menu.style.top = top + "px";
}

export function hideLbCtxMenu() {
  document.getElementById("lb-ctx-menu")?.classList.add("hidden");
}
