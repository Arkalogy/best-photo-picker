// @ts-check
/**
 * Lightbox zoom + pan + mouse-drag + touch gestures.
 *
 * Extracted from lightbox.mjs during the v0.1 cleanup. This module
 * owns the "pixel-level photo viewer" surface:
 *
 *   * Zoom state + transform: _lbApplyTransform, _lbClampPan,
 *     lbResetZoom, lbZoomAt, _lbShowZoomIndicator, _lbKeyZoom
 *   * Wheel + mouse drag pan + double-click toggle (IIFE)
 *   * Touch pinch-zoom + drag-pan + double-tap toggle + swipe-nav (IIFE)
 *
 * Shared cross-realm state (`lbZoom`, `lbPanX`, `lbPanY`, `LB_ZOOM_MIN`,
 * `LB_ZOOM_MAX`, `lightboxIdx`) lives on `window` (declared in
 * globals.js). The drag-state lets are module-local to this file.
 *
 * Re-exported from lightbox.mjs so the modules-bridge in
 * templates/index.html keeps exposing every public name on window.
 */

import { lightboxNav } from "./lightbox-actions.mjs";

let _lbDragging = false;
let _lbDragStartX = 0;
let _lbDragStartY = 0;
let _lbDragStartPanX = 0;
let _lbDragStartPanY = 0;
/** @type {ReturnType<typeof setTimeout> | null} */
let _lbZoomTimer = null;

const LB_ZOOM_WHEEL_FACTOR = 1.15;


export function lbResetZoom() {
  /** @type {any} */
  const win = window;
  win.lbZoom = 1;
  win.lbPanX = 0;
  win.lbPanY = 0;
  _lbDragging = false;
  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  if (img) {
    img.style.transform = "";
    img.style.transition = "";
    img.style.willChange = "";
  }
  const fc = /** @type {HTMLElement | null} */ (document.getElementById("lb-face-container"));
  if (fc) {
    fc.style.transform = "";
    fc.style.transition = "";
  }
  const wrapper = document.querySelector(".lb-img-wrapper");
  if (wrapper) wrapper.classList.remove("zoomed", "panning");
  const ind = document.getElementById("lb-zoom-level");
  if (ind) ind.classList.remove("visible");
}

/**
 * @param {boolean} smooth
 */
export function _lbApplyTransform(smooth) {
  /** @type {any} */
  const win = window;
  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  if (!img) return;
  const wrapper = /** @type {HTMLElement | null} */ (img.closest(".lb-img-wrapper"));
  const faceContainer = /** @type {HTMLElement | null} */ (
    document.getElementById("lb-face-container")
  );
  if (win.lbZoom <= 1) {
    img.style.transform = "";
    img.style.transition = "";
    img.style.willChange = "";
    if (faceContainer) {
      faceContainer.style.transform = "";
      faceContainer.style.transition = "";
    }
    if (wrapper) wrapper.classList.remove("zoomed", "panning");
    const _zpReset = document.getElementById("editor-zoom-pct");
    if (_zpReset) _zpReset.textContent = "100%";
    const _zsReset = /** @type {HTMLInputElement | null} */ (
      document.getElementById("editor-zoom-slider")
    );
    if (_zsReset) _zsReset.value = "1";
    return;
  }
  const tfm =
    "translate(" +
    win.lbPanX.toFixed(1) +
    "px," +
    win.lbPanY.toFixed(1) +
    "px) scale(" +
    win.lbZoom.toFixed(3) +
    ")";
  const trans = smooth ? "transform 0.15s ease-out" : "none";
  img.style.willChange = "transform";
  img.style.transition = trans;
  img.style.transform = tfm;
  if (faceContainer) {
    faceContainer.style.transition = trans;
    faceContainer.style.transform = tfm;
  }
  if (wrapper) {
    wrapper.classList.add("zoomed");
    wrapper.classList.toggle("panning", _lbDragging);
  }
  const _zpEl = document.getElementById("editor-zoom-pct");
  if (_zpEl) _zpEl.textContent = Math.round(win.lbZoom * 100) + "%";
  const _zsEl = /** @type {HTMLInputElement | null} */ (
    document.getElementById("editor-zoom-slider")
  );
  if (_zsEl) _zsEl.value = String(win.lbZoom);
}

