// @ts-check
/**
 * Editor styles + auto-enhance + custom-ratio + filter preview helpers.
 *
 * Extracted from editor.mjs during the v0.1 cleanup. Owns the
 * "styles tab" panel rendering and the auto/B&W operations that
 * pair with it:
 *
 *   * _renderStylesGrid / _stylePreviewCss — styles tab paint
 *   * _applyStyle / _editorResetStyle / _refreshStylesTab
 *   * _toggleBWMode  — B&W tint toggle
 *   * _autoSection / _autoStraighten — auto-enhance hooks
 *   * _showCustomRatio / _applyCustomRatio — custom crop ratio
 *   * _filterPreviewStyle — render CSS filter chain for previews
 *
 * Re-exported from editor.mjs so existing data-action handlers
 * reach them off window.
 */

import { apiFetch } from "./api-client.mjs";
import { escapeAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";
import {
  BUILT_IN_FILTERS,
  EDITOR_DEFAULTS,
  STYLE_COLORS,
  STYLE_GRID,
  STYLE_TONES,
} from "./editor-constants.mjs";
import {
  _applyLivePreview,
  _edits,
  _refreshAdjustTab,
  _renderSliders,

  _updateAllSliders,
} from "./editor.mjs";


export function _renderStylesGrid() {
  const e = _edits();
  const currentFilter = e.filter_name || "";

  let html = '<div class="editor-styles-section">';
  html += '<div class="editor-section-label">Style</div>';
  html += '<div class="editor-styles-grid">';

  html += '<div class="editor-styles-grid-cell editor-styles-grid-corner"></div>';
  for (const color of STYLE_COLORS) {
    html += `<div class="editor-styles-grid-cell editor-styles-grid-header">${color}</div>`;
  }

  for (const tone of STYLE_TONES) {
    html += `<div class="editor-styles-grid-cell editor-styles-grid-row-label">${tone}</div>`;
    for (const color of STYLE_COLORS) {
      const key = `${tone}|${color}`;
      const isActive = currentFilter === key;
      html += `
        <button class="editor-styles-grid-cell editor-styles-grid-btn${isActive ? " active" : ""}"
          data-action="_applyStyle" data-arg0="${escapeAttr(key)}" title="${escapeAttr(tone)} ${escapeAttr(color)}">
          <span class="editor-style-preview" style="${_stylePreviewCss(STYLE_GRID[key] || {})}"></span>
        </button>
      `;
    }
  }
  html += "</div>";

  html += '<div class="editor-section-label" style="margin-top:12px">Classic Presets</div>';
  html += '<div class="editor-filters-grid">';
  for (const f of BUILT_IN_FILTERS) {
    const isActive = f.name === currentFilter;
    html += `
      <button class="editor-filter-chip${isActive ? " active" : ""}"
        data-action="_applyFilter" data-arg0="${escapeAttr(f.name)}" title="${escapeAttr(f.name)}">
        <span class="editor-filter-preview" style="${_filterPreviewStyle(f.params)}"></span>
        <span class="editor-filter-name">${f.name}</span>
      </button>
    `;
  }
  html += "</div>";

  const pts = e.redeye_points;
  if (pts && pts.length > 0) {
    html += `<div class="editor-redeye-status">${pts.length} red-eye fix${pts.length > 1 ? "es" : ""} applied
      <button class="editor-redeye-clear" data-action="_clearRedeyePoints">Clear all</button>
    </div>`;
  }

  html +=
    '<button class="editor-btn editor-btn-reset" style="margin-top:12px;width:100%" data-action="_editorResetStyle">Reset Style</button>';
  html += "</div>";

  return html;
}

/**
 * @param {Record<string, number>} params
 */
export function _stylePreviewCss(params) {
  const parts = [];
  if (params.brightness && params.brightness !== 1.0) parts.push(`brightness(${params.brightness})`);
  if (params.contrast && params.contrast !== 1.0) parts.push(`contrast(${params.contrast})`);
  if (params.saturation !== undefined && params.saturation !== 1.0)
    parts.push(`saturate(${params.saturation})`);
  if (params.warmth && params.warmth > 0)
    parts.push(`sepia(${Math.min(params.warmth * 0.4, 0.3)})`);
  if (params.warmth && params.warmth < 0) parts.push(`hue-rotate(${params.warmth * 15}deg)`);
  return parts.length > 0 ? `filter: ${parts.join(" ")}` : "";
}

/**
 * @param {string} key
 */
export function _applyStyle(key) {
  /** @type {any} */
  const win = window;
  const params = STYLE_GRID[key];
  if (!params) return;

  const isNone = key === "Standard|Neutral";
  if (isNone) {
    _editorResetStyle();
    return;
  }

  const e = _edits();
  const newEdits = { ...EDITOR_DEFAULTS, ...params };
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
  newEdits.filter_name = key;

  win.editorEdits = newEdits;
  _updateAllSliders();
  _applyLivePreview();
  _refreshStylesTab();
  // No toast: the live preview applies the style on the image instantly and
  // the Styles tab highlights the selection — restating it is noise.
}

export function _editorResetStyle() {
  /** @type {any} */
  const win = window;
  const preserve = [
    "crop_x",
    "crop_y",
    "crop_w",
    "crop_h",
    "rotation",
    "flip_h",
    "flip_v",
    "straighten",
    "perspective_v",
    "perspective_h",
    "redeye_points",
  ];
  const e = _edits();
  /** @type {any} */
  const newEdits = { ...EDITOR_DEFAULTS };
  for (const k of preserve) newEdits[k] = e[k];
  win.editorEdits = newEdits;
  _updateAllSliders();
  _applyLivePreview();
  _refreshStylesTab();
}

export function _refreshStylesTab() {
  const tab = document.getElementById("editor-styles-tab");
  if (tab && !tab.classList.contains("hidden")) {
    tab.innerHTML = _renderStylesGrid();
  }
}

export function _toggleBWMode() {
  const e = _edits();
  const isBW = (e.saturation ?? 1.0) === 0.0;
  if (isBW) {
    e.saturation = EDITOR_DEFAULTS.saturation;
  } else {
    e.saturation = 0.0;
  }
  _updateAllSliders();
  _applyLivePreview();
  const adjustTab = document.getElementById("editor-adjust-tab");
  if (adjustTab && !adjustTab.classList.contains("hidden")) {
    adjustTab.innerHTML = _renderSliders();
  }
}

/**
 * @param {string} section
 */
export async function _autoSection(section) {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const p = win.lightboxIdx >= 0 ? items[win.lightboxIdx] : null;
  if (!p) return;
  /** @type {Record<string, string[]>} */
  const sectionKeys = {
    Light: ["exposure", "brilliance", "brightness", "contrast", "highlights", "shadows", "black_point"],
    Color: ["saturation", "vibrance", "warmth", "tint"],
  };
  const keys = sectionKeys[section];
  if (!keys) return;
  try {
    const data = await apiFetch(`/api/v1/photos/enhance-preview?filepath=${encodeURIComponent(p.filepath)}`);
    if (!data?.params) return;
    const e = _edits();
    for (const k of keys) {
      if (data.params[k] !== undefined) e[k] = data.params[k];
    }
    _updateAllSliders();
    _refreshAdjustTab();
    _applyLivePreview();
    toast(`${section} auto-optimized`);
  } catch (err) {
    console.error("Auto section failed:", err);
    toastError("auto-optimize the photo", err);
  }
}

export async function _autoStraighten() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const p = win.lightboxIdx >= 0 ? items[win.lightboxIdx] : null;
  if (!p) return;
  const btn = /** @type {HTMLButtonElement | null} */ (
    document.querySelector(".editor-straighten-section .editor-section-auto-btn")
  );
  if (btn) {
    btn.disabled = true;
    btn.textContent = "…";
  }
  try {
    const data = await apiFetch(`/api/v1/photos/${p.id}/auto_straighten`);
    if (data?.angle !== undefined) {
      const e = _edits();
      e.straighten = data.angle;
      _updateAllSliders();
      _applyLivePreview();
      toast(`Auto-straighten: ${data.angle.toFixed(1)}°`);
    }
  } catch (err) {
    console.error("Auto-straighten failed:", err);
    toastError("auto-straighten the photo", err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "AUTO";
    }
  }
}

