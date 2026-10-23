// @ts-check
/**
 * Photo grid multi-select: card click, range-click, UI refresh.
 *
 * Extracted from photos.mjs during the v0.1 cleanup.
 *
 *   * handleCardClick(event, idx) — click dispatcher
 *   * updateMultiSelectUI()       — repaint the action bar / counter
 *   * clearMultiSelect()          — drop selection, refresh
 *   * focusCard / _bppCardKeydown — keyboard: one focusable card at a
 *     time (roving tabindex), arrows move it, Home / End jump
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
  // A click (mouse, or Enter — the key handler re-marks it) ends keyboard
  // navigation: a re-render won't chase focus for a mouse user.
  if (win.vgrid) win.vgrid._keyNav = false;
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

/**
 * Where an arrow / Home / End key moves the grid's focus from ``idx``,
 * or null for a key that isn't navigation or a move off the grid.
 * @param {string} key
 * @param {number} idx
 * @param {number} cols
 * @param {number} count
 * @returns {number | null}
 */
export function nextCardIndex(key, idx, cols, count) {
  if (count <= 0) return null;
  const last = count - 1;
  switch (key) {
    case "ArrowRight":
      return idx < last ? idx + 1 : null;
    case "ArrowLeft":
      return idx > 0 ? idx - 1 : null;
    case "ArrowDown": {
      if (idx + cols <= last) return idx + cols;
      // A short last row below: land on its last card. Already on the
      // last row: stay (no sideways jump).
      const rowBelowExists = Math.floor(idx / cols) < Math.floor(last / cols);
      return rowBelowExists ? last : null;
    }
    case "ArrowUp":
      return idx - cols >= 0 ? idx - cols : null;
    case "Home":
      return idx > 0 ? 0 : null;
    case "End":
      return idx < last ? last : null;
    default:
      return null;
  }
}

/** Whether a dialog over the grid (lightbox, compare, slideshow) is open. */
function _viewerOpen() {
  const slideshow = document.getElementById("slideshow-overlay");
  return !!(
    document.getElementById("lightbox")?.classList.contains("visible") ||
    document.getElementById("compare-overlay")?.classList.contains("visible") ||
    (slideshow && !slideshow.classList.contains("hidden"))
  );
}

/**
 * Move the grid's one focusable card to ``idx`` and focus it: the row is
 * scrolled into view first so the virtual grid has rendered the card.
 * @param {number} idx
 */
export function focusCard(idx) {
  /** @type {any} */
  const win = window;
  const vg = win.vgrid;
  const grid = document.getElementById("photo-grid");
  if (!vg || !grid || idx < 0 || idx >= vg.items.length) return;
  vg._keyNav = true; // a keyboard move: the grid may restore focus after a re-render
  vg._movingFocus = true; // the scroll render below must not adopt / restore the old card
  const scrollEl = /** @type {HTMLElement | null} */ (document.querySelector(".content"));
  if (scrollEl && vg.rowHeight && vg.cols) {
    // The grid sits below other bars in the scroll area: measure from its top.
    const gridTop =
      grid.getBoundingClientRect().top - scrollEl.getBoundingClientRect().top + scrollEl.scrollTop;
    const top = gridTop + Math.floor(idx / vg.cols) * vg.rowHeight;
    const bottom = top + vg.rowHeight;
    if (top < scrollEl.scrollTop) scrollEl.scrollTop = top;
    else if (bottom > scrollEl.scrollTop + scrollEl.clientHeight) {
      scrollEl.scrollTop = bottom - scrollEl.clientHeight;
    }
    vg.render?.(false);
  }
  vg._movingFocus = false;
  vg.focusIdx = idx;
  for (const c of grid.querySelectorAll('.card[tabindex="0"]')) {
    /** @type {HTMLElement} */ (c).tabIndex = -1;
  }
  const card = /** @type {HTMLElement | null} */ (grid.querySelector(`.card[data-idx="${idx}"]`));
  if (card) {
    card.tabIndex = 0;
    card.focus({ preventScroll: true });
    card.scrollIntoView?.({ block: "nearest" });
  }
}

/**
 * ``data-onkeydown`` of a photo card (``this`` is the card).
 * @this {HTMLElement}
 * @param {KeyboardEvent} e
 */
export function _bppCardKeydown(e) {
  if (e.altKey || e.ctrlKey || e.metaKey) return;
  if (_viewerOpen()) return; // the viewer's own keys apply
  /** @type {any} */
  const vg = /** @type {any} */ (window).vgrid;
  if (!vg) return;
  if (e.key === "Enter" || e.key === " ") {
    // Open it like a click (the generic activation in globals.js leaves
    // elements with their own keydown handler alone).
    e.preventDefault();
    this.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    vg._keyNav = true;
    return;
  }
  const idx = +(this.dataset.idx || 0);
  const next = nextCardIndex(e.key, idx, vg.cols || 1, vg.items.length);
  if (next === null) return;
  e.preventDefault();
  focusCard(next);
}