export function _lbClampPan() {
  /** @type {any} */
  const win = window;
  if (win.lbZoom <= 1) return;
  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  if (!img) return;
  const w = img.clientWidth;
  const h = img.clientHeight;
  win.lbPanX = Math.max(w * (1 - win.lbZoom), Math.min(0, win.lbPanX));
  win.lbPanY = Math.max(h * (1 - win.lbZoom), Math.min(0, win.lbPanY));
}

export function _lbShowZoomIndicator() {
  /** @type {any} */
  const win = window;
  const el = document.getElementById("lb-zoom-level");
  if (!el) return;
  el.textContent = Math.round(win.lbZoom * 100) + "%";
  el.classList.add("visible");
  if (_lbZoomTimer) clearTimeout(_lbZoomTimer);
  _lbZoomTimer = setTimeout(() => el.classList.remove("visible"), 1200);
}

/**
 * @param {number} newZoom
 * @param {number} mx
 * @param {number} my
 */
export function lbZoomAt(newZoom, mx, my) {
  /** @type {any} */
  const win = window;
  newZoom = Math.max(win.LB_ZOOM_MIN, Math.min(win.LB_ZOOM_MAX, newZoom));
  if (Math.abs(newZoom - win.lbZoom) < 0.001) return;
  const ratio = newZoom / win.lbZoom;
  win.lbPanX = mx - (mx - win.lbPanX) * ratio;
  win.lbPanY = my - (my - win.lbPanY) * ratio;
  win.lbZoom = newZoom;
  if (win.lbZoom <= 1.01) {
    win.lbZoom = 1;
    win.lbPanX = 0;
    win.lbPanY = 0;
  }
  _lbClampPan();
  _lbApplyTransform(true);
  _lbShowZoomIndicator();
}

/**
 * @param {number} dir
 */
export function _lbKeyZoom(dir) {
  /** @type {any} */
  const win = window;
  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  if (!img) return;
  const cx = img.clientWidth / 2;
  const cy = img.clientHeight / 2;
  const factor = dir > 0 ? LB_ZOOM_WHEEL_FACTOR : 1 / LB_ZOOM_WHEEL_FACTOR;
  lbZoomAt(win.lbZoom * factor, cx, cy);
}

/**
 * @param {number} idx
 * @param {boolean} [animClass]
 */

// ── Wheel + mouse-drag pan + double-click toggle ──
(() => {
  /** @type {any} */
  const win = window;
  const wrapper = document.querySelector(".lb-img-wrapper");
  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  if (!wrapper || !img) return;

  wrapper.addEventListener("wheel", (e) => {
    if (win.lightboxIdx < 0) return;
    e.preventDefault();
    const we = /** @type {WheelEvent} */ (e);
    const rect = img.getBoundingClientRect();
    const mx = we.clientX - rect.left;
    const my = we.clientY - rect.top;
    const factor = we.deltaY < 0 ? LB_ZOOM_WHEEL_FACTOR : 1 / LB_ZOOM_WHEEL_FACTOR;
    lbZoomAt(win.lbZoom * factor, mx, my);
  }, { passive: false });

  img.addEventListener("dblclick", (e) => {
    if (win.lightboxIdx < 0) return;
    e.preventDefault();
    e.stopPropagation();
    const rect = img.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    if (win.lbZoom > 1.05) {
      win.lbZoom = 1;
      win.lbPanX = 0;
      win.lbPanY = 0;
      _lbApplyTransform(true);
      _lbShowZoomIndicator();
    } else {
      lbZoomAt(2, mx, my);
    }
  });

  wrapper.addEventListener("mousedown", (e) => {
    const me = /** @type {MouseEvent} */ (e);
    if (win.lightboxIdx < 0 || win.lbZoom <= 1 || me.button !== 0) return;
    e.preventDefault();
    _lbDragging = true;
    _lbDragStartX = me.clientX;
    _lbDragStartY = me.clientY;
    _lbDragStartPanX = win.lbPanX;
    _lbDragStartPanY = win.lbPanY;
    _lbApplyTransform(false);
  });

  document.addEventListener("mousemove", (e) => {
    if (!_lbDragging) return;
    win.lbPanX = _lbDragStartPanX + (e.clientX - _lbDragStartX);
    win.lbPanY = _lbDragStartPanY + (e.clientY - _lbDragStartY);
    _lbClampPan();
    _lbApplyTransform(false);
  });

  document.addEventListener("mouseup", () => {
    if (!_lbDragging) return;
    _lbDragging = false;
    _lbApplyTransform(false);
  });
})();

