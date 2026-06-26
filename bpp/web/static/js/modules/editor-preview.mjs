// @ts-check
/**
 * Editor live-preview engine + before/after toggle + filter apply.
 *
 * Extracted from editor.mjs during the v0.1 cleanup. Owns the
 * CSS-filter-chain composition that drives every adjust slider's
 * real-time preview, the vignette overlay element, and the
 * before/after compare hold:
 *
 *   * _applyLivePreview         — compose CSS filter from _edits()
 *   * _updateVignetteOverlay    — vignette is an overlay div, not a
 *                                 CSS filter
 *   * _clearLivePreview         — strip CSS filters when editor closes
 *   * _showBefore / _hideBefore — press-and-hold compare
 *   * _applyFilter              — pick from BUILT_IN_FILTERS
 *
 * Re-exported from editor.mjs.
 */

import { state } from "./state.mjs";
import { BUILT_IN_FILTERS, EDITOR_DEFAULTS } from "./editor-constants.mjs";
import { _edits, _updateAllSliders } from "./editor.mjs";
import { _editorResetStyle, _refreshStylesTab } from "./editor-styles.mjs";


export function _applyLivePreview() {
  /** @type {any} */
  const win = window;
  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  if (!img || !win.editorActive) return;

  const e = _edits();
  const b = e.brightness || 1.0;
  const c = e.contrast || 1.0;
  const s = e.saturation || 1.0;

  const filters = [];

  const expo = e.exposure || 0;
  if (expo !== 0) {
    filters.push(`brightness(${Math.pow(2, expo)})`);
  }

  if (b !== 1.0) filters.push(`brightness(${b})`);
  if (c !== 1.0) filters.push(`contrast(${c})`);
  if (s !== 1.0) filters.push(`saturate(${s})`);

  const vib = e.vibrance || 0;
  if (vib !== 0) filters.push(`saturate(${1 + vib * 0.4})`);

  const w = e.warmth || 0;
  if (w > 0) filters.push(`sepia(${Math.min(w * 0.3, 0.25)})`);
  if (w < 0) filters.push(`hue-rotate(${w * 15}deg)`);

  const tintVal = e.tint || 0;
  if (tintVal !== 0) filters.push(`hue-rotate(${tintVal * 20}deg)`);

  const def = e.definition || 0;
  if (def !== 0) filters.push(`contrast(${1 + def * 0.15})`);

  const nr = e.noise_reduction || 0;
  if (nr > 0) filters.push(`blur(${nr * 1.5}px)`);

  img.style.filter = filters.length > 0 ? filters.join(" ") : "";

  const wrapper = /** @type {HTMLElement | null} */ (img.closest(".lb-img-wrapper"));
  if (wrapper) {
    const rot = e.rotation || 0;
    const fh = e.flip_h ? -1 : 1;
    const fv = e.flip_v ? -1 : 1;
    const str = e.straighten || 0;
    const pv = e.perspective_v || 0;
    const ph = e.perspective_h || 0;
    const parts = [];
    if (rot !== 0) parts.push(`rotate(${rot}deg)`);
    if (str !== 0) parts.push(`rotate(${str}deg)`);
    const totalRot = (((rot + (str || 0)) % 360) + 360) % 360;
    if (totalRot === 90 || totalRot === 270) {
      const iw = img.clientWidth;
      const ih = img.clientHeight;
      if (iw > 0 && ih > 0) {
        const fitScale = Math.min(iw / ih, ih / iw);
        parts.push(`scale(${(fitScale * fh).toFixed(4)}, ${(fitScale * fv).toFixed(4)})`);
      } else if (fh !== 1 || fv !== 1) {
        parts.push(`scale(${fh}, ${fv})`);
      }
    } else if (fh !== 1 || fv !== 1) {
      parts.push(`scale(${fh}, ${fv})`);
    }
    if (pv !== 0 || ph !== 0)
      parts.push(`perspective(800px) rotateX(${pv * 20}deg) rotateY(${ph * 20}deg)`);
    wrapper.style.transformOrigin = "center center";
    wrapper.style.transform = parts.length > 0 ? parts.join(" ") : "";
  }

  _updateVignetteOverlay();
}

export function _updateVignetteOverlay() {
  const wrapper = document.querySelector(".lb-img-wrapper");
  if (!wrapper) return;

  let overlay = /** @type {HTMLElement | null} */ (
    document.getElementById("editor-vignette-overlay")
  );
  const v = _edits().vignette || 0;

  if (v <= 0) {
    if (overlay) overlay.remove();
    return;
  }

  if (!overlay) {
    overlay = document.createElement("div");
    overlay.id = "editor-vignette-overlay";
    overlay.className = "editor-vignette-overlay";
    wrapper.appendChild(overlay);
  }

  const opacity = Math.min(v * 0.8, 0.7);
  overlay.style.background = `radial-gradient(circle, transparent 40%, rgba(0,0,0,${opacity}) 100%)`;
}

export function _clearLivePreview() {
  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  if (!img) return;
  img.style.filter = "";
  img.style.transform = "";
  const wrap = /** @type {HTMLElement | null} */ (img.closest(".lb-img-wrapper"));
  if (wrap) {
    wrap.style.transform = "";
    wrap.style.transformOrigin = "";
  }
  document.getElementById("editor-vignette-overlay")?.remove();
}

export function _showBefore() {
  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  if (!img) return;
  img.style.filter = "";
  img.style.transform = "";
  img.classList.add("editor-before");
  const overlay = /** @type {HTMLElement | null} */ (
    document.getElementById("editor-vignette-overlay")
  );
  if (overlay) overlay.style.display = "none";
}

export function _hideBefore() {
  /** @type {any} */
  const win = window;
  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  if (!img) return;
  img.classList.remove("editor-before");
  const overlay = /** @type {HTMLElement | null} */ (
    document.getElementById("editor-vignette-overlay")
  );
  if (overlay) overlay.style.display = "block";
  if (win.editorActive) _applyLivePreview();
}

/**
 * @param {string} name
 */
export function _applyFilter(name) {
  /** @type {any} */
  const win = window;
  const filter = BUILT_IN_FILTERS.find((f) => f.name === name);
  if (!filter) return;

  if (name === "None") {
    _editorResetStyle();
    return;
  }

  const e = _edits();
  /** @type {any} */
  const newEdits = { ...EDITOR_DEFAULTS, ...filter.params };
  newEdits.crop_x = e.crop_x;
  newEdits.crop_y = e.crop_y;
  newEdits.crop_w = e.crop_w;
  newEdits.crop_h = e.crop_h;
  newEdits.rotation = e.rotation;
  newEdits.flip_h = e.flip_h;
  newEdits.flip_v = e.flip_v;
  newEdits.straighten = e.straighten;
  newEdits.perspective_v = e.perspective_v;
  newEdits.perspective_h = e.perspective_h;
  newEdits.redeye_points = e.redeye_points;
  newEdits.filter_name = name;

  win.editorEdits = newEdits;
  _updateAllSliders();
  _applyLivePreview();
  _refreshStylesTab();
  // No toast: the live preview shows the filter on the image instantly —
  // restating it is noise.
}

