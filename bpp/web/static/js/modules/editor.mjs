// @ts-check
/**
 * Photo editor — non-destructive adjust sliders, crop, rotate, flip,
 * warmth, highlights/shadows, vignette, grain, fade, filters, red-eye.
 * Activates inside the lightbox; CSS filters drive live preview, edits
 * save to DB on Done.
 *
 * Editor state lives on `window` (declared in globals.js): `editorEdits`,
 * `editorActive`, `editorOriginalEdits`, `editorCropActive`,
 * `_redeyeMode`, `_inpaintMode`, `_editorRevertPending`,
 * `_cropSavedPerspective`, `_activeAdjustSlider`, `_editorAspectRatio`,
 * etc. Cross-module helpers (`_setAspectRatio`, `_clearCrop`,
 * `_toggleCropOverlay`, `_removeCropOverlay`, `_applyCropFromOverlay`,
 * `_renderRemoveControls`, `_showInpaintOverlay`, `_removeInpaintOverlay`,
 * `_clearRedeyePoints`, `_removeRedeyeOverlay`, `_toggleRedeyeMode`)
 * live in their own modules and are window-bridged.
 *
 * Cross-file callees that stay classic (lightbox.js): `lbZoom`,
 * `LB_ZOOM_MIN/MAX`, `_lbApplyTransform`, `_lbShowZoomIndicator`,
 * `lbResetZoom`, `openLightbox`, `updateLightboxActions`,
 * `updateLightboxFaces`, `_lbLeafletMap`, `_lbMapMarker` — all looked up
 * on `window`.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { state } from "./state.mjs";
import { toast, toastError } from "./toast.mjs";
import { updateCardInPlace } from "./photos.mjs";
import { vgrid } from "./photos.mjs";
import {
  ADJUST_SLIDERS,
  ASPECT_RATIOS,
  AUTO_SECTIONS,
  BUILT_IN_FILTERS,
  EDITOR_DEFAULTS,
  STYLE_COLORS,
  STYLE_GRID,
  STYLE_TONES,
} from "./editor-constants.mjs";
export {
  ADJUST_SLIDERS,
  ASPECT_RATIOS,
  AUTO_SECTIONS,
  BUILT_IN_FILTERS,
  EDITOR_DEFAULTS,
  STYLE_COLORS,
  STYLE_GRID,
  STYLE_TONES,
};

/** @returns {any} state.editorEdits, lazily initialized */
export function _edits() {
  /** @type {any} */
  const win = window;
  return win.editorEdits || (win.editorEdits = {});
}

export function _editorPhotoHasFaces() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx < 0 || !items[win.lightboxIdx]) return false;
  const p = items[win.lightboxIdx];
  return (p.face_count || 0) > 0;
}

/**
 * @param {string} [tab]
 */
export async function openEditor(tab = "adjust") {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx < 0) return;
  const p = items[win.lightboxIdx];

  const data = await apiFetch(`/api/v1/photos/edits?filepath=${encodeURIComponent(p.filepath)}`);
  const existing = data.edits || {};

  win.editorEdits = { ...EDITOR_DEFAULTS, ...existing };
  win.editorOriginalEdits = { ...win.editorEdits };
  win.editorActive = true;
  win.editorCropActive = false;
  win._redeyeMode = false;
  win._editorAspectRatio = null;

  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  if (img) img.src = authedSrc("/photo/" + p.thumb_hash + "?raw=1&t=" + Date.now());

  document.getElementById("lightbox")?.classList.add("editor-mode");
  const revertBtn = /** @type {HTMLButtonElement | null} */ (
    document.querySelector(".editor-revert-btn")
  );
  const hasEdits = Object.keys(existing).length > 0 || p._enhanced;
  if (revertBtn) {
    revertBtn.disabled = !hasEdits;
    revertBtn.title = hasEdits ? "Discard all edits and revert to original" : "No edits to revert";
  }
  _renderEditorPanel();
  _editorSwitchTab(tab);
  _applyLivePreview();
  const _zs = /** @type {HTMLInputElement | null} */ (document.getElementById("editor-zoom-slider"));
  if (_zs && win.lbZoom != null) _zs.value = String(win.lbZoom);
  const _zp = document.getElementById("editor-zoom-pct");
  if (_zp && win.lbZoom != null) _zp.textContent = Math.round(win.lbZoom * 100) + "%";
}

/**
 * @param {string} tab
 */
export async function _editorTabClick(tab) {
  /** @type {any} */
  const win = window;
  if (!win.editorActive) {
    await openEditor(tab);
  } else {
    _editorSwitchTab(tab);
  }
}

export async function _editorQuickRotate() {
  /** @type {any} */
  const win = window;
  if (!win.editorActive) await openEditor("adjust");
  const e = _edits();
  e.rotation = ((e.rotation || 0) + 90) % 360;
  _applyLivePreview();
}

/**
 * @param {string | number} v
 */
