// @ts-check
/**
 * Editor panel rendering + slider fold-outs + tabs + crop-controls
 * panel + restore-info-panel teardown.
 *
 * Extracted from editor.mjs during the v0.1 cleanup. Owns the
 * "what's painted in the right-hand editor panel" surface:
 *
 *   * _renderEditorPanel  — top-level layout per tab
 *   * _renderSliders / _renderAdjustGrid / _renderFoldOut
 *     — adjust-tab paint
 *   * _openAdjustSlider / _closeAdjustSlider / _formatSliderValue
 *     — sub-slider expander
 *   * _isSliderModified   — predicate used by the highlight
 *   * _renderCropControls — crop-tab paint
 *   * _restoreInfoPanel   — replay the standard info layout on close
 *   * _editorSwitchTab    — top-level tab switcher
 *
 * Re-exported from editor.mjs.
 */

import { state } from "./state.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import {
  ADJUST_SLIDERS,
  ASPECT_RATIOS,
  AUTO_SECTIONS,
  EDITOR_DEFAULTS,
} from "./editor-constants.mjs";
import {
  _applyLivePreview,
  _edits,
  _editorDateValue,
  _editorPhotoHasFaces,
  _refreshAdjustTab,
} from "./editor.mjs";
import {
  _refreshStylesTab,
  _renderStylesGrid,
} from "./editor-styles.mjs";


export function _renderEditorPanel() {
  /** @type {any} */
  const win = window;
  const panel = document.querySelector(".lightbox-panel");
  if (!panel) return;

  // Keep the photo's identity (date + filename) at the top while editing —
  // the edit controls render BELOW it instead of replacing the whole panel
  // (user call: no context jump when entering edit mode). Values are
  // carried over from the live header; _restoreInfoPanel + openLightbox
  // rebuild the full info panel on exit.
  const dateText = document.getElementById("lb-date")?.textContent || "";
  const fileText = document.getElementById("lb-filename")?.textContent || "";

  panel.innerHTML = `
    <div class="lb-header">
      <div class="lb-meta-row">
        <span class="lb-date" id="lb-date">${esc(dateText)}</span>
        <span class="lb-quality" id="lb-quality"></span>
      </div>
      <div class="lb-filename" id="lb-filename" data-action="lbCopyFilePath">${esc(fileText)}</div>
    </div>
    <div class="editor-header">
      <span class="editor-title">Edit Photo</span>
      <button class="editor-close-btn" data-action="closeEditor" data-arg0="false" title="Cancel">&times;</button>
    </div>
    <div class="editor-tab-content editor-tab-scrollable" id="editor-adjust-tab">
      ${_renderSliders()}
    </div>
    <div class="editor-tab-content hidden" id="editor-styles-tab">
      ${_renderStylesGrid()}
    </div>
    <div class="editor-tab-content hidden" id="editor-crop-tab">
      ${_renderCropControls()}
    </div>
    <div class="editor-tab-content hidden" id="editor-remove-tab">
      ${win._renderRemoveControls?.() || ""}
    </div>
    <div class="editor-before-after">
      <button class="editor-before-btn" data-onmousedown="_showBefore" data-onmouseup="_hideBefore" data-onmouseleave="_hideBefore" data-ontouchstart="_showBefore" data-ontouchend="_hideBefore" title="Hold to see original photo">
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" width="12" height="12" style="vertical-align:-1px;margin-right:4px"><rect x="1" y="2" width="6" height="12" rx="1"/><rect x="9" y="2" width="6" height="12" rx="1"/></svg>Before / After
      </button>
    </div>
    <div class="editor-date-section">
      <div class="editor-crop-label">Date &amp; Time</div>
      <input type="datetime-local" class="editor-date-input" id="editor-date-input"
        value="${escapeAttr(_editorDateValue())}" data-onchange="_editorDateChanged">
    </div>
    <div class="editor-actions">
      <button class="editor-btn editor-btn-auto" data-action="_editorAutoEnhance">Auto</button>
      ${_editorPhotoHasFaces() ? '<button class="editor-btn editor-btn-redeye" data-action="_toggleRedeyeMode">Red-eye</button>' : ""}
      <button class="editor-btn editor-btn-reset" data-action="_editorReset">Reset</button>
    </div>
    <div class="editor-footer">
      <button class="editor-btn editor-btn-cancel" data-action="closeEditor" data-arg0="false">Cancel</button>
      <button class="editor-btn editor-btn-done" data-action="closeEditor" data-arg0="true">Done</button>
    </div>
  `;
}

