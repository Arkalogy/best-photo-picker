// @ts-check
/**
 * Editor adjust-tab tools: per-slider change handler, auto-enhance,
 * reset, all-sliders refresh, date editing, and rotate/flip transforms.
 *
 * Extracted from editor.mjs during the v0.1 cleanup. Re-exported from
 * editor.mjs.
 */

import { apiFetch } from "./api-client.mjs";
import { toast, toastError } from "./toast.mjs";
import { EDITOR_DEFAULTS } from "./editor-constants.mjs";
import { _applyLivePreview } from "./editor-preview.mjs";
import {
  _formatSliderValue,
  _renderCropControls,
  _renderSliders,
} from "./editor-rendering.mjs";
import { _refreshStylesTab } from "./editor-styles.mjs";
import { _edits } from "./editor.mjs";

export function _editorSliderChange(key, val) {
  if (val === undefined) { val = key; key = this.dataset.sliderKey; }
  const v = parseFloat(String(val));
  const e = _edits();
  e[key] = v;
  e.filter_name = null;

  const label = document.getElementById("ev-" + key);
  if (label) {
    if (key === "straighten") {
      label.textContent = v.toFixed(1) + "°";
    } else {
      label.textContent = _formatSliderValue(key, v);
    }
  }

  const slider = /** @type {HTMLInputElement | null} */ (document.getElementById("es-" + key));
  if (slider) {
    const min = parseFloat(slider.min);
    const max = parseFloat(slider.max);
    slider.style.setProperty("--pct", (((v - min) / (max - min)) * 100).toFixed(0) + "%");
  }

  _applyLivePreview();
}

export function _editorDateValue() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const p = items[win.lightboxIdx];
  if (!p) return "";
  const d = p.date || p.date_day || "";
  return d.length >= 16 ? d.slice(0, 16) : d.length >= 10 ? d.slice(0, 10) + "T00:00" : "";
}

/**
 * @param {string} val
 */
export async function _editorDateChanged(val) {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (!val || win.lightboxIdx < 0 || win.lightboxIdx >= items.length) return;
  const p = items[win.lightboxIdx];
  const newDate = val.length === 16 ? val + ":00" : val;
  if (newDate === p.date) return;
  try {
    const data = await apiFetch(`/api/v1/photos/${p.id}/date`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date: newDate }),
    });
    if (data.date) {
      p.date = data.date;
      p.date_day = data.date_day || data.date.slice(0, 10);
      p.date_month = data.date_month || data.date.slice(0, 7);
      toast("Date updated");
    }
  } catch (err) {
    console.error("Date update failed:", err);
    toastError("update the date", err);
  }
}

export async function _editorAutoEnhance() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const p = items[win.lightboxIdx];
  if (!p) return;
  try {
    const data = await apiFetch("/api/v1/photos/enhance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths: [p.filepath] }),
    });
    if (data.params && data.params[p.filepath]) {
      const params = data.params[p.filepath];
      const e = _edits();
      e.brightness = params.brightness;
      e.contrast = params.contrast;
      e.saturation = params.saturation;
      e.sharpness = params.sharpness;
      e.filter_name = null;
      _updateAllSliders();
      _refreshAdjustTab();
      _applyLivePreview();
      // No toast: the live preview + moved sliders show the enhancement
      // applied instantly. (Toast-noise audit item 8.)
    } else if (data.errors && data.errors[p.filepath]) {
      toastError("auto-enhance the photo", new Error(data.errors[p.filepath]));
    }
  } catch (err) {
    console.error("Auto-enhance error:", err);
    toastError("auto-enhance the photo", err);
  }
}

export function _editorReset() {
  /** @type {any} */
  const win = window;
  const e = _edits();
  const redeye = e.redeye_points;
  win.editorEdits = { ...EDITOR_DEFAULTS };
  win.editorEdits.redeye_points = redeye;
  win._activeAdjustSlider = null;
  _updateAllSliders();
  _refreshAdjustTab();
  _applyLivePreview();
  win._removeCropOverlay?.();
  win.editorCropActive = false;
  _refreshStylesTab();
  const cropTab = document.getElementById("editor-crop-tab");
  if (cropTab && !cropTab.classList.contains("hidden")) {
    cropTab.innerHTML = _renderCropControls();
  }
}

export function _updateAllSliders() {
  const e = _edits();
  const keys = [
    "brightness",
    "contrast",
    "saturation",
    "sharpness",
    "warmth",
    "highlights",
    "shadows",
    "vignette",
    "grain",
    "fade",
    "exposure",
    "brilliance",
    "black_point",
    "vibrance",
    "tint",
    "definition",
    "noise_reduction",
    "straighten",
    "perspective_v",
    "perspective_h",
  ];
  for (const key of keys) {
    const slider = /** @type {HTMLInputElement | null} */ (document.getElementById("es-" + key));
    const label = document.getElementById("ev-" + key);
    /** @type {any} */
    const defaults = EDITOR_DEFAULTS;
    const val = e[key] ?? defaults[key];
    if (slider) {
      slider.value = String(val);
      const min = parseFloat(slider.min);
      const max = parseFloat(slider.max);
      slider.style.setProperty("--pct", (((val - min) / (max - min)) * 100).toFixed(0) + "%");
    }
    if (label) {
      if (key === "straighten") {
        label.textContent = parseFloat(val).toFixed(1) + "°";
      } else {
        label.textContent = _formatSliderValue(key, val);
      }
    }
  }
}

export function _updateSliders() {
  _updateAllSliders();
}

export function _refreshAdjustTab() {
  const tab = document.getElementById("editor-adjust-tab");
  if (tab) tab.innerHTML = _renderSliders();
}

/**
 * @param {number} degrees
 */
export function _editorRotate(degrees) {
  const e = _edits();
  e.rotation = (((e.rotation || 0) + degrees + 360) % 360);
  _applyLivePreview();
  const cropTab = document.getElementById("editor-crop-tab");
  if (cropTab && !cropTab.classList.contains("hidden")) {
    cropTab.innerHTML = _renderCropControls();
  }
}

export function _editorFlipH() {
  const e = _edits();
  e.flip_h = !e.flip_h;
  _applyLivePreview();
  const cropTab = document.getElementById("editor-crop-tab");
  if (cropTab && !cropTab.classList.contains("hidden")) {
    cropTab.innerHTML = _renderCropControls();
  }
}

export function _editorFlipV() {
  const e = _edits();
  e.flip_v = !e.flip_v;
  _applyLivePreview();
  const cropTab = document.getElementById("editor-crop-tab");
  if (cropTab && !cropTab.classList.contains("hidden")) {
    cropTab.innerHTML = _renderCropControls();
  }
}