export function _editorSetZoom(v) {
  /** @type {any} */
  const win = window;
  const min = win.LB_ZOOM_MIN ?? 0.1;
  const max = win.LB_ZOOM_MAX ?? 10;
  let z = Math.max(min, Math.min(max, parseFloat(String(v))));
  if (z <= 1.01) {
    z = 1;
    win.lbPanX = 0;
    win.lbPanY = 0;
  }
  win.lbZoom = z;
  win._lbApplyTransform?.(true);
  win._lbShowZoomIndicator?.();
  const s = /** @type {HTMLInputElement | null} */ (document.getElementById("editor-zoom-slider"));
  if (s) s.value = String(z);
}

export function _editorZoomIn() {
  /** @type {any} */
  const win = window;
  _editorSetZoom((win.lbZoom || 1) * 1.3);
}

export function _editorZoomOut() {
  /** @type {any} */
  const win = window;
  _editorSetZoom((win.lbZoom || 1) / 1.3);
}

/**
 * @param {string | number} v
 */
export function _editorOnZoomSlider(v) {
  _editorSetZoom(v);
}

/**
 * @param {string} thumbHash
 * @param {string} cacheBust
 */
function _bustGridThumb(thumbHash, cacheBust) {
  const grid = document.getElementById("photo-grid");
  if (!grid) return;
  const img = /** @type {HTMLImageElement | null} */ (
    grid.querySelector(`img[src*="/thumb/${thumbHash}"]`)
  );
  if (img) {
    img.src = authedSrc("/thumb/" + thumbHash + cacheBust);
  } else if (vgrid) {
    vgrid.invalidateMeasure();
  }
}

export async function _editorRevertToOriginal() {
  /** @type {any} */
  const win = window;
  if (win._editorRevertPending) return;
  win._editorRevertPending = true;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const p = items[win.lightboxIdx];
  if (!p) {
    win._editorRevertPending = false;
    return;
  }

  try {
    if (win.editorActive) {
      win.editorEdits = { ...EDITOR_DEFAULTS };
      win.editorOriginalEdits = { ...EDITOR_DEFAULTS };
      win._removeCropOverlay?.();
      win.editorCropActive = false;
      win._cropSavedPerspective = null;
      _renderEditorPanel();
      _editorSwitchTab("adjust");
      _applyLivePreview();
      win.lbResetZoom?.();
      const s = /** @type {HTMLInputElement | null} */ (
        document.getElementById("editor-zoom-slider")
      );
      if (s) s.value = "1";
    }

    await apiFetch("/api/v1/photos/reset-edits", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths: [p.filepath] }),
    });
    p._enhanced = false;

    const cacheBust = "?t=" + Date.now();
    const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
    if (img) img.src = authedSrc("/photo/" + p.thumb_hash + cacheBust);
    _bustGridThumb(p.thumb_hash, cacheBust);
    updateCardInPlace(p.filepath);
    // Remove pencil badge from grid card — updateCardInPlace doesn't touch it.
    const grid = document.getElementById("photo-grid");
    if (grid) {
      const card = [...grid.querySelectorAll(".card")]
        .find((c) => /** @type {HTMLElement} */ (c).dataset.filepath === p.filepath);
      if (card) card.querySelector(".edited-badge")?.remove();
    }
    win.updateLightboxFaces?.(p);
    toast("Reverted to original");
    win.loadAlbumList?.(); // refresh smart_enhanced album count in sidebar
    // Full-screen flash div appended to body — nothing can clip or override it.
    const flash = document.createElement("div");
    flash.style.cssText = "position:fixed;inset:0;z-index:999999;background:rgba(255,159,10,0.25);pointer-events:none;transition:opacity 0.35s ease";
    document.body.appendChild(flash);
    setTimeout(() => { flash.style.opacity = "0"; }, 150);
    setTimeout(() => { flash.remove(); }, 520);
    const revertBtn = /** @type {HTMLButtonElement | null} */ (
      document.querySelector(".editor-revert-btn")
    );
    if (revertBtn) {
      revertBtn.disabled = true;
      revertBtn.title = "No edits to revert";
    }
  } catch (err) {
    toastError("revert to the original", err);
  } finally {
    win._editorRevertPending = false;
  }
}

/**
 * @param {boolean} save
 */
export function closeEditor(save) {
  /** @type {any} */
  const win = window;
  if (win.editorCropActive) win._applyCropFromOverlay?.();

  win.editorActive = false;
  win.editorCropActive = false;
  win._redeyeMode = false;
  win._inpaintMode = false;
  document.getElementById("lightbox")?.classList.remove("editor-mode", "editor-tab-crop");
  document.querySelectorAll(".editor-tab").forEach((t) => t.classList.remove("active"));
  win._removeCropOverlay?.();
  win._removeRedeyeOverlay?.();
  win._removeInpaintOverlay?.();
  _clearLivePreview();

  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const p = items[win.lightboxIdx];
  if (save) {
    _saveEdits(p)
      .then(() => {
        if (p) win.updateLightboxActions?.(p);
      })
      .catch((err) => {
        console.error("Failed to save edits:", err);
        toastError("save your edits", err);
      });
  } else if (p) {
    const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
    if (img) img.src = authedSrc("/photo/" + p.thumb_hash + "?t=" + Date.now());
  }

  _restoreInfoPanel();
}