/**
 * @param {{key: string, default: number}} s
 */
export function _isSliderModified(s) {
  const e = _edits();
  const val = e[s.key] ?? s.default;
  return Math.abs(val - s.default) > 0.005;
}

export function _renderSliders() {
  return _renderAdjustGrid();
}

export function _renderAdjustGrid() {
  /** @type {any} */
  const win = window;
  const e = _edits();
  let html = "";
  let currentSection = "";

  for (const s of ADJUST_SLIDERS) {
    if (s.section !== currentSection) {
      if (currentSection) {
        html += `</div>`;
        html += _renderFoldOut(currentSection);
      }
      if (currentSection === "Color") {
        const bwActive = (e.saturation ?? 1.0) === 0.0;
        html += `<div class="adjust-grid-bw">
          <button class="editor-bw-toggle${bwActive ? " active" : ""}" data-action="_toggleBWMode">
            ${bwActive ? "&#10003; B&amp;W On" : "B&amp;W Off"}
          </button>
        </div>`;
      }
      currentSection = s.section;
      const hasAuto = AUTO_SECTIONS.includes(s.section);
      html += `<div class="adjust-grid-section-header">
        <span class="adjust-grid-section-label">${s.section}</span>
        ${hasAuto ? `<button class="editor-section-auto-btn" data-action="_autoSection" data-arg0="${s.section}">AUTO</button>` : ""}
      </div>`;
      html += `<div class="adjust-grid-row">`;
    }
    const modified = _isSliderModified(s);
    const active = win._activeAdjustSlider === s.key;
    html += `<button class="adjust-grid-item${modified ? " modified" : ""}${active ? " active" : ""}" data-action="_openAdjustSlider" data-arg0="${escapeAttr(s.key)}" title="${escapeAttr(s.label)}">
      <span class="adjust-grid-icon">${s.icon}</span>
      <span class="adjust-grid-label">${s.label}</span>
    </button>`;
  }
  if (currentSection) {
    html += `</div>`;
    html += _renderFoldOut(currentSection);
  }
  return html;
}

/**
 * @param {string} section
 */
export function _renderFoldOut(section) {
  /** @type {any} */
  const win = window;
  if (!win._activeAdjustSlider) return "";
  const s = ADJUST_SLIDERS.find((sl) => sl.key === win._activeAdjustSlider);
  if (!s || s.section !== section) return "";
  const e = _edits();
  const val = e[s.key] ?? s.default;
  const pct = (((val - s.min) / (s.max - s.min)) * 100).toFixed(0);
  const defaultPct = (((s.default - s.min) / (s.max - s.min)) * 100).toFixed(1);
  return `<div class="adjust-fold-out">
    <div class="adjust-fold-header">
      <span class="adjust-fold-label">${s.label}</span>
      <span class="editor-slider-value" id="ev-${s.key}">${_formatSliderValue(s.key, val)}</span>
    </div>
    <div class="editor-slider-track-wrap">
      <input type="range" class="editor-slider" id="es-${s.key}"
        min="${s.min}" max="${s.max}" step="${s.step}" value="${val}"
        data-oninput="_editorSliderChange" data-slider-key="${s.key}"
        style="--pct: ${pct}%">
      <div class="editor-slider-midpoint" style="left: ${defaultPct}%"></div>
    </div>
  </div>`;
}

/**
 * @param {string} key
 */
export function _openAdjustSlider(key) {
  /** @type {any} */
  const win = window;
  win._activeAdjustSlider = win._activeAdjustSlider === key ? null : key;
  _refreshAdjustTab();
}

export function _closeAdjustSlider() {
  /** @type {any} */
  const win = window;
  win._activeAdjustSlider = null;
  _refreshAdjustTab();
}

/**
 * @param {string} key
 * @param {string | number} val
 */
export function _formatSliderValue(key, val) {
  const centeredKeys = [
    "warmth",
    "highlights",
    "shadows",
    "exposure",
    "brilliance",
    "black_point",
    "vibrance",
    "tint",
    "definition",
  ];
  if (centeredKeys.includes(key)) {
    const v = parseFloat(String(val));
    return (v >= 0 ? "+" : "") + v.toFixed(2);
  }
  return parseFloat(String(val)).toFixed(2);
}


