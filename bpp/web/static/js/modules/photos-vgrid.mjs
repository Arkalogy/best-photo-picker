// @ts-check
/**
 * Virtual grid renderer — the `vgrid` object that drives windowed
 * rendering of the photo grid.
 *
 * Extracted from photos.mjs during the v0.1 cleanup. Owns the
 * ~210-LOC vgrid object: viewport math, row-of-cards composition,
 * scroll-position pinning across re-renders, measurement
 * invalidation hooks.
 *
 * Re-exported from photos.mjs as `vgrid` so the rest of the codebase
 * keeps reaching it off `window.vgrid` via the bridge.
 */

import { renderCardHTML } from "./photos.mjs";


export const vgrid = {
  /** @type {any[]} */
  items: [],
  cols: 1,
  rowHeight: 0,
  totalRows: 0,
  firstRow: -1,
  lastRow: -1,
  buffer: 3,
  measured: false,
  _scrollRAF: 0,

  /** @param {any[]} items */
  setItems(items) {
    this.items = items;
    this.firstRow = -1;
    this.lastRow = -1;
    // Drop the per-card render cache — items may have changed shape
    // (different album, refreshed scores, etc.). Re-populates on demand.
    this._cardCache = new Map();
    this.measure();
    this.render(true);
  },

  /**
   * Cached card render — keyed by index + a signature of all per-item
   * state that affects the HTML output (selection / override / favorite
   * / multi-select / deleted / score). When the user scrolls back to a
   * previously-visited row range, the diff path's prepend/append loops
   * hit the cache instead of re-running the ~200-line template literal.
   *
   * State changes (selection toggle, favorite click, etc.) bypass this
   * by mutating the DOM directly elsewhere — they don't go through
   * renderCardHTML, so a stale cache entry would only manifest if the
   * user scrolls AWAY and BACK after the mutation. The cache key
   * includes those flags so even that case re-renders correctly.
   * @param {any} p photo item
   * @param {number} idx index into this.items
   */
  _cachedRender(p, idx) {
    /** @type {any} */
    const win = window;
    const isSel = (win.selectedPaths || new Set()).has(p.filepath);
    const ov = (win.overrides || {})[p.filepath] || "";
    const isFav = (win.favorites || new Set()).has(p.filepath);
    const isMulti = (win.multiSelected || new Set()).has(p.filepath);
    const sig = `${idx}|${isSel ? 1 : 0}|${ov}|${isFav ? 1 : 0}|${isMulti ? 1 : 0}|${p.deleted_at || ""}|${p.aggregate_score || 0}`;
    const cache = this._cardCache || (this._cardCache = new Map());
    let html = cache.get(sig);
    if (html === undefined) {
      html = renderCardHTML(p, idx);
      // Bound cache size — keep ~3 viewports worth of cards.
      if (cache.size > 600) {
        const firstKey = cache.keys().next().value;
        if (firstKey !== undefined) cache.delete(firstKey);
      }
      cache.set(sig, html);
    }
    return html;
  },

  measure() {
    const grid = document.getElementById("photo-grid");
    if (!grid || grid.clientWidth === 0) return;
    const style = getComputedStyle(grid);
    const thumbSize = parseFloat(style.getPropertyValue("--thumb-size")) || 260;
    const gap = parseFloat(style.gap) || 16;
    this.cols = Math.max(1, Math.floor((grid.clientWidth + gap) / (thumbSize + gap)));
    this.totalRows = Math.ceil(this.items.length / this.cols);

    if (!this.measured && this.items.length > 0) {
      const probe = document.createElement("div");
      probe.style.cssText = "position:absolute;visibility:hidden;pointer-events:none;";
      probe.className = "grid";
      probe.style.width = grid.clientWidth + "px";
      // Copy zoom-adjusted CSS variables from the grid so the probe card
      // measures at the correct size. Without this the probe inherits the
      // stylesheet defaults (--thumb-size:260, --thumb-height:220) instead
      // of the zoom-adjusted values set as inline styles on #photo-grid,
      // producing rowHeight=236 regardless of zoom level.
      const thumbH = parseFloat(style.getPropertyValue("--thumb-height")) || Math.round(thumbSize * 0.85);
      probe.style.setProperty("--thumb-size", thumbSize + "px");
      probe.style.setProperty("--thumb-height", thumbH + "px");
      probe.innerHTML = renderCardHTML(this.items[0], 0);
      document.body.appendChild(probe);
      const card = /** @type {HTMLElement | null} */ (probe.querySelector(".card"));
      if (card) {
        this.rowHeight = card.offsetHeight + gap;
        this.measured = true;
      }
      document.body.removeChild(probe);
    }
    if (!this.rowHeight) {
      const thumbH = parseFloat(style.getPropertyValue("--thumb-height")) || Math.round(thumbSize * 0.85);
      this.rowHeight = thumbH + 50 + gap;
    }
  },

  invalidateMeasure() {
    this.measured = false;
    this.firstRow = -1;
    this.lastRow = -1;
  },

  /** @param {boolean} [force] */
  render(force) {
    const grid = document.getElementById("photo-grid");
    const scrollEl = document.querySelector(".content");
    if (!grid || !scrollEl || this.items.length === 0 || !this.rowHeight) return;

    const scrollTop = scrollEl.scrollTop;
    const viewH = scrollEl.clientHeight || scrollEl.getBoundingClientRect().height;
    if (!viewH) { requestAnimationFrame(() => this.render(true)); return; }

    const fr = Math.max(0, Math.floor(scrollTop / this.rowHeight) - this.buffer);
    const lr = Math.min(
      this.totalRows - 1,
      Math.ceil((scrollTop + viewH) / this.rowHeight) + this.buffer
    );

    if (!force && fr === this.firstRow && lr === this.lastRow) return;

    const newStart = fr * this.cols;
    const newEnd = Math.min((lr + 1) * this.cols, this.items.length);
    const oldStart = this.firstRow >= 0 ? this.firstRow * this.cols : newStart;
    const oldEnd =
      this.lastRow >= 0 ? Math.min((this.lastRow + 1) * this.cols, this.items.length) : newStart;

    this.firstRow = fr;
    this.lastRow = lr;

    const topPad = fr * this.rowHeight;
    const bottomPad = Math.max(0, (this.totalRows - lr - 1) * this.rowHeight);
    /** @type {HTMLElement} */ (grid).style.paddingTop = topPad + "px";
    /** @type {HTMLElement} */ (grid).style.paddingBottom = bottomPad + "px";

    const canDiff =
      !force &&
      grid.children.length === oldEnd - oldStart &&
      newStart < oldEnd &&
      newEnd > oldStart;

    if (canDiff) {
      const removeTop = Math.max(0, newStart - oldStart);
      for (let i = 0; i < removeTop; i++) grid.firstElementChild?.remove();
      const removeBottom = Math.max(0, oldEnd - newEnd);
      for (let i = 0; i < removeBottom; i++) grid.lastElementChild?.remove();
      if (newStart < oldStart) {
        const frag = document.createDocumentFragment();
        const tmp = document.createElement("div");
        for (let i = newStart; i < oldStart; i++) {
          tmp.innerHTML = this._cachedRender(this.items[i], i);
          if (tmp.firstElementChild) frag.appendChild(tmp.firstElementChild);
        }
        grid.prepend(frag);
      }
      if (newEnd > oldEnd) {
        const frag = document.createDocumentFragment();
        const tmp = document.createElement("div");
        for (let i = oldEnd; i < newEnd; i++) {
          tmp.innerHTML = this._cachedRender(this.items[i], i);
          if (tmp.firstElementChild) frag.appendChild(tmp.firstElementChild);
        }
        grid.appendChild(frag);
      }
    } else {
      const html = [];
      for (let i = newStart; i < newEnd; i++) {
        html.push(this._cachedRender(this.items[i], i));
      }
      grid.innerHTML = html.join("");
    }

    const _resolveLoaded = () => {
      for (const img of /** @type {NodeListOf<HTMLImageElement>} */ (
        grid.querySelectorAll(".card-image.thumb-loading img")
      )) {
        if (img.complete && img.naturalWidth > 0) {
          const card = img.parentElement;
          if (card) {
            card.classList.remove("thumb-loading");
            img.style.opacity = "1";
          }
        }
      }
    };
    _resolveLoaded();
    requestAnimationFrame(_resolveLoaded);
  },

  onScroll() {
    if (this._scrollRAF) return;
    this._scrollRAF = requestAnimationFrame(() => {
      this._scrollRAF = 0;
      this.render(false);
    });
  },

  /** @param {any[]} newItems */
  appendItems(newItems) {
    this.items = this.items.concat(newItems);
    this.totalRows = Math.ceil(this.items.length / this.cols);
    this.render(true);
  },

  onResize() {
    this.invalidateMeasure();
    this.measure();
    this.render(true);
  },
};