/**
 * @param {any} [p]
 */
async function _saveEdits(p) {
  /** @type {any} */
  const win = window;
  if (!p) {
    const items = /** @type {any[]} */ (win.currentGridItems || []);
    p = items[win.lightboxIdx];
  }
  if (!p) return;

  const e = _edits();
  const hasChanges = _editsHaveChanges(e);

  try {
    if (hasChanges) {
      await apiFetch("/api/v1/photos/save-edits", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filepath: p.filepath, edits: e }),
      });
      p._enhanced = true;
      toast("Edits saved");
    } else {
      await apiFetch("/api/v1/photos/reset-edits", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filepaths: [p.filepath] }),
      });
      p._enhanced = false;
    }

    const cacheBust = "?t=" + Date.now();
    const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
    if (img) img.src = authedSrc("/photo/" + p.thumb_hash + cacheBust);
    _bustGridThumb(p.thumb_hash, cacheBust);
    updateCardInPlace(p.filepath);
  } catch (err) {
    toastError("save your edits", err);
  }
}

/**
 * @param {any} e
 * @returns {boolean}
 */
function _editsHaveChanges(e) {
  if (e.brightness !== 1.0 || e.contrast !== 1.0 || e.saturation !== 1.0 || e.sharpness !== 1.0)
    return true;
  if (e.crop_x != null) return true;
  if (e.rotation !== 0) return true;
  if (e.flip_h || e.flip_v) return true;
  if (e.warmth !== 0.0 || e.highlights !== 0.0 || e.shadows !== 0.0) return true;
  if (e.vignette !== 0.0 || e.grain !== 0.0 || e.fade !== 0.0) return true;
  if (e.redeye_points && e.redeye_points.length > 0) return true;
  if (e.exposure !== 0.0 || e.brilliance !== 0.0 || e.black_point !== 0.0) return true;
  if (e.vibrance !== 0.0 || e.tint !== 0.0) return true;
  if (e.definition !== 0.0 || e.noise_reduction !== 0.0) return true;
  if (e.straighten !== 0.0 || e.perspective_v !== 0.0 || e.perspective_h !== 0.0) return true;
  return false;
}


import {
  _applyCustomRatio,
  _applyStyle,
  _autoSection,
  _autoStraighten,
  _editorResetStyle,
  _filterPreviewStyle,
  _refreshStylesTab,
  _renderStylesGrid,
  _showCustomRatio,
  _stylePreviewCss,
  _toggleBWMode,
} from "./editor-styles.mjs";
export {
  _applyCustomRatio,
  _applyStyle,
  _autoSection,
  _autoStraighten,
  _editorResetStyle,
  _filterPreviewStyle,
  _refreshStylesTab,
  _renderStylesGrid,
  _showCustomRatio,
  _stylePreviewCss,
  _toggleBWMode,
};

import {
  _applyFilter,
  _applyLivePreview,
  _clearLivePreview,
  _hideBefore,
  _showBefore,
  _updateVignetteOverlay,
} from "./editor-preview.mjs";
export {
  _applyFilter,
  _applyLivePreview,
  _clearLivePreview,
  _hideBefore,
  _showBefore,
  _updateVignetteOverlay,
};

import {
  _closeAdjustSlider,
  _editorSwitchTab,
  _formatSliderValue,
  _isSliderModified,
  _openAdjustSlider,
  _renderAdjustGrid,
  _renderCropControls,
  _renderEditorPanel,
  _renderFoldOut,
  _renderSliders,
  _restoreInfoPanel,
} from "./editor-rendering.mjs";
export {
  _closeAdjustSlider,
  _editorSwitchTab,
  _formatSliderValue,
  _isSliderModified,
  _openAdjustSlider,
  _renderAdjustGrid,
  _renderCropControls,
  _renderEditorPanel,
  _renderFoldOut,
  _renderSliders,
  _restoreInfoPanel,
};

import {
  _editorAutoEnhance,
  _editorDateChanged,
  _editorDateValue,
  _editorFlipH,
  _editorFlipV,
  _editorReset,
  _editorRotate,
  _editorSliderChange,
  _refreshAdjustTab,
  _updateAllSliders,
  _updateSliders,
} from "./editor-tools.mjs";
export {
  _editorAutoEnhance,
  _editorDateChanged,
  _editorDateValue,
  _editorFlipH,
  _editorFlipV,
  _editorReset,
  _editorRotate,
  _editorSliderChange,
  _refreshAdjustTab,
  _updateAllSliders,
  _updateSliders,
};
