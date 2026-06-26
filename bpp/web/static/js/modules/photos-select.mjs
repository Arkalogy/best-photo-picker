// @ts-check
/**
 * Photo grid multi-select: card click, range-click, UI refresh.
 *
 * Extracted from photos.mjs during the v0.1 cleanup.
 *
 *   * handleCardClick(event, idx) — click dispatcher
 *   * updateMultiSelectUI()       — repaint the action bar / counter
 *   * clearMultiSelect()          — drop selection, refresh
 *
 * Re-exported from photos.mjs.
 */

import { state } from "./state.mjs";
import { updateOverrideStats } from "./photos-batch.mjs";
import { expandMomentStack } from "./moments-stacks.mjs";


export function handleCardClick(event, idx) {
  if (idx === undefined) idx = +this.dataset.arg0;
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const multiSelected = /** @type {Set<string>} */ (win.multiSelected || new Set());

  if (event.metaKey || event.ctrlKey) {
    event.preventDefault();
    const p = items[idx];
    if (!p) return;
    if (multiSelected.has(p.filepath)) multiSelected.delete(p.filepath);
    else multiSelected.add(p.filepath);
    win.lastMultiClickIdx = idx;
    updateMultiSelectUI();
    return;
  }
  if (event.shiftKey && win.lastMultiClickIdx >= 0) {
    event.preventDefault();
    const lo = Math.min(win.lastMultiClickIdx, idx);
    const hi = Math.max(win.lastMultiClickIdx, idx);
    for (let i = lo; i <= hi; i++) {
      const p = items[i];
      if (p) multiSelected.add(p.filepath);
    }
    updateMultiSelectUI();
    return;
  }
  if (multiSelected.size > 0) {
    const p = items[idx];
    if (!p) return;
    if (multiSelected.has(p.filepath)) multiSelected.delete(p.filepath);
    else multiSelected.add(p.filepath);
    win.lastMultiClickIdx = idx;
    updateMultiSelectUI();
    return;
  }
  const cp = items[idx];
  if (cp && cp.deleted_at) return;
  // Moments album: a card is a burst "stack" — expand it in place (the
  // flyout of all its shots) rather than jumping to the single-photo
  // lightbox; a flyout thumb then opens the compare/prune overlay.
  if (cp && cp._momentSiblings && cp._momentSiblings.length) {
    expandMomentStack(idx);
    return;
  }
  win.openLightbox?.(idx);
}

export function updateMultiSelectUI() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const multiSelected = /** @type {Set<string>} */ (win.multiSelected || new Set());

  // Delta-update: only touch cards whose membership changed since the
  // last call, instead of iterating every visible .card on every
  // toggle. Tracks the previous selection in `_lastMultiSelected`.
  /** @type {Set<string>} */
  const prev = win._lastMultiSelected || new Set();
  /** @type {string[]} */
  const added = [];
  /** @type {string[]} */
  const removed = [];
  for (const fp of multiSelected) if (!prev.has(fp)) added.push(fp);
  for (const fp of prev) if (!multiSelected.has(fp)) removed.push(fp);

  if (added.length || removed.length) {
    // Build a filepath → card-index map once. Cheaper than N querySelector
    // calls when added+removed counts are small (the common case: 1).
    /** @type {Map<string, number>} */
    const indexByPath = new Map();
    for (let i = 0; i < items.length; i++) {
      if (items[i] && items[i].filepath) indexByPath.set(items[i].filepath, i);
    }
    for (const fp of added) {
      const idx = indexByPath.get(fp);
      if (idx === undefined) continue;
      const card = document.querySelector(`.card[data-idx="${idx}"]`);
      card?.classList.add("multi-selected");
    }
    for (const fp of removed) {
      const idx = indexByPath.get(fp);
      if (idx === undefined) continue;
      const card = document.querySelector(`.card[data-idx="${idx}"]`);
      card?.classList.remove("multi-selected");
    }
  }
  win._lastMultiSelected = new Set(multiSelected);
  const bar = document.getElementById("batch-bar");
  const count = multiSelected.size;
  if (count > 0) {
    const cnt = document.getElementById("batch-count");
    if (cnt) cnt.textContent = `${count} selected`;
    bar?.classList.add("visible");
  } else {
    bar?.classList.remove("visible");
  }
  const cmpBtn = /** @type {HTMLElement | null} */ (document.getElementById("batch-compare-btn"));
  if (cmpBtn) cmpBtn.style.display = count >= 2 ? "inline-block" : "none";
  win.updatePersonPhotoSelection?.(count, [...multiSelected]);
}

export function clearMultiSelect() {
  /** @type {any} */
  const win = window;
  const multiSelected = /** @type {Set<string>} */ (win.multiSelected || new Set());
  multiSelected.clear();
  win.lastMultiClickIdx = -1;
  updateMultiSelectUI();
}