// ── Touch gestures ──
(() => {
  /** @type {any} */
  const win = window;
  const lb = document.getElementById("lightbox");
  if (!lb) return;

  let touchStartX = 0;
  let touchStartY = 0;
  let touchStartTime = 0;
  let pinchStartDist = 0;
  let pinchStartZoom = 1;
  let isPinching = false;
  let isTouchPanning = false;
  let touchPanStartX = 0;
  let touchPanStartY = 0;
  let touchPanStartPanX = 0;
  let touchPanStartPanY = 0;
  let lastTapTime = 0;

  /**
   * @param {Touch} a
   * @param {Touch} b
   */
  function dist(a, b) {
    const dx = a.clientX - b.clientX;
    const dy = a.clientY - b.clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }

  lb.addEventListener("touchstart", (e) => {
    if (win.lightboxIdx < 0) return;
    if (e.touches.length === 2) {
      isPinching = true;
      isTouchPanning = false;
      pinchStartDist = dist(e.touches[0], e.touches[1]);
      pinchStartZoom = win.lbZoom;
      e.preventDefault();
      return;
    }
    if (e.touches.length === 1) {
      touchStartX = e.touches[0].clientX;
      touchStartY = e.touches[0].clientY;
      touchStartTime = Date.now();
      if (win.lbZoom > 1) {
        isTouchPanning = true;
        touchPanStartX = e.touches[0].clientX;
        touchPanStartY = e.touches[0].clientY;
        touchPanStartPanX = win.lbPanX;
        touchPanStartPanY = win.lbPanY;
        e.preventDefault();
      }
    }
  }, { passive: false });

  lb.addEventListener("touchmove", (e) => {
    if (win.lightboxIdx < 0) return;
    if (isPinching && e.touches.length === 2) {
      e.preventDefault();
      const d = dist(e.touches[0], e.touches[1]);
      const newZoom = pinchStartZoom * (d / pinchStartDist);
      const midX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
      const midY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
      const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
      if (img) {
        const rect = img.getBoundingClientRect();
        lbZoomAt(newZoom, midX - rect.left, midY - rect.top);
      }
      return;
    }
    if (isTouchPanning && e.touches.length === 1) {
      e.preventDefault();
      win.lbPanX = touchPanStartPanX + (e.touches[0].clientX - touchPanStartX);
      win.lbPanY = touchPanStartPanY + (e.touches[0].clientY - touchPanStartY);
      _lbClampPan();
      _lbApplyTransform(false);
      return;
    }
  }, { passive: false });

  lb.addEventListener("touchend", (e) => {
    if (isPinching) {
      if (e.touches.length < 2) isPinching = false;
      return;
    }
    if (isTouchPanning) {
      isTouchPanning = false;
      return;
    }
    if (e.changedTouches.length === 1) {
      const now = Date.now();
      const dx0 = Math.abs(e.changedTouches[0].clientX - touchStartX);
      const dy0 = Math.abs(e.changedTouches[0].clientY - touchStartY);
      if (now - lastTapTime < 300 && dx0 < 20 && dy0 < 20) {
        const touch = e.changedTouches[0];
        const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
        if (img) {
          const rect = img.getBoundingClientRect();
          if (win.lbZoom > 1.05) {
            win.lbZoom = 1;
            win.lbPanX = 0;
            win.lbPanY = 0;
            _lbApplyTransform(true);
            _lbShowZoomIndicator();
          } else {
            lbZoomAt(2, touch.clientX - rect.left, touch.clientY - rect.top);
          }
        }
        lastTapTime = 0;
        return;
      }
      lastTapTime = now;
    }
    if (win.lbZoom > 1) return;
    const dx = e.changedTouches[0].clientX - touchStartX;
    const dy = e.changedTouches[0].clientY - touchStartY;
    const dt = Date.now() - touchStartTime;
    if (dt > 300) return;
    const absDx = Math.abs(dx);
    const absDy = Math.abs(dy);
    if (absDx < 50 || absDy > absDx * 0.7) return;
    if (dx < 0) lightboxNav(1);
    else lightboxNav(-1);
  }, { passive: true });
})();
