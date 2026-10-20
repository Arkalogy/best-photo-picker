// @ts-check
// Shared setup of the editor suite (split for the 500-LOC cap).
import { vi } from "vitest";

/** Per-test DOM + window state (the suite's former beforeEach). */
export function setup() {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="lightbox">
      <div class="lb-img-wrapper">
        <img id="lb-img" />
      </div>
      <div class="lightbox-panel"></div>
    </div>
    <input id="editor-zoom-slider" type="range" />
    <span id="editor-zoom-pct"></span>
    <div id="editor-adjust-tab" class="hidden"></div>
    <div id="editor-styles-tab" class="hidden"></div>
    <div id="editor-crop-tab" class="hidden"></div>
    <div id="editor-remove-tab" class="hidden"></div>
    <div class="editor-tab" data-tab="adjust"></div>
    <div class="editor-tab" data-tab="styles"></div>
    <div class="editor-tab" data-tab="crop"></div>
    <div class="editor-tab" data-tab="remove"></div>
  `;
  /** @type {any} */ (window).editorActive = false;
  /** @type {any} */ (window).editorEdits = {};
  /** @type {any} */ (window).editorOriginalEdits = null;
  /** @type {any} */ (window).editorCropActive = false;
  /** @type {any} */ (window)._redeyeMode = false;
  /** @type {any} */ (window)._inpaintMode = false;
  /** @type {any} */ (window)._editorRevertPending = false;
  /** @type {any} */ (window)._cropSavedPerspective = null;
  /** @type {any} */ (window)._activeAdjustSlider = null;
  /** @type {any} */ (window)._editorAspectRatio = null;
  /** @type {any} */ (window).lbZoom = 1;
  /** @type {any} */ (window).LB_ZOOM_MIN = 0.1;
  /** @type {any} */ (window).LB_ZOOM_MAX = 10;
  /** @type {any} */ (window).lightboxIdx = 0;
  /** @type {any} */ (window).currentGridItems = [
    { id: 5, filepath: "/a.jpg", thumb_hash: "h1", date: "2024-01-01T12:00:00", face_count: 0 },
  ];
  /** @type {any} */ (window)._lbApplyTransform = vi.fn();
  /** @type {any} */ (window)._lbShowZoomIndicator = vi.fn();
  /** @type {any} */ (window).lbResetZoom = vi.fn();
  /** @type {any} */ (window).updateLightboxFaces = vi.fn();
  /** @type {any} */ (window).updateLightboxActions = vi.fn();
  /** @type {any} */ (window).openLightbox = vi.fn();
  /** @type {any} */ (window)._setAspectRatio = vi.fn();
  /** @type {any} */ (window)._toggleCropOverlay = vi.fn();
  /** @type {any} */ (window)._removeCropOverlay = vi.fn();
  /** @type {any} */ (window)._applyCropFromOverlay = vi.fn();
  /** @type {any} */ (window)._removeRedeyeOverlay = vi.fn();
  /** @type {any} */ (window)._showInpaintOverlay = vi.fn();
  /** @type {any} */ (window)._removeInpaintOverlay = vi.fn();
  /** @type {any} */ (window)._renderRemoveControls = vi.fn(() => "");
}

/** Undo setup() (the suite's former afterEach). */
export function teardown() {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "editorActive",
    "editorEdits",
    "editorOriginalEdits",
    "editorCropActive",
    "_redeyeMode",
    "_inpaintMode",
    "_editorRevertPending",
    "_cropSavedPerspective",
    "_activeAdjustSlider",
    "_editorAspectRatio",
    "lbZoom",
    "LB_ZOOM_MIN",
    "LB_ZOOM_MAX",
    "lightboxIdx",
    "currentGridItems",
    "_lbApplyTransform",
    "_lbShowZoomIndicator",
    "lbResetZoom",
    "updateLightboxFaces",
    "updateLightboxActions",
    "openLightbox",
    "_setAspectRatio",
    "_toggleCropOverlay",
    "_removeCropOverlay",
    "_applyCropFromOverlay",
    "_removeRedeyeOverlay",
    "_showInpaintOverlay",
    "_removeInpaintOverlay",
    "_renderRemoveControls",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
}

/**
 * @param {object} body
 */
export function jsonResp(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}