export function _renderCropControls() {
  /** @type {any} */
  const win = window;
  const e = _edits();
  const straightenVal = e.straighten || 0;
  const perspVVal = e.perspective_v || 0;
  const perspHVal = e.perspective_h || 0;

  return `
    <div class="editor-crop-section">
      <div class="editor-crop-label">Aspect Ratio</div>
      <div class="editor-aspect-pills" id="editor-aspect-pills">
        ${ASPECT_RATIOS.map(
          (ar, i) => `
          <button class="editor-aspect-pill${i === 0 ? " active" : ""}"
            data-action="_setAspectRatioFromEl" data-arg0="${escapeAttr(ar.value === "original" ? "original" : ar.value)}">${ar.label}</button>
        `
        ).join("")}
        <button class="editor-aspect-pill" id="editor-custom-ratio-btn" data-action="_showCustomRatio">Custom</button>
      </div>
      <div id="editor-custom-ratio-row" class="editor-custom-ratio-row hidden">
        <input type="text" id="editor-custom-ratio-input" class="editor-custom-ratio-input"
          placeholder="e.g. 16:9"
          data-onkeydown="_kbEnterCustomRatio">
        <button class="editor-crop-btn" data-action="_applyCustomRatio">Apply</button>
      </div>
      <button class="editor-crop-btn" id="editor-crop-toggle" data-action="_toggleCropOverlay">
        ${win.editorCropActive ? "Apply Crop" : "Start Crop"}
      </button>
      ${e.crop_x != null ? '<button class="editor-crop-clear" data-action="_clearCrop">Clear Crop</button>' : ""}
    </div>
    <div class="editor-straighten-section">
      <div class="editor-section-header">
        <div class="editor-crop-label">Straighten</div>
        <button class="editor-section-auto-btn" data-action="_autoStraighten">AUTO</button>
      </div>
      <div class="editor-slider-group">
        <div class="editor-slider-header">
          <span class="editor-slider-label">Angle</span>
          <span class="editor-slider-value" id="ev-straighten">${straightenVal.toFixed(1)}°</span>
        </div>
        <div class="editor-slider-track-wrap">
          <input type="range" class="editor-slider" id="es-straighten"
            min="-45" max="45" step="0.1" value="${straightenVal}"
            data-oninput="_editorSliderChange" data-slider-key="straighten"
            style="--pct: ${(((straightenVal + 45) / 90) * 100).toFixed(0)}%">
          <div class="editor-slider-midpoint" style="left: 50%"></div>
        </div>
      </div>
      <div class="editor-crop-label" style="margin-top:8px">Perspective</div>
      <div class="editor-slider-group">
        <div class="editor-slider-header">
          <span class="editor-slider-label">Vertical</span>
          <span class="editor-slider-value" id="ev-perspective_v">${perspVVal >= 0 ? "+" : ""}${perspVVal.toFixed(2)}</span>
        </div>
        <div class="editor-slider-track-wrap">
          <input type="range" class="editor-slider" id="es-perspective_v"
            min="-1" max="1" step="0.01" value="${perspVVal}"
            data-oninput="_editorSliderChange" data-slider-key="perspective_v"
            style="--pct: ${(((perspVVal + 1) / 2) * 100).toFixed(0)}%">
          <div class="editor-slider-midpoint" style="left: 50%"></div>
        </div>
      </div>
      <div class="editor-slider-group">
        <div class="editor-slider-header">
          <span class="editor-slider-label">Horizontal</span>
          <span class="editor-slider-value" id="ev-perspective_h">${perspHVal >= 0 ? "+" : ""}${perspHVal.toFixed(2)}</span>
        </div>
        <div class="editor-slider-track-wrap">
          <input type="range" class="editor-slider" id="es-perspective_h"
            min="-1" max="1" step="0.01" value="${perspHVal}"
            data-oninput="_editorSliderChange" data-slider-key="perspective_h"
            style="--pct: ${(((perspHVal + 1) / 2) * 100).toFixed(0)}%">
          <div class="editor-slider-midpoint" style="left: 50%"></div>
        </div>
      </div>
    </div>
    <div class="editor-transform-section">
      <div class="editor-crop-label">Transform</div>
      <div class="editor-transform-row">
        <button class="editor-transform-btn" data-action="_editorRotate" data-arg0="-90" title="Rotate left">&#8634;</button>
        <button class="editor-transform-btn" data-action="_editorRotate" data-arg0="90" title="Rotate right">&#8635;</button>
        <button class="editor-transform-btn${e.flip_h ? " active" : ""}" data-action="_editorFlipH" title="Flip horizontal">&#8596;</button>
        <button class="editor-transform-btn${e.flip_v ? " active" : ""}" data-action="_editorFlipV" title="Flip vertical">&#8597;</button>
      </div>
    </div>
  `;
}