export function _showCustomRatio() {
  const row = document.getElementById("editor-custom-ratio-row");
  if (row) {
    row.classList.remove("hidden");
    const inp = /** @type {HTMLInputElement | null} */ (
      document.getElementById("editor-custom-ratio-input")
    );
    if (inp) inp.focus();
  }
}

export function _applyCustomRatio() {
  /** @type {any} */
  const win = window;
  const inp = /** @type {HTMLInputElement | null} */ (
    document.getElementById("editor-custom-ratio-input")
  );
  if (!inp || !inp.value.trim()) return;
  const m = inp.value.trim().match(/^(\d+(?:\.\d+)?)\s*[:x/]\s*(\d+(?:\.\d+)?)$/i);
  if (!m) {
    toast("Use format like 16:9 or 4:3", true);
    return;
  }
  const ratio = parseFloat(m[1]) / parseFloat(m[2]);
  if (!isFinite(ratio) || ratio <= 0) {
    toast("Invalid ratio", true);
    return;
  }
  const btn = /** @type {HTMLElement | null} */ (
    document.getElementById("editor-custom-ratio-btn")
  );
  if (btn) btn.textContent = inp.value.trim();
  win._setAspectRatio?.(ratio, btn);
  document.getElementById("editor-custom-ratio-row")?.classList.add("hidden");
}

/**
 * @param {Record<string, number>} params
 */
export function _filterPreviewStyle(params) {
  const parts = [];
  if (params.brightness && params.brightness !== 1.0) parts.push(`brightness(${params.brightness})`);
  if (params.contrast && params.contrast !== 1.0) parts.push(`contrast(${params.contrast})`);
  if (params.saturation !== undefined && params.saturation !== 1.0)
    parts.push(`saturate(${params.saturation})`);
  if (params.warmth && params.warmth > 0)
    parts.push(`sepia(${Math.min(params.warmth * 0.4, 0.3)})`);
  return parts.length > 0 ? `filter: ${parts.join(" ")}` : "";
}