export function _restoreInfoPanel() {
  /** @type {any} */
  const win = window;
  const panel = document.querySelector(".lightbox-panel");
  if (!panel || win.lightboxIdx < 0) return;
  if (win._lbLeafletMap) {
    try {
      win._lbLeafletMap.remove();
    } catch (_) {
      /* leaflet teardown best-effort */
    }
    win._lbLeafletMap = null;
    win._lbMapMarker = null;
  }
  panel.innerHTML = `
    <div class="lb-header">
      <div class="lb-meta-row">
        <span class="lb-date" id="lb-date"></span>
        <span class="lb-quality" id="lb-quality"></span>
      </div>
      <div class="lb-filename" id="lb-filename" data-action="lbCopyFilePath"></div>
    </div>
    <div class="lb-panel-body" id="lb-panel-body">
      <div id="lb-actions"></div>
      <div class="lb-scores" id="lb-scores"></div>
      <div class="lb-faces hidden" id="lb-faces"></div>
      <div class="lb-pets hidden" id="lb-pets"></div>
      <div class="lb-tags hidden" id="lb-tags"></div>
      <div class="lb-similar hidden" id="lb-similar">
        <div class="lb-similar-label">Similar photos</div>
        <div class="lb-similar-grid" id="lb-similar-strip"></div>
      </div>
      <div class="lb-exif hidden" id="lb-exif"></div>
      <div class="lb-exif hidden" id="lb-video-info"></div>
      <div class="lb-map-container hidden" id="lb-map-container">
        <div class="lb-map-header" id="lb-map-header" data-action="toggleLightboxMap">
          <span class="lb-map-toggle" id="lb-map-toggle">Expand</span>
        </div>
        <div id="lb-map" class="lb-map"></div>
      </div>
      <div class="lb-trim hidden" id="lb-trim"></div>
    </div>
  `;
  win.openLightbox?.(win.lightboxIdx);
}

/**
 * @param {string} tab
 */
export function _editorSwitchTab(tab) {
  /** @type {any} */
  const win = window;
  document
    .querySelectorAll(".editor-tab")
    .forEach((t) =>
      t.classList.toggle("active", /** @type {HTMLElement} */ (t).dataset.tab === tab)
    );
  document.getElementById("editor-adjust-tab")?.classList.toggle("hidden", tab !== "adjust");
  document.getElementById("editor-styles-tab")?.classList.toggle("hidden", tab !== "styles");
  document.getElementById("editor-crop-tab")?.classList.toggle("hidden", tab !== "crop");
  document.getElementById("editor-remove-tab")?.classList.toggle("hidden", tab !== "remove");
  document.getElementById("lightbox")?.classList.toggle("editor-tab-crop", tab === "crop");
  if (tab === "crop" && !win.editorCropActive) {
    const e = _edits();
    win._cropSavedPerspective = {
      v: e.perspective_v || 0,
      h: e.perspective_h || 0,
    };
    e.perspective_v = 0;
    e.perspective_h = 0;
    _applyLivePreview();
    win._toggleCropOverlay?.();
  } else if (tab !== "crop" && win.editorCropActive) {
    win._applyCropFromOverlay?.();
    win._removeCropOverlay?.();
    win.editorCropActive = false;
    if (win._cropSavedPerspective) {
      const e = _edits();
      e.perspective_v = win._cropSavedPerspective.v;
      e.perspective_h = win._cropSavedPerspective.h;
      win._cropSavedPerspective = null;
      _applyLivePreview();
    }
  }
  if (win._redeyeMode) {
    win._redeyeMode = false;
    win._removeRedeyeOverlay?.();
  }
  if (tab === "remove") {
    win._showInpaintOverlay?.();
  } else if (win._inpaintMode) {
    win._removeInpaintOverlay?.();
    win._inpaintMode = false;
  }
}
